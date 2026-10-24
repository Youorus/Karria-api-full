"""Lecture « comme un ATS » : texte pdfminer, types et incorporation des polices, images, DOCX."""

from __future__ import annotations

import ctypes
import io
import zipfile

import pypdfium2.raw as pdfium_c
import pytest

from documents.infrastructure.fakes import PdfBuilder, build_font, png
from karria.modules.documents.infrastructure import ats_reader
from karria.modules.documents.infrastructure.ats_reader import read_docx, read_pdf

pytestmark = pytest.mark.unit


def raw_pdf(objects: list[str], content: str, xobject: str = "") -> bytes:
    """Un PDF écrit à la main : `objects` (polices) sont les objets 4, 5… ; `xobject`, un
    formulaire posé sous `/X0`, est l'avant-dernier."""
    fonts = " ".join(f"/F{i} {i + 4} 0 R" for i in range(len(objects)))
    if xobject:
        objects = [*objects, xobject]
    xobjects = f"/X0 {len(objects) + 3} 0 R" if xobject else ""
    page_resources = f"<< /Font << {fonts} >> /XObject << {xobjects} >> >>"
    bodies = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources {page_resources}"
        f" /Contents {len(objects) + 4} 0 R >>",
        *objects,
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(bodies, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


class TestLecturePdf:
    def test_texte_police_standard_non_incorporee_et_image(self) -> None:
        b = PdfBuilder(80, 60)
        b.text("Bonjour ATS", 5, 10, size=10)
        b.image(png(), 40, 20, 20, 15)
        doc = read_pdf(b.to_bytes())
        assert "Bonjour ATS" in doc.text
        assert doc.format == "pdf"
        assert doc.pages == 1
        assert doc.images == 1
        assert doc.font_types == {"Type1": 1}
        assert doc.embedded is False
        assert doc.tables == 0

    def test_police_truetype_incorporee(self) -> None:
        b = PdfBuilder(80, 60, embedded={"Testa": build_font()})
        b.text("Python", 5, 10, font="Testa")
        doc = read_pdf(b.to_bytes())
        assert doc.font_types == {"TrueType": 1}
        assert doc.embedded is True

    def test_police_cid_lue_par_sa_descendante(self) -> None:
        data = build_font()
        b = PdfBuilder(80, 60)
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        handle = pdfium_c.FPDFText_LoadFont(
            b._doc.raw, buf, len(data), pdfium_c.FPDF_FONT_TRUETYPE, 1
        )
        b._fonts["cid"] = (handle, buf)
        b.text("Python", 5, 10, font="cid")
        doc = read_pdf(b.to_bytes())
        assert doc.font_types == {"Type0": 1}
        assert doc.embedded is True

    def test_type3_et_police_sans_sous_type(self) -> None:
        type3 = (
            "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] /FontMatrix [0.001 0 0 0.001 0 0]"
            " /CharProcs << >> /Encoding << /Type /Encoding /Differences [65 /A] >>"
            " /FirstChar 65 /LastChar 65 /Widths [1000] >>"
        )
        nameless = "<< /Type /Font /BaseFont /Mystere >>"
        doc = read_pdf(raw_pdf([type3, nameless], "BT /F0 12 Tf 10 10 Td (A) Tj ET"))
        assert doc.font_types == {"Type3": 1, "None": 1}
        assert doc.embedded is False  # la police sans descripteur n'est pas incorporée

    def test_type3_seul_reste_incorpore(self) -> None:
        type3 = (
            "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] /FontMatrix [0.001 0 0 0.001 0 0]"
            " /CharProcs << >> /FirstChar 65 /LastChar 65 /Widths [1000] >>"
        )
        doc = read_pdf(raw_pdf([type3], "BT /F0 12 Tf 10 10 Td (A) Tj ET"))
        assert doc.embedded is True

    def test_type0_sans_descendante_et_formulaire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pdfminer lui-même échoue à extraire le texte d'un Type0 sans descendante : on ne
        teste ici que l'inventaire des ressources."""
        monkeypatch.setattr(ats_reader, "extract_text", lambda _: "")
        type0 = "<< /Type /Font /Subtype /Type0 /BaseFont /X /Encoding /Identity-H >>"
        form = "<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Length 0 >>\nstream\n\nendstream"
        doc = read_pdf(raw_pdf([type0], "", form))
        assert doc.images == 0
        assert doc.font_types == {"Type0": 1}
        assert doc.embedded is False


def _docx(document_xml: str, media: tuple[str, ...] = ()) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", document_xml)
        for name in media:
            z.writestr(name, b"x")
    return out.getvalue()


class TestLectureDocx:
    def test_paragraphes_tabulations_tableaux_images(self) -> None:
        xml = (
            "<w:document><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Jean</w:t></w:r>"
            '<w:r><w:tab/></w:r><w:r><w:t xml:space="preserve">Dupont &amp; fils</w:t></w:r></w:p>'
            "</w:tc></w:tr></w:tbl><w:p></w:p><w:p><w:r><w:t>Python</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        doc = read_docx(
            _docx(xml, ("word/media/image1.png", "word/fonts/font1.odttf", "word/media/f.odttf"))
        )
        assert doc.text == "Jean\tDupont & fils\nPython"
        assert doc.format == "docx"
        assert doc.pages is None
        assert doc.embedded is True
        assert doc.images == 1
        assert doc.tables == 1
