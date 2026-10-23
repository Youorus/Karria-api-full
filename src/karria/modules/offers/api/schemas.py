"""Contrat HTTP du module `offers` — `karria_app/docs/contrat-api-recherche.md`.

Les schémas sont distincts du domaine, délibérément : tout changement
cassant du contrat se voit ici, à l'endroit où il l'est. Les noms de la V1
que l'app lit déjà sont gardés (`job_offer`, `score_normalized`,
`match_quality`, `company.logo`, `city.name`, `contract_type.label`,
`salary_raw`, `saved`, `applied`, `detail_opened`) ; ce qui change est
consigné dans le contrat, section « Ce que l'app doit changer ».
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from karria.modules.offers.application import (
    ConversationTurn,
    FeedFilterOptions,
    FeedItem,
    FeedPage,
    StoredCriteria,
)
from karria.modules.offers.domain import (
    CONTRACT_LABELS,
    MESSAGE_MAX_LENGTH,
    ContractType,
    DialogueStatus,
    ExperienceLevel,
    FeedMode,
    FeedOrigin,
    Intent,
    KarriaMemory,
    LocationCriteria,
    MatchQuality,
    MemoryFacts,
    Offer,
    RemoteMode,
    Schedule,
    SearchCriteria,
)

#: Longueur de l'extrait affiché sur une carte du fil.
EXCERPT_LENGTH = 280
#: Une offre publiée depuis moins longtemps, et jamais vue, porte le badge « Nouvelle ».
NEW_WITHIN_HOURS = 48
SEEN_BATCH_MAX = 100

# ---------------------------------------------------------------------
# L'offre
# ---------------------------------------------------------------------


class CompanyResponse(BaseModel):
    """L'employeur."""

    name: str
    logo: str | None = Field(default=None, description="URL du logo chez la source, telle quelle.")
    website: str | None = None


class CityResponse(BaseModel):
    """La commune du poste."""

    name: str
    postal_code: str | None


class ContractTypeResponse(BaseModel):
    """Le contrat."""

    code: ContractType
    label: str


class JobOfferResponse(BaseModel):
    """Une offre. `description` n'est rempli que par `GET /job-matches/{id}`."""

    id: UUID
    title: str
    summary: None = Field(
        default=None,
        description="Réservé à l'analyse de Karria (pas encore servie) : toujours `null`. "
        "L'app ne doit plus y lire la description.",
    )
    excerpt: str = Field(description="Les premières lignes de l'annonce, sur une ligne.")
    description: str | None = Field(
        default=None, description="Le texte de l'annonce (Markdown léger) — détail seulement."
    )
    company: CompanyResponse | None
    city: CityResponse | None
    location_label: str | None = Field(description="À afficher : « Lyon 3e », « Nanterre (92) ».")
    department_code: str | None
    latitude: float | None
    longitude: float | None
    contract_type: ContractTypeResponse | None
    remote: RemoteMode | None = Field(
        description="`sur_site`, `hybride`, `teletravail`, ou `null` si l'annonce ne le dit pas."
    )
    schedule: Schedule | None
    experience: ExperienceLevel | None = Field(description="Expérience demandée, ou `null`.")
    salary_raw: str | None = Field(description="Le salaire tel que l'annonce l'écrit.")
    salary_min_monthly: int | None = Field(description="Brut mensuel en euros, si compris.")
    salary_max_monthly: int | None
    published_at: datetime
    is_alternance: bool
    apply_url: str | None
    origin_url: str | None
    source: str
    is_active: bool = Field(
        description="Faux : l'offre a expiré ; elle reste lisible si elle est sauvegardée."
    )

    @classmethod
    def from_domain(cls, offer: Offer, *, detail: bool = False) -> JobOfferResponse:
        """Projette."""
        location = offer.location
        return cls(
            id=offer.id,
            title=offer.title,
            excerpt=excerpt_of(offer.description),
            description=offer.description if detail else None,
            company=CompanyResponse(
                name=offer.company.name,
                logo=offer.company.logo_url,
                website=offer.company.website,
            )
            if offer.company
            else None,
            city=CityResponse(name=location.city, postal_code=location.postal_code)
            if location is not None and location.city
            else None,
            location_label=location.label if location else None,
            department_code=location.department_code if location else None,
            latitude=location.latitude if location else None,
            longitude=location.longitude if location else None,
            contract_type=ContractTypeResponse(
                code=offer.contract_type, label=CONTRACT_LABELS[offer.contract_type]
            )
            if offer.contract_type
            else None,
            remote=offer.remote,
            schedule=offer.schedule,
            experience=offer.experience,
            salary_raw=offer.salary.raw,
            salary_min_monthly=offer.salary.min_monthly,
            salary_max_monthly=offer.salary.max_monthly,
            published_at=offer.published_at,
            is_alternance=offer.is_alternance,
            apply_url=offer.apply_url,
            origin_url=offer.origin_url,
            source=offer.source.value,
            is_active=offer.is_active,
        )


