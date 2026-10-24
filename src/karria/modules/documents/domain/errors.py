"""Erreurs du module `documents`.

Chaque erreur porte dans `details.reason` la raison fine que l'application
branche (`template_not_found`, `cv_missing`…, voir
`karria_app/docs/contrat-api-modeles.md`) et s'appuie sur un `ErrorCode`
existant pour le statut HTTP.
"""

from __future__ import annotations

from typing import Any, ClassVar

# Le sous-module, et non le paquet : son `__init__` tire Starlette.
from karria.shared.errors.exceptions import (
    AuthorizationError,
    BusinessRuleViolationError,
    DomainError,
    ErrorCode,
    InvalidInputError,
)


class TemplateNotFoundError(DomainError):
    """Le modèle n'existe pas, ou n'est pas publié."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__("Ce modèle de CV n'existe pas.", details={"reason": "template_not_found"})


class GeneratedCvNotFoundError(DomainError):
    """L'export n'existe pas, ou n'appartient pas à l'appelant."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__(
            "Ce CV exporté n'existe pas.", details={"reason": "generated_cv_not_found"}
        )


class CvMissingError(BusinessRuleViolationError):
    """La personne n'a pas encore de CV finalisé : rien à rendre."""

    def __init__(self) -> None:
        super().__init__(
            "Crée d'abord ton CV pour l'afficher dans un modèle.", details={"reason": "cv_missing"}
        )


class TemplateLockedError(AuthorizationError):
    """Un modèle Pro que la personne ne possède pas."""

    def __init__(self, reason: str = "premium_not_owned") -> None:
        super().__init__(
            "Ce modèle fait partie de Karria Pro.",
            details={"reason": "template_locked", "lock": reason},
        )


class TemplateNotFreeError(InvalidInputError):
    """Un modèle Pro ne s'ajoute pas gratuitement : il s'achète."""

    def __init__(self) -> None:
        super().__init__(
            "Ce modèle s'obtient avec Karria Pro.", details={"reason": "template_not_free"}
        )


class TailoringNotAvailableError(InvalidInputError):
    """L'adaptation à une offre (`offer_id`) n'est pas encore servie (ADR-0020 § 7)."""

    def __init__(self) -> None:
        super().__init__(
            "L'adaptation du CV à une offre arrive bientôt.",
            details={"reason": "tailoring_not_available"},
        )


class InvalidFormatError(InvalidInputError):
    """Un format d'export inconnu."""

    def __init__(self, formats: list[str]) -> None:
        super().__init__(
            "Format d'export inconnu : pdf ou docx.",
            details={"reason": "invalid_format", "formats": formats},
        )


class RenderingUnavailableError(DomainError):
    """Le moteur de rendu (Gotenberg) ne répond pas ; la demande se retente."""

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_FAILURE

    def __init__(self, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "Le rendu du CV est momentanément indisponible, réessaie dans un instant.",
            details={"reason": "rendering_unavailable", **(details or {})},
        )


class TemplateIngestionError(InvalidInputError):
    """Le PDF déposé ne donne pas de modèle exploitable (aucune rubrique, page vide…)."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            "Ce PDF ne donne pas de modèle exploitable.",
            details={"reason": "template_unreadable", "detail": detail},
        )
