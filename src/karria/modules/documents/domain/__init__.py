"""Domaine du module `documents` : modèles, versions, exports, porte de qualité, score ATS, polices."""

from karria.modules.documents.domain.ats import AtsReport, Check, ExtractedDocument, analyse
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
from karria.modules.documents.domain.gate import Fidelity, GateDecision, evaluate
from karria.modules.documents.domain.templates import (
    ExplanationStatus,
    Fit,
    GeneratedCv,
    Ownership,
    Selection,
    Template,
    TemplateVersion,
    VersionStatus,
)

__all__ = [
    "AtsReport",
    "Check",
    "CvMissingError",
    "ExplanationStatus",
    "ExtractedDocument",
    "Fidelity",
    "Fit",
    "GateDecision",
    "GeneratedCv",
    "GeneratedCvNotFoundError",
    "InvalidFormatError",
    "Ownership",
    "RenderingUnavailableError",
    "Selection",
    "TailoringNotAvailableError",
    "Template",
    "TemplateIngestionError",
    "TemplateLockedError",
    "TemplateNotFoundError",
    "TemplateNotFreeError",
    "TemplateVersion",
    "VersionStatus",
    "analyse",
    "evaluate",
]
