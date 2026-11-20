"""Les quotas d'IA du module : portraits et imports, par mois civil (ADR-0038).

Deux actions du CV coûtent un appel cher et se répètent sans limite
naturelle : le **portrait** (≈ 0,067 $ l'image, `gemini-3.1-flash-image`) et
l'**import** d'un CV (≈ 1 c€ estimé, sortie longue). Relevés du 2026-11-16.

| action | gratuit | abonné « sans pub » (usage raisonnable) |
|---|---|---|
| portrait | 3 par mois | 30 par mois |
| import | 5 par mois | 30 par mois |

L'abonnement lève les limites du gratuit ; le plafond qui reste à l'abonné
est un **garde-fou** contre l'abus, que l'usage normal n'atteint pas.

Ce qui compte : une action **rendue**. Un refus ou une panne du modèle ne
consomment rien. Le mois est le mois civil UTC (remise à zéro le 1er à
minuit UTC, soit 1 h ou 2 h à Paris), comme le jour civil UTC du quota
d'envois (`documents`, ADR-0031).

Valeurs choisies le 2026-11-16 avec Marc, sans mesure d'usage réelle (deux
personnes actives en production) : à revoir avec les premiers chiffres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from karria.modules.cv.domain.errors import UsageQuotaExceededError


class AiUsage(StrEnum):
    """Une action d'IA soumise à quota."""

    PORTRAIT = "portrait"
    IMPORT = "import"
    #: Comptées pour le garde-fou journalier seulement (voir `DAILY_GUARDS`).
    INTERPRET = "interpret"
    TRANSCRIBE = "transcribe"
    #: Une tentative d'import : l'analyse du fichier, avant tout appel au modèle.
    IMPORT_TRY = "import_try"


#: Par mois civil (UTC), sans abonnement.
MONTHLY_LIMITS: Final[dict[AiUsage, int]] = {AiUsage.PORTRAIT: 3, AiUsage.IMPORT: 5}
#: Par mois civil (UTC), avec « Karria sans pub » : le garde-fou de l'usage raisonnable.
SUBSCRIBER_MONTHLY_LIMITS: Final[dict[AiUsage, int]] = {
    AiUsage.PORTRAIT: 30,
    AiUsage.IMPORT: 30,
}
#: Le chiffre que l'app affiche aux personnes sans abonnement.
MONTHLY_PORTRAIT_LIMIT: Final = MONTHLY_LIMITS[AiUsage.PORTRAIT]


#: Garde-fous **journaliers** (jour civil UTC), pour tout le monde : l'interprétation
#: d'une réponse et la dictée n'avaient ni quota ni frein, donc une dépense d'IA
#: sans plafond pour un compte en boucle (audit de sécurité du 2026-11-20). Ce ne
#: sont pas des quotas de produit : l'usage normal (une trentaine de réponses pour
#: écrire un CV) n'en approche pas.
#: `IMPORT_TRY` borne l'**analyse** d'un fichier importé (PDF, Word, image) : elle
#: coûte du processeur avant même le quota d'import, et un fichier piégé ne
#: s'interrompt pas en cours de page.
DAILY_GUARDS: Final[dict[AiUsage, int]] = {
    AiUsage.INTERPRET: 200,
    AiUsage.TRANSCRIBE: 80,
    AiUsage.IMPORT_TRY: 25,
}


def day_start(now: datetime) -> datetime:
    """Le début du jour civil UTC de `now`."""
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Le début du mois civil UTC de `now`, et le début du suivant."""
    utc = now.astimezone(UTC)
    start = utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:  # noqa: PLR2004 - décembre, le seul mois qui change d'année
        return start, start.replace(year=start.year + 1, month=1)
    return start, start.replace(month=start.month + 1)


@dataclass(frozen=True, slots=True)
class UsageQuota:
    """Où en est une personne ce mois-ci pour une action.

    Attributes:
        usage: L'action comptée.
        subscriber: La personne a « Karria sans pub » : le plafond est le garde-fou.
        limit: Le plafond mensuel qui s'applique à elle.
        used: Actions rendues depuis le début du mois.
        resets_at: Le début du mois suivant (UTC), quand le compteur repart.
    """

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
        return cls(
            usage=usage,
            subscriber=subscriber,
            limit=limits[usage],
            used=used,
            resets_at=next_month,
        )

    def ensure_available(self, now: datetime) -> None:
        """Refuse l'action de trop, en disant quand réessayer.

        Raises:
            UsageQuotaExceededError: Plafond atteint ; `retry_after` en
                secondes jusqu'au mois suivant.
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
