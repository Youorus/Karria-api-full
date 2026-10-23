"""Le planificateur de collecte piloté par la demande : quoi demander aux sites, et quand.

Contrat
-------
France Travail est la couche de base : nationale, incrémentale, polluée par
rien (ADR-0016 bis). JobSpy (Indeed, HelloWork…) ne vaut que là où il
ajoute : les métiers et les lieux que les gens demandent, et que France
Travail couvre mal. Ce module décide **quoi** et **quand**, par des règles
explicables ; la collecte elle-même est un autre processus, qui ne
reçoit que des termes courts (`ScrapeTarget`), jamais le texte d'une
personne.

1. **Cibles canoniques.** Une demande donne des cibles
   « terme canonique x département x contrat ». Mille personnes qui
   cherchent « serveur à Paris » font **une** cible ; le lien
   personne ↔ cible (`TargetDemand`) sert au compte de la demande.
   Le terme vient de la sortie structurée du modèle (`search_terms`,
   1 à 4 mots, bornés, validés contre le vocabulaire des titres réels) ;
   le lieu, du géocodage ; le contrat, des critères.
2. **Priorité par cible.** Demande (personnes actives, pondérées par la
   fraîcheur de leur recherche), manque d'offre (une recherche maigre),
   rendement historique (offres nouvelles par passage, lissé), ancienneté
   du dernier passage, et — en négatif — ce que France Travail rapporte
   déjà sur la même cible.
3. **Cadence adaptative.** Une cible qui rapporte se recollecte plus
   souvent ; une cible muette recule exponentiellement ; une cible
   nouvelle est **à l'essai** : un premier passage sans rendement, ou dont
   les titres ne passent pas la porte d'intitulé, la rétrograde.
4. **Un socle** de métiers très demandés garantit un corpus JobSpy avant la
   première demande.

Ce que ce module ne fait pas : parler aux sites, ni connaître leurs
limites de débit (le collecteur les tient) ; ni chercher en direct pour
une personne — **une conversation n'interroge jamais une source externe**,
elle lit l'index, et au plus rend une cible due.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from karria.modules.offers.domain.criteria import SearchCriteria
from karria.modules.offers.domain.normalization import fold
from karria.modules.offers.domain.offers import ContractType
from karria.modules.offers.domain.titles import TitleMatcher, title_tokens


class TargetOrigin(StrEnum):
    """D'où vient une cible de collecte."""

    SEED = "seed"
    DEMAND = "demand"


class TargetState(StrEnum):
    """Où en est une cible dans sa vie."""

    #: Nouvelle : un premier passage décide.
    PROBATION = "probation"
    ACTIVE = "active"
    #: Muette ou hors sujet au premier passage : cadence longue.
    DEMOTED = "demoted"
    #: Plus demandée.
    RETIRED = "retired"


