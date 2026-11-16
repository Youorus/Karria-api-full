"""Les quotas d'IA des dossiers de candidature, par mois civil (ADR-0038).

Trois actions appellent le modèle et se répètent à volonté : **ouvrir un
dossier** (le CV adapté à l'offre, sortie longue : ≈ 1,4 c€ estimé), **écrire
la lettre** (≈ 0,3 c€) et **réécrire un paragraphe**. Relevé du 2026-11-16.

| action | gratuit | abonné « sans pub » (usage raisonnable) |
|---|---|---|
| dossier ouvert | 20 par mois | 100 par mois |
| lettre écrite | 30 par mois | 150 par mois |
| paragraphe réécrit | 100 par mois | 500 par mois |

Vingt dossiers, c'est presque une candidature par jour ouvré ; la lettre a
de la marge pour être recommencée, le paragraphe pour être retravaillé.
L'abonnement lève ces plafonds ; ce qui reste à l'abonné est un garde-fou
contre l'abus, que l'usage normal n'atteint pas.

Ce qui compte : une action **rendue**. Relancer une adaptation échouée ne
compte pas (l'échec n'a rien coûté de notable). Mois civil UTC, comme le
module `cv` — chaque module garde sa règle, aucun n'importe l'autre.

Le frein de rythme (`AI_RATE_LIMIT`, trente appels en dix minutes) reste en
place : il arrête un script dans l'instant, le quota borne le mois.

Valeurs choisies le 2026-11-16 avec Marc, sans mesure d'usage réelle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from karria.modules.applications.domain.errors import UsageQuotaExceededError


class AiUsage(StrEnum):
    """Une action d'IA du module soumise à quota."""

    DOSSIER = "dossier"
    LETTER = "letter"
    PARAGRAPH = "paragraph"


#: Par mois civil (UTC), sans abonnement.
MONTHLY_LIMITS: Final[dict[AiUsage, int]] = {
    AiUsage.DOSSIER: 20,
    AiUsage.LETTER: 30,
    AiUsage.PARAGRAPH: 100,
}
#: Par mois civil (UTC), avec « Karria sans pub » : le garde-fou de l'usage raisonnable.
SUBSCRIBER_MONTHLY_LIMITS: Final[dict[AiUsage, int]] = {
    AiUsage.DOSSIER: 100,
    AiUsage.LETTER: 150,
    AiUsage.PARAGRAPH: 500,
}


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Le début du mois civil UTC de `now`, et le début du suivant."""
    utc = now.astimezone(UTC)
    start = utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:  # noqa: PLR2004 - décembre, le seul mois qui change d'année
        return start, start.replace(year=start.year + 1, month=1)
    return start, start.replace(month=start.month + 1)


@dataclass(frozen=True, slots=True)
class UsageQuota:
    """Où en est une personne ce mois-ci pour une action."""

    usage: AiUsage
    subscriber: bool
    limit: int
    used: int
    resets_at: datetime

    @property
    def remaining(self) -> int:
        """Ce qui reste, jamais négatif."""
        return max(self.limit - self.used, 0)

    @classmethod
    def of(cls, usage: AiUsage, *, used: int, subscriber: bool, now: datetime) -> UsageQuota:
        """Le quota du mois de `now`, avec `used` actions déjà rendues."""
        _, next_month = month_bounds(now)
        limits = SUBSCRIBER_MONTHLY_LIMITS if subscriber else MONTHLY_LIMITS
        return cls(usage, subscriber, limits[usage], used, next_month)

    def ensure_available(self, now: datetime) -> None:
        """Refuse l'action de trop, en disant quand réessayer.

        Raises:
            UsageQuotaExceededError: Plafond atteint ; `retry_after` jusqu'au mois suivant.
        """
        if self.remaining > 0:
            return
        retry_after = max(int((self.resets_at - now.astimezone(UTC)).total_seconds()), 1)
        raise UsageQuotaExceededError(
            usage=self.usage.value,
            limit=self.limit,
            subscriber=self.subscriber,
            resets_at=self.resets_at,
            retry_after=retry_after,
        )
