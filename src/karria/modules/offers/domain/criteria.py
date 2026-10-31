"""Les critères de recherche d'une personne — ce que Karria a compris de sa demande.

Contrat
-------
Les critères sont **la** donnée que la conversation produit, que le fil
consomme, que les alertes surveillent et qui pilote la collecte. Ils sont
un modèle Pydantic (comme les chapitres du CV, ADR-0013 §4) : la même
forme borne la sortie de Gemini, valide ce que l'app envoie et relit la
base.

Trois questions auxquelles ils répondent :

- `is_searchable` — peut-on chercher ? Il faut un **métier** et un **lieu**
  (ou « toute la France », ou « en télétravail »). Tout le reste est
  facultatif : un contrat non précisé veut dire « tous ».
- `accepts(offer)` — cette offre respecte-t-elle les contraintes **dures** ?
  C'est le filtre de la recherche et celui des alertes, une seule règle.
- `summary` — comment le dire en une ligne à la personne.
"""

from __future__ import annotations

import hashlib
import math
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from karria.modules.offers.domain.normalization import fold
from karria.modules.offers.domain.offers import (
    ContractType,
    ExperienceLevel,
    Offer,
    RemoteMode,
    Schedule,
)

#: Rayon par défaut autour d'une ville, en kilomètres, quand la personne
#: n'en dit rien. Trente kilomètres : un trajet quotidien raisonnable.
DEFAULT_RADIUS_KM: Final = 30
#: Les contrats qu'une annonce dit toujours : leur absence élimine.
STRICT_CONTRACTS: Final[frozenset[ContractType]] = frozenset(
    {ContractType.ALTERNANCE, ContractType.STAGE}
)
MAX_RADIUS_KM: Final = 200
#: Bornes de forme d'un terme de collecte (le fond : `domain/steering.py`).
SEARCH_TERMS_MAX: Final = 3
SEARCH_TERM_MAX_LENGTH: Final = 40
_EARTH_RADIUS_KM: Final = 6_371.0


