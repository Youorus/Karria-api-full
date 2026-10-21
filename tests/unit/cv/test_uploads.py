"""Les règles sur les fichiers reçus : types, tailles, pages, durée."""

from __future__ import annotations

import pytest

from karria.modules.cv.domain import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_PAGES,
    AudioInfo,
    DocumentInfo,
    UnsupportedUploadError,
    check_audio,
    check_audio_size,
    check_audio_type,
    check_document,
    check_document_size,
    check_document_type,
    plain_content_type,
)

pytestmark = pytest.mark.unit


def reason_of(excinfo: pytest.ExceptionInfo[UnsupportedUploadError]) -> str:
    return str(excinfo.value.details["reason"])


class TestTypeAnnonce:
    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ("audio/m4a; codecs=mp4a", "audio/m4a"),
            (" Application/PDF ", "application/pdf"),
            (None, ""),
        ],
    )
    def test_le_type_est_reduit_a_sa_forme_simple(
        self, declared: str | None, expected: str
    ) -> None:
        assert plain_content_type(declared) == expected


class TestDocuments:
    def test_un_type_hors_liste_est_refuse_avant_lecture(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_document_type("application/zip")
        assert reason_of(excinfo) == "document_type_unsupported"
        assert excinfo.value.details["field"] == "file"

    def test_les_types_du_contrat_passent(self) -> None:
        for content_type in ("application/pdf", "image/jpeg", "text/plain"):
            check_document_type(content_type)

    def test_un_fichier_vide_ou_trop_gros_est_refuse(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_document_size(0)
        assert reason_of(excinfo) == "document_empty"
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_document_size(MAX_DOCUMENT_BYTES + 1)
        assert reason_of(excinfo) == "document_too_large"
        check_document_size(MAX_DOCUMENT_BYTES)

    def test_trop_de_pages_est_refuse(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_document(DocumentInfo(b"x", "application/pdf", pages=MAX_DOCUMENT_PAGES + 1))
        assert reason_of(excinfo) == "document_too_many_pages"

    def test_un_document_sans_rien_de_lisible_est_refuse(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_document(DocumentInfo(b"x", "application/pdf", pages=0))
        assert reason_of(excinfo) == "document_empty"
        with pytest.raises(UnsupportedUploadError):
            check_document(DocumentInfo(b"   \n", "text/plain"))

    def test_un_document_normal_passe(self) -> None:
        check_document(DocumentInfo(b"CV", "application/pdf", pages=2))
        check_document(DocumentInfo(b"CV", "text/plain"))


class TestAudio:
    def test_un_format_inconnu_est_refuse(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_audio_type("video/mp4")
        assert reason_of(excinfo) == "audio_type_unsupported"
        assert excinfo.value.details["field"] == "audio_file"

    def test_trop_court_ou_trop_gros_est_refuse(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_audio_size(10)
        assert reason_of(excinfo) == "audio_empty"
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_audio_size(MAX_AUDIO_BYTES + 1)
        assert reason_of(excinfo) == "audio_too_large"

    def test_trop_long_est_refuse_et_la_duree_inconnue_passe(self) -> None:
        with pytest.raises(UnsupportedUploadError) as excinfo:
            check_audio(AudioInfo("audio/mp4", duration_seconds=MAX_AUDIO_SECONDS + 1))
        assert reason_of(excinfo) == "audio_too_long"
        check_audio(AudioInfo("audio/mp4", duration_seconds=None))
        check_audio(AudioInfo("audio/mp4", duration_seconds=12.5))