#: Le lieu « tout le pays ».
COUNTRY_LOCATION: Final = "France"
COUNTRY_KEY: Final = "fr"
#: Cibles confiées au collecteur par passage.
TARGETS_PER_RUN: Final = 30
#: Cadence de base : une cible demandée toutes les six heures, le socle
#: une fois par jour. La cadence réelle s'adapte au rendement (`interval_for`).
DEMAND_INTERVAL: Final = timedelta(hours=6)
SEED_INTERVAL: Final = timedelta(hours=24)
MIN_INTERVAL: Final = timedelta(hours=1)
MAX_INTERVAL: Final = timedelta(days=7)
#: Une cible confiée au collecteur n'est pas reproposée avant ce délai.
TARGET_LEASE: Final = timedelta(minutes=30)
#: Une cible que plus personne ne demande s'éteint après quinze jours.
DEMAND_TTL: Final = timedelta(days=15)
#: Sous ce nombre de résultats, une recherche rend ses cibles dues tout de
#: suite : le corpus ne couvre pas la demande, la collecte s'en charge.
THIN_RESULTS: Final = 10
#: Une recherche maigre pèse dans la priorité pendant ce délai.
GAP_TTL: Final = timedelta(hours=24)
#: Demi-vie du poids d'une personne dans la demande : une recherche d'il y
#: a deux semaines compte moitié moins qu'une d'aujourd'hui.
DEMAND_HALF_LIFE: Final = timedelta(days=14)
#: Lissage exponentiel du rendement (offres nouvelles par passage).
YIELD_SMOOTHING: Final = 0.3
#: Rendement à partir duquel la cadence est à son plus court.
YIELD_SATURATION: Final = 20.0
#: Part des titres qui doivent passer la porte d'intitulé au premier
#: passage, sinon la cible cherche autre chose que son métier…
PROBATION_MIN_GATE_RATE: Final = 0.3
#: … sauf si elle rapporte quand même : mesuré le 2026-10-22, « caissière,
#: Lille » rendait 49 annonces titrées « Hôte de caisse » (porte à 0 %) qui
#: sont exactement ce que la personne cherche sous un autre nom. Une cible
#: qui apporte autant d'offres nouvelles garde sa place ; la porte à 0 %
#: reste un signal (journalisé) pour la boucle des synonymes.
PROBATION_MIN_NEW: Final = 5
#: Au-delà de cette part rapportée par France Travail, la cible JobSpy est
#: redondante : sa priorité baisse.
OVERLAP_REDUNDANT: Final = 0.8
#: Poids de la priorité.
WEIGHT_DEMAND: Final = 1.0
WEIGHT_GAP: Final = 2.0
WEIGHT_YIELD: Final = 1.0
WEIGHT_STALENESS: Final = 1.0
WEIGHT_OVERLAP: Final = 1.0
#: Bornes d'un terme de collecte : court, un métier, jamais une phrase.
TERM_MAX_WORDS: Final = 4
TERM_MAX_LENGTH: Final = 40
TERMS_PER_USER: Final = 3
#: Un terme réduit à cela ne dit aucun métier : refusé.
GENERIC_TERMS: Final[frozenset[str]] = frozenset(
    {
        "emploi", "job", "travail", "poste", "cdi", "cdd", "interim", "alternance", "stage",
        "mission", "offre", "recrutement", "temps plein", "temps partiel", "urgent", "h/f",
    }
)  # fmt: skip
_CONTRACT_PREFIX: Final[dict[ContractType, str]] = {
    ContractType.ALTERNANCE: "alternance",
    ContractType.STAGE: "stage",
}
_WORD: Final = re.compile(r"[a-z0-9+#]+")
#: Un contrat dit en tête de terme (« alternance développeur », « stage en
#: marketing ») : il appartient à la cible, pas au terme.
_CONTRACT_HEAD: Final = re.compile(
    r"^(?:en\s+)?(?:alternance|stage|apprentissage)\s+(?:en\s+)?", re.IGNORECASE
)
#: Ponctuation et tirets en tête ou en queue d'un terme.
_EDGES: Final = re.compile(r"^[\s\-,;:.]+|[\s\-,;:.]+$")


@dataclass(frozen=True, slots=True)
class ScrapeTarget:
    """Une requête à soumettre à un site : un terme court, un lieu, un contrat.

    C'est **tout** ce qu'un site reçoit d'une personne : jamais sa phrase.
    """

    search_term: str
    location: str
    origin: TargetOrigin
    weight: int = 1
    #: Rayon autour du lieu, en kilomètres ; `None` : celui du site.
    distance_km: int | None = None
    #: Le département (ou `fr`) : la granularité du regroupement.
    location_key: str = COUNTRY_KEY
    #: Le contrat demandé quand il change la requête (alternance, stage).
    contract: ContractType | None = None

    @property
    def key(self) -> str:
        """Identité canonique : terme plié x département x contrat."""
        contract = self.contract.value if self.contract else "-"
        return f"{fold(self.search_term)}|{self.location_key}|{contract}"

    @property
    def query_term(self) -> str:
        """Le terme tel qu'il part au site : préfixé du contrat quand il le faut.

        Les sites ne filtrent pas l'alternance de façon fiable ; le mot dans
        la requête, si : « alternance développeur » rend des alternances.
        """
        prefix = _CONTRACT_PREFIX.get(self.contract) if self.contract else None
        if prefix is None or fold(self.search_term).startswith(prefix):
            return self.search_term
        return f"{prefix} {self.search_term}"


