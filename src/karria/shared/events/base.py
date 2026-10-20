"""Définition d'un événement de domaine.

Un événement décrit **un fait accompli**, au passé : `UserRegistered`,
`InvoicePaid`. Jamais un ordre (`SendEmail`) — un ordre crée un couplage
déguisé entre l'émetteur et l'action attendue, ce que le bus est justement
censé supprimer.

Les événements sont immuables : ils relatent le passé, qui ne se modifie pas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class DomainEvent(BaseModel):
    """Classe de base de tout événement métier."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def name(self) -> str:
        """Nom de l'événement, utilisé comme clé dans les journaux."""
        return type(self).__name__