class LocationCriteria(BaseModel):
    """Où la personne cherche : un point et un rayon, et/ou des départements."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description="Le lieu tel que la personne l'a dit : « Paris », « Lyon et alentours »."
    )
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: int = Field(default=DEFAULT_RADIUS_KM, ge=1, le=MAX_RADIUS_KM)
    department_code: str | None = Field(
        default=None,
        description="Le département du point, posé par le géocodage. Ne pas remplir.",
    )
    city: str | None = Field(
        default=None, description="La commune du point, posée par le géocodage. Ne pas remplir."
    )
    department_codes: list[str] = Field(
        default_factory=list,
        description="Codes de départements (« 75 », « 2A », « 974 ») quand la personne vise une zone plutôt qu'une ville.",
    )

    @field_validator("department_codes")
    @classmethod
    def _normalise_departments(cls, codes: list[str]) -> list[str]:
        cleaned = {code.strip().upper() for code in codes if code.strip()}
        return sorted(cleaned)

    @property
    def is_geocoded(self) -> bool:
        """A-t-on un point ?"""
        return self.latitude is not None and self.longitude is not None

    @property
    def is_usable(self) -> bool:
        """Peut-on en faire un filtre ?"""
        return self.is_geocoded or bool(self.department_codes)


class SearchCriteria(BaseModel):
    """La demande comprise."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="La demande de la personne, reformulée en une phrase simple.")
    job_titles: list[str] = Field(
        default_factory=list,
        description="Intitulés de poste visés, au masculin et au féminin quand ils diffèrent.",
    )
    excluded_titles: list[str] = Field(
        default_factory=list,
        description=(
            "Intitulés voisins à EXCLURE : les autres niveaux et les métiers proches qui partagent "
            "des mots avec les intitulés visés (« chef cuisinier », « second de cuisine », « commis de "
            "cuisine » pour « cuisinier »), sauf si la personne les demande. Au plus 12."
        ),
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Termes qui doivent apparaître pour qu'une offre soit pertinente : compétences, domaines, sigles.",
    )
    semantic_query: str = Field(
        default="",
        description="Description dense du poste idéal, comme une annonce l'écrirait : métier, missions, environnement.",
    )
    search_terms: list[str] = Field(
        default_factory=list,
        description=(
            "Termes de collecte : un par métier distinct cherché, 1 à 4 mots, tels qu'on les "
            "taperait dans la barre de recherche d'un site d'offres (« serveur », « chef de rang »). "
            "Jamais une phrase, jamais une préférence. Au plus 3."
        ),
    )
    preferences: list[str] = Field(
        default_factory=list,
        description=(
            "Ce que la personne souhaite sans que ce soit un filtre : ambiance, valeurs, taille "
            "d'entreprise, rythme, mobilité (« pas trop de stress », « petite équipe »). "
            "Sert au dialogue et au classement, jamais à la collecte."
        ),
    )
    location: LocationCriteria | None = None
    whole_france: bool = Field(default=False, description="La personne cherche partout en France.")
    contract_types: list[ContractType] = Field(default_factory=list, description="Vide : tous.")
    remote: list[RemoteMode] = Field(
        default_factory=list, description="Modes acceptés. Vide : tous."
    )
    schedule: Schedule | None = None
    max_experience: ExperienceLevel | None = Field(
        default=None,
        description="Le niveau de la personne : une offre qui exige davantage est écartée.",
    )
    salary_min_monthly: int | None = Field(default=None, ge=0, le=100_000)

    @field_validator("job_titles", "excluded_titles", "keywords", "preferences")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for value in values:
            cleaned = " ".join(value.split())
            if cleaned and fold(cleaned) not in seen:
                seen[fold(cleaned)] = cleaned
        return list(seen.values())[:12]

    @field_validator("search_terms")
    @classmethod
    def _short_terms(cls, values: list[str]) -> list[str]:
        """Courts et peu nombreux : la forme est contrôlée ici, le fond par le planificateur."""
        seen: dict[str, str] = {}
        for value in values:
            cleaned = " ".join(value.split())[:SEARCH_TERM_MAX_LENGTH]
            if cleaned and fold(cleaned) not in seen:
                seen[fold(cleaned)] = cleaned
        return list(seen.values())[:SEARCH_TERMS_MAX]

    @model_validator(mode="after")
    def _titles_from_terms(self) -> Self:
        """Un modèle qui ne remplit que `search_terms` a quand même dit le métier."""
        if not self.job_titles and self.search_terms:
            self.job_titles = list(self.search_terms)
        return self

    @field_validator("contract_types")
    @classmethod
    def _unique_contracts(cls, values: list[ContractType]) -> list[ContractType]:
        return list(dict.fromkeys(values))

    @field_validator("remote")
    @classmethod
    def _unique_remote(cls, values: list[RemoteMode]) -> list[RemoteMode]:
        return list(dict.fromkeys(values))

    # --- Ce qui manque ------------------------------------------------

    @property
    def wants_remote_only(self) -> bool:
        """La personne ne veut que du télétravail complet : le lieu n'importe plus."""
        return self.remote == [RemoteMode.REMOTE]

    @property
    def has_place(self) -> bool:
        """Sait-on où chercher ?"""
        return (
            self.whole_france
            or self.wants_remote_only
            or (self.location is not None and self.location.is_usable)
        )

    def missing(self) -> list[str]:
        """Ce qu'il faut encore demander avant de chercher, dans l'ordre."""
        gaps: list[str] = []
        if not self.job_titles:
            gaps.append("job")
        if not self.has_place:
            gaps.append("location")
        return gaps

    @property
    def is_searchable(self) -> bool:
        """Un métier et un lieu : on peut chercher."""
        return not self.missing()

    @property
    def alternance_only(self) -> bool:
        """L'alternance est le seul contrat voulu."""
        return self.contract_types == [ContractType.ALTERNANCE]

    # --- Le filtre dur ------------------------------------------------

    @property
    def reference_point(self) -> tuple[float, float] | None:
        """Le point d'où mesurer « Plus proches » : le lieu géocodé de la recherche.

        `None` pour une recherche sans point (« Toute la France », télétravail
        seul, départements sans centre) : le tri par distance n'y a pas de sens.
        """
        if self.location is None or not self.location.is_geocoded:
            return None
        return (self.location.latitude or 0.0, self.location.longitude or 0.0)

    def distance_km(self, latitude: float, longitude: float) -> float | None:
        """Distance au point cherché, ou `None` sans point."""
        if self.location is None or not self.location.is_geocoded:
            return None
        return haversine_km(
            self.location.latitude or 0.0, self.location.longitude or 0.0, latitude, longitude
        )

    @property
    def remote_anywhere(self) -> bool:
        """La personne accepte-t-elle une offre en télétravail complet, où qu'elle soit ?

        Seulement si elle a parlé de télétravail. Mesuré le 2026-10-22 :
        une caisse en alternance à Venerque (31) remontait pour « caissière
        à Lille » parce que l'annonce se disait « télétravail complet » (la
        part école) ; qui demande Lille veut Lille.
        """
        return RemoteMode.REMOTE in self.remote

    @property
    def strict_contracts(self) -> bool:
        """L'absence de contrat dans l'offre élimine-t-elle ?

        Oui quand la personne ne veut que de l'alternance ou du stage : une
        annonce d'alternance ou de stage le dit toujours, une annonce muette
        est presque toujours un emploi. Mesuré le 2026-10-22 : pour
        « chargé de recrutement en alternance », quatre offres sur dix
        n'avaient pas de contrat lisible, et aucune n'était une alternance.
        Pour un CDI, l'absence ne prouve rien (JobSpy perd la mention).
        """
        return bool(self.contract_types) and set(self.contract_types) <= STRICT_CONTRACTS

    @property
    def strict_schedule(self) -> bool:
        """L'absence de temps de travail dans l'offre élimine-t-elle ?

        Oui pour « temps partiel » : une annonce à temps partiel le dit
        toujours (la durée hebdomadaire est une mention obligatoire, et
        c'est son argument) ; une annonce muette est presque toujours à
        temps plein — mesuré le 2026-10-22 sur France Travail, 85 % des
        offres sont à temps plein. Non pour « temps plein » : l'absence ne
        prouve rien, JobSpy perd souvent la mention.
        """
        return self.schedule is Schedule.PART_TIME

    def accepts_location(self, offer: Offer) -> bool:
        """Le lieu de l'offre convient-il ?"""
        if self.whole_france or self.wants_remote_only:
            return True
        if offer.remote is RemoteMode.REMOTE and self.remote_anywhere:
            return True
        location = self.location
        if location is None or not location.is_usable:
            return True
        place = offer.location
        if place is None:
            return False
        if location.department_codes and place.department_code in location.department_codes:
            return True
        distance = (
            self.distance_km(place.latitude or 0.0, place.longitude or 0.0)
            if place.is_geocoded
            else None
        )
        return distance is not None and distance <= location.radius_km

    def accepts(self, offer: Offer) -> bool:
        """Les contraintes dures, les mêmes pour le fil et pour les alertes.

        Une information **absente** de l'offre n'élimine pas : on ne sait
        pas, on laisse passer. Une information **contraire** élimine. Trois
        exceptions, assumées, pour ce qu'une annonce dit toujours quand
        c'est vrai : « uniquement en télétravail » écarte une offre muette
        (presque toujours sur site) ; alternance ou stage écartent une offre
        sans contrat (`strict_contracts`) ; « temps partiel » écarte une
        offre sans temps de travail (`strict_schedule`).
        """
        return offer.indexable and self.accepts_location(offer) and self.accepts_terms(offer)

    def accepts_terms(self, offer: Offer) -> bool:
        """Contrat, télétravail, temps, expérience, salaire."""
        ceiling = offer.salary.max_monthly or offer.salary.min_monthly
        checks = (
            not self.contract_types
            or (offer.contract_type is None and not self.strict_contracts)
            or offer.contract_type in self.contract_types,
            not self.wants_remote_only or offer.remote is RemoteMode.REMOTE,
            not self.remote or offer.remote is None or offer.remote in self.remote,
            self.schedule is None
            or (offer.schedule is None and not self.strict_schedule)
            or offer.schedule is self.schedule,
            self.max_experience is None
            or offer.experience is None
            or offer.experience.rank <= self.max_experience.rank,
            not self.salary_min_monthly or ceiling is None or ceiling >= self.salary_min_monthly,
        )
        return all(checks)

    # --- Présentation et dérivés --------------------------------------

    def summary(self) -> str:
        """Une ligne : « Serveur · Paris (30 km) · CDI, CDD »."""
        parts: list[str] = []
        if self.job_titles:
            parts.append(self.job_titles[0])
        if self.wants_remote_only:
            parts.append("en télétravail")
        elif self.whole_france:
            parts.append("toute la France")
        elif self.location is not None:
            radius = f" ({self.location.radius_km} km)" if self.location.is_geocoded else ""
            parts.append(f"{self.location.label}{radius}")
        if self.contract_types:
            parts.append(", ".join(kind.label for kind in self.contract_types))
        if self.salary_min_monthly:
            parts.append(f"≥ {self.salary_min_monthly} €/mois")
        return " · ".join(parts) or self.query

    def lexical_terms(self) -> list[str]:
        """Les termes de la branche lexicale : intitulés d'abord, mots-clés ensuite."""
        return [*self.job_titles, *self.keywords]

    def dense_text(self) -> str:
        """Le texte plongé pour la branche sémantique."""
        return self.semantic_query or " ; ".join([*self.job_titles, self.query])

    def fingerprint(self) -> str:
        """Change quand ce qui influence la recherche change."""
        payload = self.model_dump_json(exclude={"query", "preferences", "search_terms"})
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique entre deux points, en kilomètres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
