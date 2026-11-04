"""La règle de version : une app installée est à jour, en retard, ou trop vieille pour servir.

Contrat
-------
- `build` est le numéro de build natif de l'app installée (entier).
- `minimum_build` : en dessous, l'app **doit** se mettre à jour (le contrat
  d'API a changé de façon incompatible, ou un défaut grave est corrigé).
  `0` : aucune contrainte.
- `latest_build` : la dernière version publiée ; en dessous, une mise à jour
  est **proposée**. `0` : rien à proposer.
- Un `minimum_build` au-dessus de `latest_build` est une erreur de réglage :
  on ne propose jamais un build qui n'existe pas, `latest` vaut alors `minimum`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReleaseStatus(StrEnum):
    """Ce que l'app doit faire."""

    OK = "ok"
    UPDATE_AVAILABLE = "update_available"
    UPDATE_REQUIRED = "update_required"


@dataclass(frozen=True, slots=True)
class ReleaseVerdict:
    """Le verdict et les deux seuils qui l'ont produit."""

    status: ReleaseStatus
    latest_build: int
    minimum_build: int


def evaluate_release(build: int, *, minimum_build: int, latest_build: int) -> ReleaseVerdict:
    """Compare le build installé aux seuils de l'environnement."""
    minimum = max(minimum_build, 0)
    latest = max(latest_build, minimum)
    if minimum and build < minimum:
        status = ReleaseStatus.UPDATE_REQUIRED
    elif latest and build < latest:
        status = ReleaseStatus.UPDATE_AVAILABLE
    else:
        status = ReleaseStatus.OK
    return ReleaseVerdict(status=status, latest_build=latest, minimum_build=minimum)