@dataclass(frozen=True, slots=True)
class TargetStats:
    """Ce que les passages ont appris d'une cible."""

    state: TargetState = TargetState.PROBATION
    scrapes: int = 0
    #: Offres nouvelles par passage, lissées.
    yield_ema: float = 0.0
    #: Passages consécutifs sans rien de nouveau.
    zero_streak: int = 0
    #: Part des annonces déjà rapportées par France Travail, lissée.
    overlap_ema: float = 0.0
    #: Titres qui n'ont pas passé la porte d'intitulé, en cumul.
    gate_rejects: int = 0
    last_found: int = 0


@dataclass(frozen=True, slots=True)
class ScrapeOutcome:
    """Ce qu'un passage sur une cible a rapporté, vu de l'ingestion."""

    found: int
    new: int
    #: Annonces du lot qui doublonnaient une offre France Travail.
    overlap_france_travail: int = 0
    #: Part des titres du lot qui passent la porte d'intitulé du terme.
    gate_rate: float | None = None


@dataclass(frozen=True, slots=True)
class TargetDemand:
    """Une personne demande une cible, depuis cet instant."""

    user_id: object
    target_key: str
    since: datetime


# ---------------------------------------------------------------------
# Les termes : courts, canoniques, validés
# ---------------------------------------------------------------------


def clean_term(term: str) -> str | None:
    """Un terme de collecte acceptable, ou `None` : court, un métier, pas une phrase.

    Les contraintes sont des bornes de **forme** (1 à 4 mots, 40
    caractères, pas un mot générique) ; la vérification de **fond** — le
    terme désigne un métier que des annonces titrent vraiment — se fait
    contre le vocabulaire de l'index (`validate_terms`).
    """
    cleaned = _EDGES.sub("", _CONTRACT_HEAD.sub("", " ".join(term.split())))
    words = _WORD.findall(fold(cleaned))
    if not cleaned or not words or len(words) > TERM_MAX_WORDS or len(cleaned) > TERM_MAX_LENGTH:
        return None
    if fold(cleaned) in GENERIC_TERMS or not title_tokens(cleaned):
        return None
    return cleaned


def candidate_terms(criteria: SearchCriteria) -> list[str]:
    """Les termes de collecte que la demande propose : `search_terms`, sinon les intitulés."""
    proposed = criteria.search_terms or criteria.job_titles[:2]
    seen: dict[str, str] = {}
    for term in proposed:
        cleaned = clean_term(term)
        if cleaned is not None and fold(cleaned) not in seen:
            seen[fold(cleaned)] = cleaned
    return list(seen.values())[:TERMS_PER_USER]


def validate_terms(terms: Sequence[str], titles_by_term: Mapping[str, Sequence[str]]) -> list[str]:
    """Garde les termes qu'au moins un titre réel de l'index confirme (porte d'intitulé).

    `titles_by_term` : pour chaque terme, un échantillon de titres que
    l'index rapproche de lui. Un terme qu'aucun titre ne confirme chercherait
    un métier qui n'existe pas sous ce nom : on ne crée pas la cible.
    """
    kept: list[str] = []
    for term in terms:
        matcher = TitleMatcher.of([term])
        if any(matcher.match(title) is not None for title in titles_by_term.get(term, ())):
            kept.append(term)
    return kept


def gate_rate(term: str, titles: Iterable[str]) -> float | None:
    """La part des titres qui disent le métier du terme ; `None` sans titre."""
    matcher = TitleMatcher.of([term])
    counted = passed = 0
    for title in titles:
        counted += 1
        if matcher.match(title) is not None:
            passed += 1
    return passed / counted if counted else None


# ---------------------------------------------------------------------
# Des critères aux cibles
# ---------------------------------------------------------------------


def contract_for_targets(criteria: SearchCriteria) -> ContractType | None:
    """Le contrat qui change la requête : alternance ou stage seuls."""
    if criteria.alternance_only:
        return ContractType.ALTERNANCE
    if criteria.contract_types == [ContractType.STAGE]:
        return ContractType.STAGE
    return None


