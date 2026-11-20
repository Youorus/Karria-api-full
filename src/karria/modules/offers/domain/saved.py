"""Les recherches enregistrées : ce que Karria continue de surveiller pour quelqu'un (ADR-0041).

Chaque recherche qu'une personne applique est retenue. La dernière est la
recherche **en cours** (elle fait le fil) ; les précédentes restent
enregistrées et, tant que leur alerte est active, une offre nouvelle qui
leur convient déclenche une notification. La personne les retrouve dans ses
paramètres, où elle active, coupe ou supprime chacune.

Règles :

- **Dix au plus** par personne : au-delà, la moins récemment utilisée sort
  (jamais la recherche en cours).
- **Le rythme est celui de la personne**, pas de la recherche : pas deux
  notifications en moins de deux heures, quatre par jour au plus, toutes
  recherches confondues (`domain/alerts.py`).
- **Jamais deux fois la même offre** pour une même recherche.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID

#: Recherches gardées par personne.
MAX_SAVED_SEARCHES: Final = 10
#: Offres déjà signalées dont on garde la trace, par recherche.
NOTIFIED_MEMORY: Final = 200
#: Karria dit une fois, en une phrase, où couper une alerte : à la première
#: recherche enregistrée, puis à la deuxième (celle où la première continue de
#: surveiller). Ensuite elle ne le répète plus : l'espace reste épuré.
HINT_UNTIL: Final = 2
SAVED_SEARCH_HINT: Final = (
    "Je vous préviens dès qu'une offre correspond ; "
    "vous pouvez couper cette alerte dans vos paramètres, rubrique « Mes recherches »."
)


class _Search(Protocol):
    """Ce que ces règles lisent d'une recherche enregistrée."""

    @property
    def id(self) -> UUID: ...
    @property
    def fingerprint(self) -> str: ...
    @property
    def alerts_enabled(self) -> bool: ...
    @property
    def last_used_at(self) -> datetime: ...
    @property
    def target_keys(self) -> tuple[str, ...]: ...
    @property
    def last_alert_at(self) -> datetime | None: ...
    @property
    def alerts_sent_on(self) -> datetime | None: ...
    @property
    def alerts_sent_today(self) -> int: ...


def to_evict[S: _Search](searches: Sequence[S], *, current: str | None) -> list[S]:
    """Celles qui sortent quand il y en a plus de dix : les moins récemment utilisées."""
    excess = len(searches) - MAX_SAVED_SEARCHES
    if excess <= 0:
        return []
    candidates = sorted(
        (s for s in searches if s.fingerprint != current), key=lambda s: s.last_used_at
    )
    return candidates[:excess]


def demanded_keys(searches: Sequence[_Search]) -> list[str]:
    """Les cibles de collecte que demandent les recherches dont l'alerte est active."""
    keys: dict[str, None] = {}
    for search in searches:
        if search.alerts_enabled:
            keys.update(dict.fromkeys(search.target_keys))
    return list(keys)


class _Rhythm(Protocol):
    """Ce que le rythme lit : une recherche enregistrée, ou les critères courants."""

    @property
    def last_alert_at(self) -> datetime | None: ...
    @property
    def alerts_sent_on(self) -> datetime | None: ...
    @property
    def alerts_sent_today(self) -> int: ...


def alert_rhythm(searches: Sequence[_Rhythm], *, now: datetime) -> tuple[datetime | None, int]:
    """(dernière notification, envois du jour) d'une personne, toutes recherches confondues."""
    lasts = [s.last_alert_at for s in searches if s.last_alert_at is not None]
    today = sum(
        s.alerts_sent_today
        for s in searches
        if s.alerts_sent_on is not None and s.alerts_sent_on.date() == now.date()
    )
    return (max(lasts) if lasts else None), today


def remember_notified(known: Sequence[UUID], new: Sequence[UUID]) -> tuple[UUID, ...]:
    """Les offres signalées, les plus récentes gardées (bornées)."""
    merged = [*known, *(i for i in new if i not in set(known))]
    return tuple(merged[-NOTIFIED_MEMORY:])
