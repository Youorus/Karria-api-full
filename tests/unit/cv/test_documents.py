"""L'inspecteur de fichiers : PDF, DOCX, images, M4A — lus pour ce qu'ils sont."""

from __future__ import annotations

import struct
import zipfile
from io import BytesIO

import pytest
from pypdf import PdfWriter

from doubles import sample_image
from karria.modules.cv.domain import DOCX_CONTENT_TYPE, UnsupportedUploadError
from karria.modules.cv.infrastructure import FileInspector
from karria.modules.cv.infrastructure.documents import MIN_TEXT_LAYER_CHARS

pytestmark = pytest.mark.unit

inspector = FileInspector()


def pdf_with_text(lines: list[str], *, pages: int = 1) -> bytes:
    """Un PDF minimal, écrit à la main, dont la couche texte est lisible par pypdf."""
    content = (
        "BT /F1 11 Tf 40 760 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids ["
        + " ".join(f"{3 + i} 0 R" for i in range(pages))
        + f"] /Count {pages} >>",
    ]
    objects.extend(
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        f"/Resources << /Font << /F1 {3 + pages} 0 R >> >> /Contents {4 + pages} 0 R >>"
        for _ in range(pages)
    )
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def docx_with(body_xml: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body_xml}</w:body></w:document>",
        )
    return buffer.getvalue()


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mvhd(version: int, timescale: int, duration: int) -> bytes:
    if version == 0:
        body = bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, timescale, duration)
    else:
        body = bytes([1, 0, 0, 0]) + struct.pack(">QQIQ", 0, 0, timescale, duration)
    return box(b"mvhd", body + b"\x00" * 80)


def m4a(*moov_children: bytes) -> bytes:
    return box(b"ftyp", b"M4A \x00\x00\x00\x00") + box(b"moov", b"".join(moov_children))


class TestPdf:
    def test_une_couche_texte_suffisante_part_en_texte_avec_son_nombre_de_pages(self) -> None:
        lines = [f"Ligne {i} de contenu du CV pour la couche texte" for i in range(8)]

        info = inspector.document(pdf_with_text(lines, pages=2), "application/pdf")

        assert info.content_type == "text/plain"
        assert info.pages == 2
        assert len(info.payload) >= MIN_TEXT_LAYER_CHARS
        assert b"Ligne 3 de contenu" in info.payload

    def test_un_pdf_sans_couche_texte_part_tel_quel(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_blank_page(width=595, height=842)
        buffer = BytesIO()
        writer.write(buffer)
        scanned = buffer.getvalue()

        info = inspector.document(scanned, "application/pdf")

        assert info.payload is scanned
        assert info.content_type == "application/pdf"
        assert info.pages == 2

    def test_un_pdf_protege_sans_mot_de_passe_se_lit(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.encrypt(user_password="", owner_password="proprio", algorithm="RC4-128")
        buffer = BytesIO()
        writer.write(buffer)

        assert inspector.document(buffer.getvalue(), "application/pdf").pages == 1

    def test_un_pdf_illisible_est_refuse(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            inspector.document(b"%PDF-1.4 tronque", "application/pdf")

        assert excinfo.value.details["reason"] == "document_unreadable"


class TestDocx:
    def test_le_texte_sort_paragraphe_par_paragraphe(self) -> None:
        payload = docx_with(
            "<w:p><w:r><w:t>Awa</w:t><w:tab/><w:t>Diallo</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Vendeuse</w:t><w:br/><w:t>Zara</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>   </w:t></w:r></w:p>"
        )

        info = inspector.document(payload, DOCX_CONTENT_TYPE)

        assert info.content_type == "text/plain"
        assert info.pages is None
        assert info.payload.decode() == "Awa\tDiallo\nVendeuse\nZara"

    @pytest.mark.parametrize(
        "payload",
        [
            b"pas un zip",
            zipfile.ZipFile(BytesIO(), "w").fp and b"PK\x05\x06" + b"\x00" * 18,
            docx_with("<w:p>non fermé"),
        ],
    )
    def test_un_docx_illisible_est_refuse(self, payload: bytes) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            inspector.document(payload, DOCX_CONTENT_TYPE)

        assert excinfo.value.details["reason"] == "document_unreadable"

    def test_un_document_xml_demesure_est_refuse_avant_decompression(self) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", " " * (20 * 1024 * 1024 + 1))

        with pytest.raises(UnsupportedUploadError) as excinfo:
            inspector.document(buffer.getvalue(), DOCX_CONTENT_TYPE)

        assert excinfo.value.details["reason"] == "document_too_large"


class TestImagesEtTexte:
    def test_une_image_est_reconnue_a_ses_octets(self) -> None:
        info = inspector.document(sample_image(fmt="PNG"), "image/jpeg")

        assert info.content_type == "image/png"
        assert info.pages == 1

    def test_un_faux_fichier_image_est_refuse(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            inspector.document(b"\xff\xd8 pas une image", "image/jpeg")

        assert excinfo.value.details["reason"] == "document_unreadable"

    def test_le_texte_brut_passe_tel_quel(self) -> None:
        info = inspector.document(b"Awa Diallo", "text/plain")

        assert info == info.__class__(payload=b"Awa Diallo", content_type="text/plain")


class TestAudio:
    def test_la_duree_dun_m4a_se_lit_dans_mvhd_v0(self) -> None:
        info = inspector.audio(m4a(box(b"udta", b""), mvhd(0, 1000, 12_500)), "audio/m4a")

        assert info.duration_seconds == 12.5
        assert info.content_type == "audio/m4a"

    def test_la_duree_se_lit_aussi_dans_mvhd_v1(self) -> None:
        assert inspector.audio(m4a(mvhd(1, 48_000, 96_000)), "audio/mp4").duration_seconds == 2.0

    @pytest.mark.parametrize(
        "payload",
        [
            box(b"ftyp", b"M4A "),
            m4a(box(b"udta", b"")),
            m4a(mvhd(0, 0, 10)),
            m4a(box(b"mvhd", b"\x00")),
            struct.pack(">I", 3) + b"ftyp",
            struct.pack(">I", 1) + b"moov" + b"\x00" * 4,
        ],
    )
    def test_sans_mvhd_exploitable_la_duree_reste_inconnue(self, payload: bytes) -> None:
        assert inspector.audio(payload, "audio/m4a").duration_seconds is None

    def test_les_boites_de_taille_etendue_ou_ouverte_sont_parcourues(self) -> None:
        large = struct.pack(">I", 1) + b"free" + struct.pack(">Q", 16)
        moov_open = struct.pack(">I", 0) + b"moov" + mvhd(0, 10, 50)

        assert inspector.audio(large + moov_open, "audio/m4a").duration_seconds == 5.0

    def test_un_autre_format_na_pas_de_duree_connue(self) -> None:
        assert inspector.audio(b"RIFF....WAVE", "audio/wav").duration_seconds is None