def location_for_targets(criteria: SearchCriteria) -> tuple[str, str, int | None]:
    """(lieu à demander au site, clé de regroupement, rayon)."""
    location = criteria.location
    if location is None or criteria.wants_remote_only or criteria.whole_france:
        return COUNTRY_LOCATION, COUNTRY_KEY, None
    if not location.label.strip():
        return COUNTRY_LOCATION, COUNTRY_KEY, None
    if location.department_codes and not location.is_geocoded:
        # Une zone sans point : la cible porte le libellé et le premier département.
        return location.label, location.department_codes[0], None
    key = location.department_code or COUNTRY_KEY
    # Le site reçoit la ville que le géocodeur a nommée (« Lille »), jamais
    # la tournure de la personne (« Lille et alentours »).
    place = location.city or location.label
    return place, key, location.radius_km if location.is_geocoded else None


def targets_of(criteria: SearchCriteria, terms: Sequence[str] | None = None) -> list[ScrapeTarget]:
    """Les cibles d'une personne : un terme court x son département x son contrat."""
    location, key, distance = location_for_targets(criteria)
    contract = contract_for_targets(criteria)
    return [
        ScrapeTarget(
            search_term=term,
            location=location,
            origin=TargetOrigin.DEMAND,
            distance_km=distance,
            location_key=key,
            contract=contract,
        )
        for term in (candidate_terms(criteria) if terms is None else terms)
    ]


def targets_from_criteria(all_criteria: Iterable[SearchCriteria]) -> list[ScrapeTarget]:
    """Les cibles demandées par tout le monde, regroupées, pesées par le nombre de personnes.

    Deux personnes du même département et du même métier font une cible ; le
    lieu gardé est celui de la première, le rayon le plus grand des deux.
    """
    grouped: dict[str, ScrapeTarget] = {}
    for criteria in all_criteria:
        for target in targets_of(criteria):
            existing = grouped.get(target.key)
            if existing is None:
                grouped[target.key] = target
                continue
            grouped[target.key] = replace(
                existing,
                weight=existing.weight + 1,
                distance_km=max(existing.distance_km or 0, target.distance_km or 0) or None,
            )
    return sorted(grouped.values(), key=lambda t: (-t.weight, t.key))


#: Le socle : les métiers les plus recherchés en France, pour que le fil
#: « toutes les offres » ait du sens dès le premier jour.
SEED_TERMS: Final[tuple[str, ...]] = (
    "serveur",
    "cuisinier",
    "vendeur",
    "caissier",
    "préparateur de commandes",
    "chauffeur livreur",
    "agent d'entretien",
    "aide-soignant",
    "infirmier",
    "auxiliaire de vie",
    "assistant administratif",
    "comptable",
    "commercial",
    "chargé de clientèle",
    "téléconseiller",
    "développeur",
    "data analyst",
    "chef de projet",
    "technicien de maintenance",
    "électricien",
    "manutentionnaire",
    "cariste",
    "agent de sécurité",
    "animateur",
    "enseignant",
    "ressources humaines",
    "marketing",
    "alternance",
    "stage",
    "job étudiant",
)


def seed_targets() -> list[ScrapeTarget]:
    """Les cibles du socle, sur tout le pays."""
    return [
        ScrapeTarget(search_term=term, location=COUNTRY_LOCATION, origin=TargetOrigin.SEED)
        for term in SEED_TERMS
    ]


# ---------------------------------------------------------------------
# La demande, la priorité, la cadence
# ---------------------------------------------------------------------


def demand_weight(since: datetime, now: datetime) -> float:
    """Le poids d'une personne : 1 aujourd'hui, ½ après la demi-vie, puis décroissant."""
    age = max((now - since).total_seconds(), 0.0)
    return float(0.5 ** (age / DEMAND_HALF_LIFE.total_seconds()))


def demand_scores(demands: Iterable[TargetDemand], now: datetime) -> dict[str, float]:
    """La demande de chaque cible : la somme des poids de ses personnes."""
    scores: dict[str, float] = {}
    for demand in demands:
        scores[demand.target_key] = scores.get(demand.target_key, 0.0) + demand_weight(
            demand.since, now
        )
    return scores


