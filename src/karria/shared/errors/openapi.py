"""Documentation OpenAPI des réponses d'erreur.

La V1 figeait des constantes du type `RESPONSES_401_404`, ce qui produisait
une combinatoire ingérable dès qu'une route avait besoin d'un cas de plus.
On expose ici une fonction **composable** :

    @router.get("/{item_id}", responses=error_responses(ErrorCode.NOT_FOUND))

Le schéma décrit est toujours `ErrorResponse` : la documentation ne peut
pas diverger de ce que le code renvoie réellement.
"""

from __future__ import annotations

from typing import Any

from karria.shared.errors.exceptions import ErrorCode
from karria.shared.errors.handlers import STATUS_BY_ERROR_CODE
from karria.shared.errors.schemas import ErrorResponse

_DESCRIPTIONS: dict[ErrorCode, str] = {
    ErrorCode.NOT_FOUND: "La ressource demandée n'existe pas.",
    ErrorCode.ALREADY_EXISTS: "La ressource existe déjà.",
    ErrorCode.INVALID_INPUT: "La requête est invalide.",
    ErrorCode.RULE_VIOLATION: "Une règle métier interdit cette opération.",
    ErrorCode.UNAUTHENTICATED: "Authentification requise ou invalide.",
    ErrorCode.FORBIDDEN: "Accès refusé.",
    ErrorCode.GONE: "La ressource a expiré ou a déjà été consommée : recommencer.",
    ErrorCode.RATE_LIMITED: "Trop de tentatives. `details.retry_after` indique quand réessayer.",
    ErrorCode.DEPENDENCY_FAILURE: "Un service dont dépend l'API est indisponible.",
    ErrorCode.INTERNAL: "Erreur interne.",
}


def error_responses(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """Construit le bloc `responses` d'une route pour les erreurs indiquées."""
    return {
        STATUS_BY_ERROR_CODE[code]: {
            "model": ErrorResponse,
            "description": _DESCRIPTIONS[code],
        }
        for code in codes
    }
