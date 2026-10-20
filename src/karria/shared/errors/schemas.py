"""Format unique des réponses d'erreur.

Contrat
-------
Toute erreur renvoyée par l'API — métier, validation ou panne — a
**exactement** cette forme. Un client écrit son gestionnaire d'erreurs une
seule fois. C'est le genre de garantie qui coûte dix lignes ici et évite
des dizaines de cas particuliers côté application mobile.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from karria.shared.errors.exceptions import ErrorCode


class ErrorDetail(BaseModel):
    """Description exploitable par le client."""

    code: ErrorCode = Field(description="Nature de l'erreur, stable et branchable.")
    message: str = Field(description="Message destiné à un humain.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Contexte structuré. Ne contient jamais de secret ni de trace.",
    )


class ErrorResponse(BaseModel):
    """Enveloppe de toute réponse d'erreur de l'API."""

    error: ErrorDetail
    correlation_id: str | None = Field(
        default=None,
        description="Identifiant de la requête. À fournir au support pour investigation.",
    )
