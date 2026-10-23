"""Le journal d'apprentissage : chaque geste sur une offre, avec le contexte qui l'explique.

Un modèle de classement (ADR-0019) apprend de ce que les personnes font des
offres **à la place où elles les ont vues** : une offre ouverte en tête de
fil n'apprend pas la même chose qu'une offre ouverte en page trois. On garde
donc, avec chaque geste, l'instantané des traits qui le rendaient probable
ou non : score de recherche, sorte d'accord d'intitulé, distance, âge,
accord de contrat et de temps, source, salaire affiché. Rien de nominatif
au-delà de l'identifiant de la personne, déjà présent sur l'état de l'offre.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from karria.modules.offers.domain.criteria import SearchCriteria
from karria.modules.offers.domain.feed import FeedEntry, FeedMode, OfferState
from karria.modules.offers.domain.offers import Offer
from karria.modules.offers.domain.titles import TitleMatcher


class InteractionKind(StrEnum):
    """Ce que la personne a fait."""

    IMPRESSION = "impression"
    OPEN = "open"
    LINK_CLICK = "link_click"
    SAVE = "save"
    UNSAVE = "unsave"
    APPLY = "apply"
    UNAPPLY = "unapply"
    DISMISS = "dismiss"
    UNDISMISS = "undismiss"
    DWELL = "dwell"


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Les traits de l'offre pour cette personne, au moment du geste."""

    feed_mode: FeedMode
    criteria_fingerprint: str | None
    score: float | None
    quality: str | None
    origin: str | None
    source: str
    age_hours: float
    distance_km: float | None
    #: 0 : l'intitulé dit ; 1 : un synonyme ; `None` : pas de recherche ou pas d'accord.
    title_match: int | None
    contract_match: bool | None
    schedule_match: bool | None
    has_salary: bool


@dataclass(frozen=True, slots=True)
class InteractionEvent:
    """Un geste, daté, placé, et expliqué par son instantané."""

    user_id: UUID
    offer_id: UUID
    kind: InteractionKind
    at: datetime
    snapshot: Snapshot
    position: int | None = None
    dwell_ms: int | None = None


def snapshot(
    offer: Offer, entry: FeedEntry | None, criteria: SearchCriteria | None, *, now: datetime
) -> Snapshot:
    """Calcule l'instantané ; sans recherche, seuls les traits de l'offre sont connus."""
    age = max((now - offer.published_at).total_seconds() / 3600, 0.0)
    distance: float | None = None
    title_match: int | None = None
    contract_match: bool | None = None
    schedule_match: bool | None = None
    if criteria is not None:
        location = offer.location
        if (
            location is not None
            and location.latitude is not None
            and location.longitude is not None
        ):
            distance = criteria.distance_km(location.latitude, location.longitude)
        title_match = TitleMatcher.of(criteria.job_titles, criteria.excluded_titles).match(
            offer.title
        )
        if criteria.contract_types:
            contract_match = offer.contract_type in criteria.contract_types
        if criteria.schedule is not None:
            schedule_match = offer.schedule == criteria.schedule
    return Snapshot(
        feed_mode=FeedMode.SEARCH if criteria is not None else FeedMode.ALL_OFFERS,
        criteria_fingerprint=criteria.fingerprint() if criteria is not None else None,
        score=entry.score if entry else None,
        quality=entry.quality.value if entry else None,
        origin=entry.origin.value if entry else None,
        source=offer.source.value,
        age_hours=round(age, 2),
        distance_km=round(distance, 1) if distance is not None else None,
        title_match=title_match,
        contract_match=contract_match,
        schedule_match=schedule_match,
        has_salary=offer.salary.min_monthly is not None or bool(offer.salary.raw),
    )


def transitions(before: OfferState, after: OfferState) -> list[InteractionKind]:
    """Les gestes que le passage d'un état à l'autre révèle, dans un ordre stable."""
    found: list[InteractionKind] = []
    if after.opened and not before.opened:
        found.append(InteractionKind.OPEN)
    if after.link_clicked and not before.link_clicked:
        found.append(InteractionKind.LINK_CLICK)
    if after.saved != before.saved:
        found.append(InteractionKind.SAVE if after.saved else InteractionKind.UNSAVE)
    if after.applied != before.applied:
        found.append(InteractionKind.APPLY if after.applied else InteractionKind.UNAPPLY)
    if after.dismissed != before.dismissed:
        found.append(InteractionKind.DISMISS if after.dismissed else InteractionKind.UNDISMISS)
    return found
