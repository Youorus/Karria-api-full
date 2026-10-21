"""Les erreurs du module : code HTTP par la table du socle, raison fine dans `details`."""

from __future__ import annotations

import pytest

from karria.modules.cv.domain import (
    ChapterSubmissionError,
    DraftIncompleteError,
    DraftNotFoundError,
    ModelOutputInvalidError,
    ModelUnavailableError,
    PhotoEnhanceRefusedError,
    PhotoEnhanceUnavailableError,
    UnsupportedUploadError,
)
from karria.shared.errors import STATUS_BY_ERROR_CODE

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (DraftNotFoundError(), 404, "draft_not_found"),
        (DraftIncompleteError(["photo"]), 409, "draft_incomplete"),
        (ChapterSubmissionError("photo", "m", reason="chapter_required"), 422, "chapter_required"),
        (UnsupportedUploadError("m", reason="audio_empty", field="audio_file"), 422, "audio_empty"),
        (PhotoEnhanceRefusedError(), 422, "photo_enhance_refused"),
        (PhotoEnhanceUnavailableError(), 503, "photo_enhance_unavailable"),
        (ModelUnavailableError(), 503, "ai_unavailable"),
        (ModelOutputInvalidError(), 503, "ai_output_invalid"),
    ],
)
def test_chaque_erreur_a_son_statut_et_sa_raison(
    error: Exception, status: int, reason: str
) -> None:
    code = error.code  # type: ignore[attr-defined]
    details = error.details  # type: ignore[attr-defined]

    assert STATUS_BY_ERROR_CODE[code] == status
    assert details["reason"] == reason
    assert "detail" not in details
    assert "finish_reason" not in details


def test_le_detail_du_fournisseur_est_conserve_quand_il_existe() -> None:
    assert PhotoEnhanceRefusedError("SPII").details["finish_reason"] == "SPII"
    assert ModelUnavailableError("HTTP 429").details["detail"] == "HTTP 429"
    assert DraftIncompleteError(["a", "b"]).details["chapters"] == ["a", "b"]
