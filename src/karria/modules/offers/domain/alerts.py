"""Les alertes : prévenir, au bon moment, sans harceler.

Une alerte naît quand une offre **nouvellement indexée** convient aux
critères d'une personne — matching inversé : c'est l'offre qui cherche ses
lecteurs, pas l'inverse. Les règles ici disent **si** et **comment** on
prévient ; elles ne savent rien d'Expo ni des appareils.

Ce qu'on garde de la V1 : la notification n'est envoyée que pour un match
de haute qualité, jamais deux fois pour la même offre, et pas plus de
quelques fois par jour. Ce qu'on change : un seul envoi groupé par passage
(« 3 nouvelles offres »), pas un par offre.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from karria.modules.offers.domain.offers import Offer
from karria.modules.offers.domain.ranking import QUALITY_HIGH_SCORE

#: Similarité cosinus minimale entre l'offre et les critères : un pré-filtre
#: large et bon marché (e5 place deux textes du même domaine au-dessus de
#: 0,80, deux domaines différents vers 0,75) ; la porte d'intitulé décide.
ALERT_MIN_SIMILARITY: Final = 0.78
#: Score à partir duquel on prévient — la qualité « haute ».
ALERT_MIN_SCORE: Final = QUALITY_HIGH_SCORE
#: Pas deux notifications à la même personne en moins de deux heures.
ALERT_MIN_INTERVAL: Final = timedelta(hours=2)
#: Et pas plus de quatre par jour.
ALERT_DAILY_MAX: Final = 4
#: Offres citées dans le corps d'une notification groupée.
ALERT_BODY_OFFERS: Final = 3
#: Une offre publiée il y a plus longtemps n'est plus « nouvelle ».
ALERT_MAX_OFFER_AGE: Final = timedelta(days=3)


@dataclass(frozen=True, slots=True)
class AlertNotification:
    """Ce qu'on envoie : un titre, un corps, et de quoi ouvrir le fil."""

    title: str
    body: str
    data: dict[str, str]


def may_notify(*, now: datetime, last_alert_at: datetime | None, sent_today: int) -> bool:
    """Le rythme : intervalle minimal et plafond journalier."""
    if sent_today >= ALERT_DAILY_MAX:
        return False
    return last_alert_at is None or now - last_alert_at >= ALERT_MIN_INTERVAL


def is_fresh(offer: Offer, *, now: datetime) -> bool:
    """Une offre assez récente pour justifier une notification."""
    return now - offer.published_at <= ALERT_MAX_OFFER_AGE


def build_notification(criteria_summary: str, offers: Sequence[Offer]) -> AlertNotification:
    """Un message groupé, en français, qui nomme les premières offres."""
    count = len(offers)
    title = "Une nouvelle offre pour vous" if count == 1 else f"{count} nouvelles offres pour vous"
    lines = [_line(offer) for offer in offers[:ALERT_BODY_OFFERS]]
    if count > ALERT_BODY_OFFERS:
        lines.append(
            f"… et {count - ALERT_BODY_OFFERS} autre{'s' if count - ALERT_BODY_OFFERS > 1 else ''}"
        )
    body = f"{criteria_summary} — " + " · ".join(lines) if criteria_summary else " · ".join(lines)
    return AlertNotification(
        title=title,
        body=body[:240],
        data={"type": "NEW_OFFERS", "offer_id": str(offers[0].id), "count": str(count)},
    )


def _line(offer: Offer) -> str:
    company = f" chez {offer.company.name}" if offer.company else ""
    return f"{offer.title}{company}"
