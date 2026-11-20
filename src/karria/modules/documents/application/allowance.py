"""Les exports sans filigrane d'un compte, ce mois-ci (ADR-0040, `domain/watermark.py`)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from karria.modules.documents.domain.watermark import ExportAllowance, month_start
from karria.shared.clock import Clock


class SubscriptionReader(Protocol):
    """La personne a-t-elle « Karria sans pub » ? (module `payments`, ADR-0024).

    Branché par `bootstrap/wiring.py`. Par défaut, personne n'est abonné : le
    filigrane revient après trois CV, ce qui n'empêche jamais de télécharger.
    """

    async def is_subscriber(self, user_id: UUID) -> bool:
        """Vrai si l'abonnement est actif (grâce comprise)."""
        ...


class CleanExportCounter(Protocol):
    """Ce que le compteur lit des exports (`GeneratedCvRepository`)."""

    async def clean_cv_times(self, user_id: UUID, *, since: datetime) -> list[datetime]:
        """Les instants des CV exportés sans filigrane depuis `since`."""
        ...


class ReadExportAllowance:
    """Où en est la personne : abonnée ? combien de CV sans filigrane ce mois-ci ?"""

    def __init__(
        self, *, generated: CleanExportCounter, subscriptions: SubscriptionReader, clock: Clock
    ) -> None:
        self._generated, self._subscriptions, self._clock = generated, subscriptions, clock

    async def execute(self, user_id: UUID) -> ExportAllowance:
        """L'état du compte, lu à chaque fois : un abonnement pris à l'instant compte."""
        now = self._clock.now()
        subscriber = await self._subscriptions.is_subscriber(user_id)
        exports = await self._generated.clean_cv_times(user_id, since=month_start(now))
        return ExportAllowance.at(now, subscriber=subscriber, exports=exports)
