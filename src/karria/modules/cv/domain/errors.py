"""Erreurs du module `cv`.

Chaque erreur porte dans `details.reason` la raison fine que l'application
branche (`draft_not_found`, `draft_incomplete`, `photo_enhance_refused`…),
et s'appuie sur un `ErrorCode` existant pour le statut HTTP. Aucun code
HTTP n'est inventé ici : la traduction reste dans `shared/errors`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, ClassVar

# Le sous-module, et non le paquet `karria.shared.errors` : son `__init__`
# tire les gestionnaires HTTP, donc Starlette, que le domaine ne doit pas voir.
from karria.shared.errors.exceptions import (
    BusinessRuleViolationError,
    DependencyUnavailableError,
    DomainError,
    ErrorCode,
    InvalidInputError,
    RateLimitedError,
)


class DraftNotFoundError(DomainError):
    """L'appelant n'a aucun brouillon en cours."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__("Aucun brouillon de CV en cours.", details={"reason": "draft_not_found"})


class CvNotFoundError(DomainError):
    """La personne n'a pas encore de CV : rien à modifier directement."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__(
            "Aucun CV à modifier : crée-le ou importe-le d'abord.",
            details={"reason": "cv_not_found"},
        )


class PhotoMissingError(BusinessRuleViolationError):
    """Retravailler la photo du CV suppose qu'il en ait une."""

    def __init__(self) -> None:
        super().__init__(
            "Ton CV n'a pas encore de photo : ajoute-en une d'abord.",
            details={"reason": "photo_missing"},
        )


#: Ce que la personne lit, par action (`domain/quotas.py`).
_QUOTA_WORDS = {"portrait": "portraits", "import": "imports de CV"}


class UsageQuotaExceededError(RateLimitedError):
    """Le plafond mensuel d'une action d'IA est atteint (`domain/quotas.py`, ADR-0038).

    `details.reason` vaut `portrait_quota_exceeded` ou `import_quota_exceeded`.
    """

    def __init__(
        self, *, usage: str, limit: int, subscriber: bool, resets_at: datetime, retry_after: int
    ) -> None:
        words = _QUOTA_WORDS.get(usage, usage)
        message = (
            f"Tu as atteint la limite d'usage raisonnable de {limit} {words} ce mois-ci."
            if subscriber
            else f"Tu as utilisé tes {limit} {words} du mois. "
            "Avec Karria sans pub, tu n'as plus de limite."
        )
        super().__init__(
            message,
            retry_after=retry_after,
            details={
                "reason": f"{usage}_quota_exceeded",
                "limit": limit,
                "subscriber": subscriber,
                "resets_at": resets_at.isoformat(),
            },
        )


class DraftIncompleteError(BusinessRuleViolationError):
    """La finalisation exige que chaque chapitre soit validé ou passé."""

    def __init__(self, chapters: Sequence[str]) -> None:
        super().__init__(
            "Des chapitres restent à faire avant de finaliser le CV.",
            details={"reason": "draft_incomplete", "chapters": list(chapters)},
        )


class ChapterValidationError(InvalidInputError):
    """Le contenu envoyé pour un chapitre ne respecte pas son schéma."""

    def __init__(self, chapter: str, fields: Sequence[dict[str, Any]]) -> None:
        super().__init__(
            "Le contenu du chapitre est invalide.",
            details={"reason": "chapter_invalid", "chapter": chapter, "fields": list(fields)},
        )


class ChapterSubmissionError(InvalidInputError):
    """La forme de la soumission ne correspond pas au chapitre.

    Un chapitre simple attend `data`, un chapitre en boucle attend `items`,
    et un chapitre obligatoire ne se passe pas.
    """

    def __init__(self, chapter: str, message: str, *, reason: str) -> None:
        super().__init__(message, details={"reason": reason, "chapter": chapter})


class UnsupportedUploadError(InvalidInputError):
    """Un fichier reçu — audio, document, image — n'est pas exploitable."""

    def __init__(self, message: str, *, reason: str, field: str) -> None:
        super().__init__(message, details={"reason": reason, "field": field})


class PhotoEnhanceRefusedError(InvalidInputError):
    """Le générateur a refusé l'image (contenu, visage absent…).

    Erreur **de l'entrée**, pas du service : réessayer avec la même photo
    donnerait le même refus. L'application garde alors l'originale.
    """

    def __init__(self, finish_reason: str | None = None) -> None:
        details: dict[str, Any] = {"reason": "photo_enhance_refused"}
        if finish_reason:
            details["finish_reason"] = finish_reason
        super().__init__(
            "Impossible d'améliorer cette photo. Essaie avec une autre : visage bien "
            "visible, cadrage simple.",
            details=details,
        )


class PhotoEnhanceUnavailableError(DependencyUnavailableError):
    """Le générateur d'image ne répond pas ou n'a rien rendu d'exploitable."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("amélioration de photo")
        self.details["reason"] = "photo_enhance_unavailable"
        if reason:
            self.details["detail"] = reason


class ModelUnavailableError(DependencyUnavailableError):
    """Le modèle de langage est injoignable, en panne ou refuse la requête."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("Gemini")
        self.details["reason"] = "ai_unavailable"
        if reason:
            self.details["detail"] = reason


class ModelOutputInvalidError(DependencyUnavailableError):
    """Le modèle a répondu, mais hors du schéma imposé.

    Classée en défaillance de dépendance : ce n'est ni la faute de
    l'appelant ni une règle métier, et un nouvel essai a ses chances. La
    sortie fautive part dans les journaux, jamais vers le client.
    """

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("Gemini")
        self.details["reason"] = "ai_output_invalid"
        if reason:
            self.details["detail"] = reason
