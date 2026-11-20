"""Le filigrane Karria : absent des trois premiers CV du mois, jamais chez un abonné.

Contrat (décision de Marc, 2026-11-20 — ADR-0040)
-------------------------------------------------
Le « filigrane » est la ligne de pied de page « CV réalisé avec Karria ·
karria.fr/app » (`infrastructure/footer.py`). Elle ne bloque rien : la
personne récupère **toujours** son document.

- Chaque mois civil (UTC), les **trois premiers CV exportés** d'un compte
  gratuit sortent **sans** filigrane. À partir du quatrième, le filigrane est
  posé, et l'app propose « Karria sans pub » (4,99 € par mois) pour le retirer
  et soutenir Karria. Refuser ne retire rien : le CV se télécharge, filigrané.
- Un **abonné** n'a jamais de filigrane, et ses exports ne lui sont pas comptés
  comme une limite (ils restent des exports sans filigrane du mois : s'il
  résilie, il a simplement consommé ses trois).
- Une **lettre** suit l'état du compte au moment où elle est exportée (elle
  porte le filigrane si le prochain CV le porterait) ; elle ne consomme rien.
- L'aperçu de l'atelier montre ce que l'export donnera.

Seuls comptent les CV exportés **sans** filigrane depuis le début du mois :
un CV filigrané n'entame rien, et un export d'avant cette règle compte comme
sans filigrane (colonne à `false` par défaut).

**Une génération n'est pas un fichier.** L'app exporte à l'ouverture de
« Finaliser », après « Optimiser », au retour de l'atelier : trois fichiers
pour un seul CV. Les exports sans filigrane faits dans la demi-heure qui suit
le premier d'entre eux comptent pour **une** génération ; pendant cette
demi-heure, un nouvel export reste sans filigrane, même si c'était la
troisième du mois.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

#: CV exportés sans filigrane offerts à un compte gratuit, par mois civil (UTC).
FREE_CLEAN_EXPORTS_PER_MONTH: Final = 3
#: Les exports faits dans cette fenêtre après le premier sont le même CV, retouché.
GENERATION_WINDOW: Final = timedelta(minutes=30)


def generation_starts(exports: Iterable[datetime]) -> list[datetime]:
    """Le début de chaque génération : un export à plus d'une demi-heure du début de la précédente."""
    starts: list[datetime] = []
    for moment in sorted(exports):
        if not starts or moment - starts[-1] > GENERATION_WINDOW:
            starts.append(moment)
    return starts


def month_start(now: datetime) -> datetime:
    """Le premier instant du mois civil (UTC) de `now`."""
    moment = now.astimezone(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class ExportAllowance:
    """Où en est un compte de ses exports sans filigrane ce mois-ci."""

    subscriber: bool
    #: Générations sans filigrane depuis le début du mois.
    used: int
    free_per_month: int = FREE_CLEAN_EXPORTS_PER_MONTH
    #: Une génération sans filigrane est en cours (moins d'une demi-heure) :
    #: un nouvel export en fait partie, il ne porte pas le filigrane.
    continuing: bool = False

    @classmethod
    def at(cls, now: datetime, *, subscriber: bool, exports: Iterable[datetime]) -> ExportAllowance:
        """L'état à `now`, d'après les instants des CV exportés sans filigrane ce mois-ci."""
        starts = generation_starts(exports)
        continuing = bool(starts) and now - starts[-1] <= GENERATION_WINDOW
        return cls(subscriber=subscriber, used=len(starts), continuing=continuing)

    @property
    def remaining(self) -> int:
        """Exports sans filigrane encore offerts ce mois-ci (un abonné n'a pas de compteur)."""
        return max(0, self.free_per_month - self.used)

    @property
    def watermark(self) -> bool:
        """Le prochain export portera-t-il le filigrane ?"""
        return not self.subscriber and self.used >= self.free_per_month and not self.continuing


#: Sans compteur branché (un script, un essai) : le filigrane, comme avant cette règle.
ALWAYS_WATERMARKED: Final = ExportAllowance(subscriber=False, used=FREE_CLEAN_EXPORTS_PER_MONTH)