def excerpt_of(description: str) -> str:
    """Les premières lignes, sur une ligne, coupées sur un mot."""
    flat = " ".join(description.split())
    if len(flat) <= EXCERPT_LENGTH:
        return flat
    return flat[:EXCERPT_LENGTH].rsplit(" ", 1)[0] + "…"


class JobMatchResponse(BaseModel):
    """Une offre dans le fil d'une personne : sa place, son état, l'offre."""

    id: UUID = Field(description="L'identifiant de l'**offre** : une personne la voit une fois.")
    job_offer_id: UUID
    score_normalized: float | None = Field(
        description="Pertinence dans [0, 1] (intitulé demandé, rang dans la recherche) ; `null` hors recherche."
    )
    match_quality: MatchQuality | None = Field(
        description="`high` (« Très alignée »), `medium`, `low` (« À explorer ») ; `null` hors recherche."
    )
    reasons: list[str] = Field(
        description="Ce que l'offre confirme de la demande : « CDI », « Lyon · 4 km »."
    )
    origin: FeedOrigin | None = Field(
        description="`search` (la recherche), `alert` (arrivée depuis), `null` hors recherche."
    )
    is_new: bool = Field(description=f"Publiée il y a moins de {NEW_WITHIN_HOURS} h et jamais vue.")
    feed_seen: bool
    detail_opened: bool
    saved: bool
    applied: bool
    dismissed: bool
    job_offer: JobOfferResponse

    @classmethod
    def from_item(cls, item: FeedItem, *, now: datetime, detail: bool = False) -> JobMatchResponse:
        """Projette."""
        entry, state, offer = item.entry, item.state, item.offer
        fresh = (now - offer.published_at).total_seconds() < NEW_WITHIN_HOURS * 3600
        return cls(
            id=offer.id,
            job_offer_id=offer.id,
            score_normalized=entry.score if entry else None,
            match_quality=entry.quality if entry else None,
            reasons=list(entry.reasons) if entry else [],
            origin=entry.origin if entry else None,
            is_new=fresh and not state.seen,
            feed_seen=state.seen,
            detail_opened=state.opened,
            saved=state.saved,
            applied=state.applied,
            dismissed=state.dismissed,
            job_offer=JobOfferResponse.from_domain(offer, detail=detail),
        )


class FeedPageResponse(BaseModel):
    """Une page du fil."""

    items: list[JobMatchResponse]
    count: int = Field(description="Total d'offres correspondant aux filtres.")
    limit: int
    offset: int
    has_next: bool
    mode: FeedMode = Field(
        description="`all_offers` : pas de recherche, toutes les offres récentes. "
        "`search` : les offres retenues pour la recherche en cours."
    )
    criteria_summary: str | None = Field(
        description="La recherche en cours en une ligne (« Serveur · Paris (30 km) · CDI »)."
    )

    @classmethod
    def from_page(
        cls, page: FeedPage, *, limit: int, offset: int, now: datetime
    ) -> FeedPageResponse:
        """Projette."""
        return cls(
            items=[JobMatchResponse.from_item(item, now=now) for item in page.items],
            count=page.total,
            limit=limit,
            offset=offset,
            has_next=offset + len(page.items) < page.total,
            mode=page.mode,
            criteria_summary=page.criteria_summary,
        )


class FilterOptionResponse(BaseModel):
    """Une valeur de filtre."""

    value: str = Field(description="À renvoyer tel quel dans `contract_types` ou `cities`.")
    label: str
    count: int