def interval_for(
    origin: TargetOrigin, stats: TargetStats = TargetStats(state=TargetState.ACTIVE)
) -> timedelta:
    """La cadence : la base de l'origine, raccourcie par le rendement, allongée par le silence.

    Une cible à 20 offres nouvelles par passage tourne à l'heure ; une cible
    muette double son attente à chaque passage vide (bornée à sept jours) ;
    une cible rétrogradée part de quatre fois la base.
    """
    base = DEMAND_INTERVAL if origin is TargetOrigin.DEMAND else SEED_INTERVAL
    if stats.state is TargetState.DEMOTED:
        base *= 4
    speedup = 1.0 + min(stats.yield_ema, YIELD_SATURATION) / YIELD_SATURATION * (
        base / MIN_INTERVAL - 1
    )
    interval: timedelta = base / speedup
    interval *= 2 ** min(stats.zero_streak, 8)
    bounded: timedelta = max(MIN_INTERVAL, min(interval, MAX_INTERVAL))
    return bounded


def after_scrape(stats: TargetStats, outcome: ScrapeOutcome) -> TargetStats:
    """Les statistiques après un passage : rendement lissé, silence, essai tranché."""
    yield_ema = (
        float(outcome.new)
        if stats.scrapes == 0
        else (1 - YIELD_SMOOTHING) * stats.yield_ema + YIELD_SMOOTHING * outcome.new
    )
    overlap = outcome.overlap_france_travail / outcome.found if outcome.found else 0.0
    overlap_ema = (
        overlap
        if stats.scrapes == 0
        else (1 - YIELD_SMOOTHING) * stats.overlap_ema + YIELD_SMOOTHING * overlap
    )
    rejects = stats.gate_rejects
    if outcome.gate_rate is not None:
        rejects += round(outcome.found * (1 - outcome.gate_rate))
    state = stats.state
    if state is TargetState.PROBATION:
        off_target = (
            outcome.gate_rate is not None
            and outcome.gate_rate < PROBATION_MIN_GATE_RATE
            and outcome.new < PROBATION_MIN_NEW
        )
        state = TargetState.DEMOTED if outcome.found == 0 or off_target else TargetState.ACTIVE
    elif state is TargetState.DEMOTED and outcome.new > 0:
        state = TargetState.ACTIVE
    return TargetStats(
        state=state,
        scrapes=stats.scrapes + 1,
        yield_ema=round(yield_ema, 3),
        zero_streak=0 if outcome.new > 0 else stats.zero_streak + 1,
        overlap_ema=round(overlap_ema, 3),
        gate_rejects=rejects,
        last_found=outcome.found,
    )


def priority(
    target: ScrapeTarget,
    stats: TargetStats,
    *,
    demand: float,
    thin_since: datetime | None,
    last_scraped_at: datetime | None,
    now: datetime,
) -> float:
    """Le score qui ordonne les cibles dues : plus il est haut, plus tôt on collecte.

    - demande : `log(1 + poids des personnes)` — mille personnes ne valent pas
      mille fois une, mais toujours plus ;
    - manque : une recherche maigre récente, le signal le plus urgent ;
    - rendement : ce que la cible rapporte, normalisé ;
    - ancienneté : le temps écoulé rapporté à la cadence due ;
    - redondance : ce que France Travail rapporte déjà, en négatif.
    """
    gap = 1.0 if thin_since is not None and now - thin_since <= GAP_TTL else 0.0
    yield_term = min(stats.yield_ema, YIELD_SATURATION) / YIELD_SATURATION
    interval = interval_for(target.origin, stats)
    staleness = 2.0 if last_scraped_at is None else min((now - last_scraped_at) / interval, 2.0)
    overlap = max(stats.overlap_ema - OVERLAP_REDUNDANT, 0.0) / (1 - OVERLAP_REDUNDANT)
    return round(
        WEIGHT_DEMAND * math.log1p(demand)
        + WEIGHT_GAP * gap
        + WEIGHT_YIELD * yield_term
        + WEIGHT_STALENESS * staleness
        - WEIGHT_OVERLAP * overlap,
        4,
    )
