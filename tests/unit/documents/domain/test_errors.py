"""Erreurs du module `documents` : la raison fine et le code HTTP de chacune."""

from __future__ import annotations

import pytest

from karria.modules.documents.domain.errors import (
    CvMissingError,
    GeneratedCvNotFoundError,
    InvalidFormatError,
    RenderingUnavailableError,
    TailoringNotAvailableError,
    TemplateIngestionError,
    TemplateLockedError,
    TemplateNotFoundError,
    TemplateNotFreeError,
)
from karria.shared.errors.exceptions import DomainError, ErrorCode

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error", "code", "reason"),
    [
        (TemplateNotFoundError(), ErrorCode.NOT_FOUND, "template_not_found"),
        (GeneratedCvNotFoundError(), ErrorCode.NOT_FOUND, "generated_cv_not_found"),
        (CvMissingError(), ErrorCode.RULE_VIOLATION, "cv_missing"),
        (TemplateLockedError(), ErrorCode.FORBIDDEN, "template_locked"),
        (TemplateNotFreeError(), ErrorCode.INVALID_INPUT, "template_not_free"),
        (TailoringNotAvailableError(), ErrorCode.INVALID_INPUT, "tailoring_not_available"),
        (InvalidFormatError(["odt"]), ErrorCode.INVALID_INPUT, "invalid_format"),
        (RenderingUnavailableError(), ErrorCode.DEPENDENCY_FAILURE, "rendering_unavailable"),
        (TemplateIngestionError("page vide"), ErrorCode.INVALID_INPUT, "template_unreadable"),
    ],
)
def test_chaque_erreur_porte_sa_raison_et_son_code(
    error: DomainError, code: ErrorCode, reason: str
) -> None:
    assert isinstance(error, DomainError)
    assert error.code is code
    assert error.details["reason"] == reason
    assert error.message


def test_le_verrou_par_defaut_est_un_modele_pro_non_possede() -> None:
    assert TemplateLockedError().details["lock"] == "premium_not_owned"


def test_le_verrou_garde_la_raison_donnee() -> None:
    assert TemplateLockedError("expired").details["lock"] == "expired"


def test_le_format_invalide_liste_les_formats_refuses() -> None:
    assert InvalidFormatError(["odt", "rtf"]).details["formats"] == ["odt", "rtf"]


def test_le_rendu_indisponible_ajoute_les_details_fournis() -> None:
    error = RenderingUnavailableError({"status": 503})
    assert error.details == {"reason": "rendering_unavailable", "status": 503}


def test_le_rendu_indisponible_sans_details_ne_porte_que_la_raison() -> None:
    assert RenderingUnavailableError().details == {"reason": "rendering_unavailable"}


def test_l_echec_d_ingestion_garde_le_detail() -> None:
    assert TemplateIngestionError("aucune rubrique").details["detail"] == "aucune rubrique"
