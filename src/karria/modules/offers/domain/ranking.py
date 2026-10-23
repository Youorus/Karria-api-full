"""Du candidat retrouvé à l'offre classée : porte d'intitulé, score, qualité, raisons.

La recherche se fait en deux temps : un **rappel** large et bon marché
(index hybride, dense + deux branches lexicales, fusion RRF côté Qdrant),
puis une **décision** du domaine sur chaque candidat : contraintes dures
(`SearchCriteria.accepts`), puis porte d'intitulé (`domain/titles.py`).
Il n'y a pas de reclasseur neuronal : l'ADR-0016 consigne pourquoi
(mesuré sur ce CPU, le léger se trompe, le juste est seize fois trop lent).

Le score, dans [0, 1], dit deux choses que la personne comprend : le
titre reprend-il l'un des **premiers** intitulés demandés (le métier dit),
ou un synonyme plus lointain ; et quelle est la place de l'offre dans le
rappel hybride. Il se recalibre avec `scripts/offers_eval.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from karria.modules.offers.domain.criteria import SearchCriteria
from karria.modules.offers.domain.offers import Offer, RemoteMode
from karria.modules.offers.domain.titles import TitleMatcher


class MatchQuality(StrEnum):
    """Ce que l'app affiche : « Très alignée », rien, « À explorer »."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: Candidats demandés à la fusion hybride pour une recherche. Sans
#: reclasseur à payer, la fenêtre peut être large : 300 candidats se lisent
#: en base en ~15 ms.
#: Candidats demandés à l'index : assez pour que le fil porte toutes les offres
#: pertinentes d'une demande large (mesuré 2026-10-23 : « cuisinier · France »).
RETRIEVAL_LIMIT: Final = 1_500
#: Les premiers intitulés de la liste sont le métier dit ; les suivants,
#: des synonymes que Karria a ajoutés.
PRIMARY_TITLES: Final = 2
TITLE_PRIMARY_SCORE: Final = 1.0
TITLE_SYNONYM_SCORE: Final = 0.8
#: Part du titre et part du rang de rappel dans le score.
TITLE_WEIGHT: Final = 0.6
#: Au-dessus : « Très alignée ». Entre les deux : rien. En dessous : « À explorer ».
QUALITY_HIGH_SCORE: Final = 0.85
QUALITY_MEDIUM_SCORE: Final = 0.65
#: Place maximale du fil issu d'une recherche.
FEED_MAX_ENTRIES: Final = 1_000
#: Voisines d'intitulé cherchées dans les premiers candidats du rappel seulement.
NEIGHBOUR_WINDOW: Final = 200


@dataclass(frozen=True, slots=True)
class Candidate:
    """Une offre retrouvée par l'index, avec son score de fusion."""

    offer_id: UUID
    retrieval_score: float


@dataclass(frozen=True, slots=True)
class RankedOffer:
    """Une offre classée pour une personne."""

    offer_id: UUID
    score: float
    quality: MatchQuality
    reasons: tuple[str, ...]


def quality_of(score: float) -> MatchQuality:
    """Le palier d'un score."""
    if score >= QUALITY_HIGH_SCORE:
        return MatchQuality.HIGH
    if score >= QUALITY_MEDIUM_SCORE:
        return MatchQuality.MEDIUM
    return MatchQuality.LOW


def rank(
    ordered: Sequence[Offer],
    criteria: SearchCriteria,
    *,
    limit: int = FEED_MAX_ENTRIES,
) -> list[RankedOffer]:
    """Filtre dur, porte d'intitulé, score ; `ordered` est dans l'ordre du rappel."""
    matcher = TitleMatcher.of(criteria.job_titles, criteria.excluded_titles)
    total = max(len(ordered), 1)
    ranked: list[RankedOffer] = []
    for position, offer in enumerate(ordered):
        if not criteria.accepts(offer):
            continue
        phrase = matcher.match(offer.title)
        if phrase is None:
            continue
        title_score = TITLE_PRIMARY_SCORE if phrase < PRIMARY_TITLES else TITLE_SYNONYM_SCORE
        score = round(TITLE_WEIGHT * title_score + (1 - TITLE_WEIGHT) * (1 - position / total), 4)
        ranked.append(
            RankedOffer(
                offer_id=offer.id,
                score=score,
                quality=quality_of(score),
                reasons=reasons_for(offer, criteria),
            )
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[:limit]


def gate_rejects(ordered: Sequence[Offer], criteria: SearchCriteria) -> list[Offer]:
    """Les offres que le filtre dur retient mais que la porte d'intitulé écarte.

    Ce sont les voisines d'une recherche : « commis de cuisine » pour
    « cuisinier ». Les élargissements s'en nourrissent — dans la tête du
    rappel seulement (`NEIGHBOUR_WINDOW`), là où le voisinage a un sens.
    """
    matcher = TitleMatcher.of(criteria.job_titles, criteria.excluded_titles)
    window = ordered[:NEIGHBOUR_WINDOW]
    return [o for o in window if criteria.accepts(o) and matcher.match(o.title) is None]


def reasons_for(offer: Offer, criteria: SearchCriteria) -> tuple[str, ...]:
    """Pourquoi cette offre : ce que la personne a demandé et que l'offre confirme."""
    found: list[str] = []
    if criteria.contract_types and offer.contract_type in criteria.contract_types:
        found.append(offer.contract_type.label)
    if offer.location is not None and offer.location.is_geocoded:
        distance = criteria.distance_km(
            offer.location.latitude or 0.0, offer.location.longitude or 0.0
        )
        if distance is not None and offer.location.city:
            found.append(
                f"{offer.location.city} · {round(distance)} km"
                if distance >= 1
                else offer.location.city
            )
    if offer.remote is RemoteMode.REMOTE:
        found.append("Télétravail")
    elif offer.remote is RemoteMode.HYBRID and criteria.remote:
        found.append("Hybride")
    if criteria.salary_min_monthly and offer.salary.min_monthly:
        found.append(f"{offer.salary.min_monthly} €/mois")
    return tuple(found[:3])
