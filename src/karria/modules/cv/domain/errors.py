"""Erreurs du module `cv`.

Chaque erreur porte dans `details.reason` la raison fine que l'application
branche (`draft_not_found`, `draft_incomplete`, `photo_enhance_refused`…),
et s'appuie sur un `ErrorCode` existant pour le statut HTTP. Aucun code
HTTP n'est inventé ici : la traduction reste dans `shared/errors`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

# Le sous-module, et non le paquet `karria.shared.errors` : son `__init__`
# tire les gestionnaires HTTP, donc Starlette, que le domaine ne doit pas voir.
from karria.shared.errors.exceptions import (
    BusinessRuleViolationError,
    DependencyUnavailableError,
    DomainError,
    ErrorCode,
    InvalidInputError,
)


class DraftNotFoundError(DomainError):
    """L'appelant n'a aucun brouillon en cours."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__("Aucun brouillon de CV en cours.", details={"reason": "draft_not_found"})


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
