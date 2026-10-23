"""Erreurs du module `offers`.

Chaque erreur porte dans `details.reason` la raison fine que l'application
branche, sur un `ErrorCode` existant du socle : aucun statut HTTP n'est
inventé ici.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

# Le sous-module, et non le paquet : son `__init__` tire Starlette.
from karria.shared.errors.exceptions import (
    DependencyUnavailableError,
    DomainError,
    ErrorCode,
    InvalidInputError,
    RateLimitedError,
)


class OfferNotFoundError(DomainError):
    """L'offre n'existe pas, ou n'est plus visible."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, offer_id: UUID) -> None:
        super().__init__(
            "Cette offre n'est plus disponible.",
            details={"reason": "offer_not_found", "offer_id": str(offer_id)},
        )


class ConversationNotFoundError(DomainError):
    """La conversation désignée n'appartient pas à l'appelant, ou n'existe pas."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(
            "Conversation introuvable.",
            details={"reason": "conversation_not_found", "conversation_id": str(conversation_id)},
        )


class CriteriaNotFoundError(DomainError):
    """La personne n'a pas de critères : rien à surveiller, rien à basculer."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__("Aucune recherche en cours.", details={"reason": "criteria_not_found"})


class CriteriaIncompleteError(InvalidInputError):
    """Des critères posés directement doivent au moins dire quel métier, et où."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "Les critères sont incomplets.",
            details={"reason": "criteria_incomplete", "missing": missing},
        )


class AssistantRateLimitedError(RateLimitedError):
    """Trop de tours de conversation sur la journée."""

    def __init__(self, *, retry_after: int) -> None:
        super().__init__(
            "Karria a beaucoup discuté aujourd'hui ; réessayez un peu plus tard.",
            retry_after=retry_after,
            details={"reason": "assistant_rate_limited"},
        )


class ModelUnavailableError(DependencyUnavailableError):
    """Le fournisseur de modèle (Gemini) ne répond pas ou n'est pas configuré."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("Gemini", reason)
        self.details["reason"] = "ai_unavailable"


class ModelOutputInvalidError(DependencyUnavailableError):
    """Le modèle a répondu hors du schéma imposé — une panne, pas une donnée."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("Gemini", reason)
        self.details["reason"] = "ai_output_invalid"


class SearchUnavailableError(DependencyUnavailableError):
    """Le moteur de recherche ou le serveur d'inférence ne répond pas."""

    def __init__(self, dependency: str, reason: str | None = None) -> None:
        super().__init__(dependency, reason)
        self.details["reason"] = "search_unavailable"


class BatchRejectedError(InvalidInputError):
    """Un lot remis par le collecteur est refusé en bloc (trop gros, cible inconnue, version)."""

    def __init__(self, reason: str) -> None:
        super().__init__("Lot refusé.", details={"reason": reason})