class FilterOptionsResponse(BaseModel):
    """Les valeurs de filtre présentes dans le fil."""

    contract_types: list[FilterOptionResponse]
    cities: list[FilterOptionResponse]

    @classmethod
    def from_options(cls, options: FeedFilterOptions) -> FilterOptionsResponse:
        """Projette."""
        return cls(
            contract_types=[
                FilterOptionResponse(
                    value=code, label=CONTRACT_LABELS[ContractType(code)], count=count
                )
                for code, count in sorted(options.contract_types.items(), key=lambda kv: -kv[1])
            ],
            cities=[
                FilterOptionResponse(value=city, label=city, count=count)
                for city, count in options.cities.items()
            ],
        )


class SeenRequest(BaseModel):
    """Les offres que l'app vient d'afficher."""

    model_config = ConfigDict(extra="forbid")

    offer_ids: list[UUID] = Field(min_length=1, max_length=SEEN_BATCH_MAX)


class SeenResponse(BaseModel):
    """Combien sont nouvellement marquées vues."""

    updated: int


class InteractionRequest(BaseModel):
    """Ce que la personne vient de faire. Un ou plusieurs champs ; les absents ne changent rien."""

    model_config = ConfigDict(extra="forbid")

    saved: bool | None = None
    applied: bool | None = None
    dismissed: bool | None = Field(
        default=None, description="Vrai : l'offre quitte le fil et n'y revient plus."
    )
    detail_opened: Literal[True] | None = None
    link_clicked: Literal[True] | None = None


# ---------------------------------------------------------------------
# La recherche
# ---------------------------------------------------------------------


class LocationCriteriaResponse(BaseModel):
    """Où la personne cherche."""

    label: str
    latitude: float | None
    longitude: float | None
    radius_km: int
    department_codes: list[str]


class CriteriaResponse(BaseModel):
    """La recherche comprise par Karria, telle qu'elle est enregistrée."""

    query: str
    summary: str
    job_titles: list[str]
    keywords: list[str]
    location: LocationCriteriaResponse | None
    whole_france: bool
    contract_types: list[ContractType]
    remote: list[RemoteMode]
    schedule: Schedule | None
    max_experience: ExperienceLevel | None
    salary_min_monthly: int | None
    alerts_enabled: bool
    updated_at: datetime

    @classmethod
    def from_stored(cls, stored: StoredCriteria) -> CriteriaResponse:
        """Projette."""
        criteria = stored.criteria
        location = criteria.location
        return cls(
            query=criteria.query,
            summary=criteria.summary(),
            job_titles=criteria.job_titles,
            keywords=criteria.keywords,
            location=LocationCriteriaResponse(**location.model_dump()) if location else None,
            whole_france=criteria.whole_france,
            contract_types=criteria.contract_types,
            remote=criteria.remote,
            schedule=criteria.schedule,
            max_experience=criteria.max_experience,
            salary_min_monthly=criteria.salary_min_monthly,
            alerts_enabled=stored.alerts_enabled,
            updated_at=stored.updated_at,
        )


class CriteriaEnvelopeResponse(BaseModel):
    """Les critères courants, ou `null` s'il n'y a pas de recherche."""

    criteria: CriteriaResponse | None


class CriteriaRequest(BaseModel):
    """Des critères posés directement (édition depuis l'app), sans conversation."""

    model_config = ConfigDict(extra="forbid")

    job_titles: list[str] = Field(min_length=1, max_length=12)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    location: LocationCriteria | None = None
    whole_france: bool = False
    contract_types: list[ContractType] = Field(default_factory=list)
    remote: list[RemoteMode] = Field(default_factory=list)
    schedule: Schedule | None = None
    max_experience: ExperienceLevel | None = None
    salary_min_monthly: int | None = Field(default=None, ge=0, le=100_000)

    def to_domain(self) -> SearchCriteria:
        """Les critères du domaine ; la phrase est reconstruite des intitulés."""
        return SearchCriteria(
            query=" ou ".join(self.job_titles[:2]),
            job_titles=self.job_titles,
            keywords=self.keywords,
            location=self.location,
            whole_france=self.whole_france,
            contract_types=self.contract_types,
            remote=self.remote,
            schedule=self.schedule,
            max_experience=self.max_experience,
            salary_min_monthly=self.salary_min_monthly,
        )


