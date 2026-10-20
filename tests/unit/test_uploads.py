"""`prepare_upload` : ce qu'une route fait d'un fichier reçu (ADR-0009)."""

from __future__ import annotations

import pytest

from doubles import sample_image
from karria.shared.storage import Bucket, PayloadRejectedError, prepare_upload

pytestmark = pytest.mark.unit


class TestImages:
    def test_une_image_est_normalisee_selon_le_bucket(self) -> None:
        prepared = prepare_upload(Bucket.MEDIA, sample_image(4_000, 3_000, fmt="PNG"), "image/png")
        assert prepared.content_type == "image/jpeg"
        assert prepared.extension == "jpg"
        assert prepared.is_image
        assert len(prepared.payload) <= 5 * 1024 * 1024

    def test_le_type_annonce_ne_compte_pas_pour_une_image(self) -> None:
        """Un HEIC étiqueté JPEG par le client est reconnu à ses octets."""
        prepared = prepare_upload(Bucket.MEDIA, sample_image(300, 200, fmt="PNG"), "image/jpeg")
        assert prepared.content_type == "image/jpeg"

    def test_une_image_conforme_passe_intacte(self) -> None:
        photo = sample_image(640, 480)
        prepared = prepare_upload(Bucket.MEDIA, photo, "image/jpeg")
        assert prepared.payload is photo

    def test_un_fichier_qui_se_dit_image_sans_en_etre_une_est_refuse(self) -> None:
        with pytest.raises(PayloadRejectedError, match="pas une image"):
            prepare_upload(Bucket.MEDIA, b"\xff\xd8\xff\xe0 octets d'image", "image/jpeg")

    def test_une_image_trop_lourde_pour_le_bucket_est_reduite_et_non_refusee(self) -> None:
        """Le cas qui motivait tout : 6 Mo de photo sur un bucket à 5 Mo."""
        photo = sample_image(5_000, 4_000)
        assert len(photo) > 5 * 1024 * 1024
        prepared = prepare_upload(Bucket.MEDIA, photo, "image/jpeg")
        assert len(prepared.payload) <= 5 * 1024 * 1024


class TestAutresFichiers:
    def test_un_pdf_passe_intact_avec_son_type(self) -> None:
        pdf = b"%PDF-1.4 un billet"
        prepared = prepare_upload(Bucket.DOCUMENTS, pdf, "application/pdf")
        assert prepared.payload is pdf
        assert prepared.content_type == "application/pdf"
        assert prepared.extension == "pdf"
        assert not prepared.is_image

    def test_une_note_vocale_passe_intacte(self) -> None:
        """Le fichier audio d'une transcription : pas une image, pas retouché."""
        audio = b"\x00\x00\x00\x18ftypM4A des octets audio"
        prepared = prepare_upload(Bucket.TEMP, audio, "audio/m4a; codecs=mp4a")
        assert prepared.payload is audio
        assert prepared.content_type == "audio/m4a"

    def test_un_bucket_sans_politique_corrige_seulement_le_type(self) -> None:
        png = sample_image(300, 200, fmt="PNG")
        prepared = prepare_upload(Bucket.TEMP, png, "image/jpeg")
        assert prepared.payload is png
        assert prepared.content_type == "image/png"
