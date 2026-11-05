"""Erreurs du module `applications`.

Chaque erreur porte dans `details.reason` la raison fine que l'application
branche (`cv_missing`, `offer_not_found`, `adaptation_in_progress`…), et
s'appuie sur un `ErrorCode` existant pour le statut HTTP. Aucun code HTTP
n'est inventé ici : la traduction reste dans `shared/errors`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar
from uuid import UUID

# Le sous-module, et non le paquet `karria.shared.errors` : son `__init__`
# tire les gestionnaires HTTP, donc Starlette, que le domaine ne doit pas voir.
from karria.shared.errors.exceptions import (
    AuthorizationError,
    BusinessRuleViolationError,
    DependencyUnavailableError,
    DomainError,
    ErrorCode,
    InvalidInputError,
)


class DossierNotFoundError(DomainError):
    """Le dossier n'existe pas, ou n'appartient pas à l'appelant — même réponse."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, dossier_id: UUID) -> None:
        super().__init__(
            "Dossier de candidature introuvable.",
            details={"reason": "dossier_not_found", "dossier_id": str(dossier_id)},
        )


class OfferNotFoundError(DomainError):
    """L'offre visée n'est pas (ou plus) dans l'index."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, offer_id: UUID) -> None:
        super().__init__(
            "Offre introuvable.", details={"reason": "offer_not_found", "offer_id": str(offer_id)}
        )


class BaseCvMissingError(BusinessRuleViolationError):
    """Pas de CV de base : rien à adapter. L'application envoie créer ou importer le CV."""

    def __init__(self) -> None:
        super().__init__(
            "Crée ou importe ton CV avant de l'adapter à une offre.",
            details={"reason": "cv_missing"},
        )


class TemplateLockedError(AuthorizationError):
    """Le modèle choisi est Premium et la personne ne le possède pas."""

    def __init__(self, template_id: UUID) -> None:
        super().__init__(
            "Ce modèle n'est pas disponible pour ton compte.",
            details={"reason": "template_locked", "template_id": str(template_id)},
        )


class AdaptationInProgressError(BusinessRuleViolationError):
    """Karria adapte encore le CV : on ne modifie pas un dossier sous ses mains."""

    def __init__(self, dossier_id: UUID) -> None:
        super().__init__(
            "Karria adapte encore ton CV, réessaie dans quelques secondes.",
            details={"reason": "adaptation_in_progress", "dossier_id": str(dossier_id)},
        )


class AdaptationNotFailedError(BusinessRuleViolationError):
    """On ne relance l'adaptation que si elle a échoué."""

    def __init__(self, dossier_id: UUID, state: str) -> None:
        super().__init__(
            "L'adaptation n'a pas échoué : rien à relancer.",
            details={
                "reason": "adaptation_not_failed",
                "dossier_id": str(dossier_id),
                "adaptation": state,
            },
        )


class PropositionNotFoundError(DomainError):
    """Aucune proposition de cet identifiant dans ce dossier."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, proposition_id: UUID) -> None:
        super().__init__(
            "Proposition introuvable.",
            details={"reason": "proposition_not_found", "proposition_id": str(proposition_id)},
        )


class PropositionPathError(BusinessRuleViolationError):
    """Le chemin de la proposition ne désigne plus rien dans le CV (élément retiré à la main)."""

    def __init__(self, path: str) -> None:
        super().__init__(
            "Cette proposition vise un élément qui n'est plus dans le CV.",
            details={"reason": "proposition_path_invalid", "path": path},
        )


class PropositionInvalidError(BusinessRuleViolationError):
    """La valeur proposée n'a pas la forme que son chemin attend : elle ne s'écrit pas."""

    def __init__(self, path: str, why: str) -> None:
        super().__init__(
            "Cette proposition ne peut pas être appliquée telle quelle.",
            details={"reason": "proposition_invalid", "path": path, "why": why},
        )


class DossierConflictError(BusinessRuleViolationError):
    """Le dossier a changé pendant l'appel au modèle : rien n'est écrasé, on recommence."""

    def __init__(self, dossier_id: UUID) -> None:
        super().__init__(
            "Le dossier a été modifié entre-temps, réessaie.",
            details={"reason": "dossier_conflict", "dossier_id": str(dossier_id)},
        )


class CvTooLargeError(InvalidInputError):
    """Le corps envoyé dépasse ce qu'un CV peut raisonnablement peser."""

    def __init__(self, size: int, limit: int) -> None:
        super().__init__(
            "Le CV envoyé est trop volumineux.",
            details={"reason": "cv_too_large", "size": size, "limit": limit},
        )


class LetterMissingError(DomainError):
    """Le dossier n'a pas encore de lettre : il faut d'abord l'ébaucher."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, dossier_id: UUID) -> None:
        super().__init__(
            "Ce dossier n'a pas encore de lettre de motivation.",
            details={"reason": "letter_missing", "dossier_id": str(dossier_id)},
        )


class ParagraphNotFoundError(DomainError):
    """L'index de paragraphe dépasse la lettre."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, index: int) -> None:
        super().__init__(
            "Paragraphe introuvable.", details={"reason": "paragraph_not_found", "index": index}
        )


class CvInvalidError(InvalidInputError):
    """Le CV retouché ne respecte pas la forme d'un CV."""

    def __init__(self, fields: Sequence[dict[str, Any]]) -> None:
        super().__init__(
            "Le CV envoyé est invalide.", details={"reason": "cv_invalid", "fields": list(fields)}
        )


class LetterInvalidError(InvalidInputError):
    """La lettre envoyée ne respecte pas sa forme (paragraphes, longueurs, sortes)."""

    def __init__(self, fields: Sequence[dict[str, Any]]) -> None:
        super().__init__(
            "La lettre envoyée est invalide.",
            details={"reason": "letter_invalid", "fields": list(fields)},
        )


class ModelUnavailableError(DependencyUnavailableError):
    """Le modèle de langage est injoignable, en panne ou refuse la requête."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("modèle de langage")
        self.details["reason"] = "ai_unavailable"
        if reason:
            self.details["detail"] = reason


class ModelOutputInvalidError(DependencyUnavailableError):
    """Le modèle a répondu, mais hors du schéma imposé. Un nouvel essai a ses chances."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("modèle de langage")
        self.details["reason"] = "ai_output_invalid"
        if reason:
            self.details["detail"] = reason