class CriteriaAppliedResponse(BaseModel):
    """Les critères enregistrés et ce que la recherche a trouvé."""

    criteria: CriteriaResponse
    results_count: int = Field(description="Offres placées dans le fil.")


class AlertsRequest(BaseModel):
    """Activer ou couper les alertes de la recherche en cours."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class AlertsResponse(BaseModel):
    """L'état des alertes."""

    enabled: bool


# ---------------------------------------------------------------------
# L'assistant
# ---------------------------------------------------------------------


class MessageRequest(BaseModel):
    """Un message de la personne à Karria."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = Field(
        default=None, description="`null` au premier message : le serveur ouvre la conversation."
    )
    content: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)


class MessageResponse(BaseModel):
    """Un message de la conversation."""

    id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class AssistantReplyResponse(BaseModel):
    """La réponse de Karria, et ce qu'elle a déclenché."""

    conversation_id: UUID
    message: MessageResponse
    status: DialogueStatus = Field(
        description="`search` : une recherche a tourné, le fil est à jour. `clarify` : Karria "
        "pose une question. `chat` : une réponse, rien d'autre."
    )
    results_count: int | None = Field(description="Offres placées dans le fil, si `search`.")
    criteria: CriteriaResponse | None = Field(description="La recherche en cours après ce tour.")
    suggestions: list[str] = Field(
        description="0 à 4 réponses rapides à proposer sous la bulle ; un toucher les envoie "
        "comme un message."
    )
    intent: Intent = Field(
        description="Ce que Karria a compris du message : `search`, `refine`, `career_talk`, "
        "`app_help`, `small_talk`, `off_topic`."
    )

    @classmethod
    def from_turn(cls, turn: ConversationTurn) -> AssistantReplyResponse:
        """Projette."""
        message = turn.message
        return cls(
            conversation_id=turn.conversation_id,
            message=MessageResponse(
                id=message.id,
                role="assistant" if message.role == "assistant" else "user",
                content=message.content,
                created_at=message.created_at,
            ),
            status=turn.status,
            results_count=turn.results_count,
            criteria=CriteriaResponse.from_stored(turn.criteria) if turn.criteria else None,
            suggestions=list(turn.suggestions),
            intent=turn.intent,
        )


def parse_csv(value: str | None) -> tuple[str, ...]:
    """`"cdi,cdd"` → `("cdi", "cdd")`, sans vides ni doublons."""
    if not value:
        return ()
    return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


class MemoryResponse(BaseModel):
    """Ce que Karria a retenu de la personne — elle peut le lire, et l'effacer."""

    target_jobs: list[str]
    locations: list[str]
    constraints: list[str]
    preferences: list[str]
    situation: list[str]
    summary: str
    updated_at: datetime | None

    @classmethod
    def from_memory(cls, memory: KarriaMemory | None) -> MemoryResponse:
        """Projette ; vide si rien n'a été retenu."""
        facts = memory.facts if memory else MemoryFacts()
        return cls(
            target_jobs=facts.target_jobs,
            locations=facts.locations,
            constraints=facts.constraints,
            preferences=facts.preferences,
            situation=facts.situation,
            summary=facts.summary,
            updated_at=memory.updated_at if memory else None,
        )


# ---------------------------------------------------------------------
# Le collecteur (routes internes)
# ---------------------------------------------------------------------


class TargetResponse(BaseModel):
    """Une requête que le collecteur doit soumettre aux sites."""

    key: str = Field(description="À renvoyer dans `X-Target-Key` avec le lot correspondant.")
    search_term: str
    location: str = Field(description="Une ville, ou « France ».")
    distance_km: int | None = Field(description="Rayon autour du lieu ; `null` : celui du site.")
    last_found: int = Field(
        default=0,
        description="Annonces trouvées au dernier passage : zéro aujourd'hui alors que c'était "
        "beaucoup hier signale une page qui a changé.",
    )


class TargetsResponse(BaseModel):
    """Les cibles louées au collecteur pour ce passage."""

    handoff_version: int
    targets: list[TargetResponse]


class BatchAcceptedResponse(BaseModel):
    """Le lot est atterri ; son ingestion est différée."""

    key: str
