"""Le fil d'une personne et son état vis-à-vis de chaque offre.

Deux tables, deux rôles :

- l'**entrée de fil** dit qu'une offre a été retenue pour cette personne,
  avec quel score et d'où (une recherche, une alerte). Elle se recalcule
  quand les critères changent ;
- l'**état** dit ce que la personne a fait de l'offre (vue, ouverte,
  sauvegardée, candidaté, écartée). Il survit aux recalculs : une offre
  sauvegardée ne disparaît jamais du fil, quelle que soit la recherche
  suivante — c'est la garantie que la V1 avait dû ajouter après coup.

Sans critères, le fil est le corpus entier, du plus récent au plus ancien :
une personne qui vient de s'inscrire voit toutes les offres actives.

Sans compte du tout (ADR-0025), le fil est le même corpus, en lecture
seule : ni état, ni critères, ni journal — et compté par origine, parce
qu'un fil public sans quota est un fil qu'on aspire.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from karria.modules.offers.domain.ranking import MatchQuality

#: Lectures anonymes permises par origine sur la fenêtre : assez pour
#: feuilleter le fil et ouvrir des offres pendant dix minutes, trop peu pour
#: le recopier.
GUEST_RATE_LIMIT: Final = 240
#: Largeur de la fenêtre glissante des lectures anonymes, en secondes.
GUEST_RATE_WINDOW_SECONDS: Final = 600


class FeedOrigin(StrEnum):
    """Par quel chemin l'offre est entrée dans le fil."""

    SEARCH = "search"
    ALERT = "alert"


class FeedSort(StrEnum):
    """L'ordre du fil, choisi par la personne (ADR-0027).

    Le tri se fait **en base**, pagination comprise : l'app ne retrie jamais
    une page, sinon la page suivante contredirait la précédente.
    """

    #: Le score de la recherche, puis la date. Sans recherche, il n'y a pas de
    #: pertinence : le fil retombe sur `RECENT`.
    RELEVANCE = "relevance"
    #: Les plus récemment publiées d'abord.
    RECENT = "recent"
    #: Du plus proche au plus loin du lieu de la recherche ; les offres sans
    #: coordonnées en dernier. Exige un point de référence (`SearchCriteria.reference_point`).
    NEAREST = "nearest"


class FeedMode(StrEnum):
    """Ce que le fil montre."""

    #: Pas de critères : toutes les offres actives, les plus récentes d'abord.
    ALL_OFFERS = "all_offers"
    #: Des critères : les offres retenues pour la personne.
    SEARCH = "search"


@dataclass(frozen=True, slots=True)
class FeedEntry:
    """Une offre retenue pour une personne."""

    user_id: UUID
    offer_id: UUID
    score: float
    quality: MatchQuality
    reasons: tuple[str, ...]
    origin: FeedOrigin
    created_at: datetime
    notified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OfferState:
    """Ce que la personne a fait d'une offre. Tout est faux au départ."""

    user_id: UUID
    offer_id: UUID
    seen: bool = False
    opened: bool = False
    link_clicked: bool = False
    saved: bool = False
    applied: bool = False
    dismissed: bool = False
    seen_at: datetime | None = None
    opened_at: datetime | None = None
    saved_at: datetime | None = None
    applied_at: datetime | None = None
    dismissed_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def protected(self) -> bool:
        """Une offre que la personne a prise en main ne se retire pas du fil."""
        return self.saved or self.applied or self.opened

    @property
    def is_blank(self) -> bool:
        """Rien n'a été fait : inutile de persister."""
        return not (
            self.seen
            or self.opened
            or self.link_clicked
            or self.saved
            or self.applied
            or self.dismissed
        )

    def with_changes(
        self,
        *,
        at: datetime,
        seen: bool | None = None,
        opened: bool | None = None,
        link_clicked: bool | None = None,
        saved: bool | None = None,
        applied: bool | None = None,
        dismissed: bool | None = None,
    ) -> OfferState:
        """Applique ce que l'app rapporte. Les horodatages ne se posent qu'au passage à vrai."""
        state = replace(self, updated_at=at)
        if seen is not None:
            state = replace(
                state, seen=seen or state.seen, seen_at=state.seen_at or (at if seen else None)
            )
        if opened:
            state = replace(
                state,
                opened=True,
                seen=True,
                opened_at=state.opened_at or at,
                seen_at=state.seen_at or at,
            )
        if link_clicked:
            state = replace(state, link_clicked=True)
        if saved is not None:
            state = replace(state, saved=saved, saved_at=at if saved else None)
        if applied is not None:
            state = replace(state, applied=applied, applied_at=at if applied else None)
        if dismissed is not None:
            state = replace(state, dismissed=dismissed, dismissed_at=at if dismissed else None)
        return state


def entries_to_remove(
    current: list[FeedEntry], kept_offer_ids: set[UUID], states: dict[UUID, OfferState]
) -> list[UUID]:
    """Ce qu'un recalcul retire : ce qui n'est plus retenu **et** que la personne n'a pas pris en main."""
    return [
        entry.offer_id
        for entry in current
        if entry.offer_id not in kept_offer_ids
        and not (entry.offer_id in states and states[entry.offer_id].protected)
    ]
