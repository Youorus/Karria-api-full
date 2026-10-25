"""Doublures en mémoire des ports du module `offers`, et petits constructeurs.

Chaque doublure implémente un port de `application/ports.py` avec une
règle aussi proche que possible de l'adaptateur réel (filtres, tri,
bornes), et garde la trace de ce qu'on lui a demandé : les tests vérifient
le comportement d'un cas d'usage par ce qu'il a fait faire à ses ports.
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from karria.modules.offers.application import (
    CriteriaMatch,
    FeedQuery,
    FetchedImage,
    IndexedOffer,
    IndexQuery,
    IngestionReport,
    Message,
    OfferFilters,
    OfferLink,
    OutboxItem,
    ProcessedLogo,
    StoredCriteria,
    StoredTarget,
)
from karria.modules.offers.domain import (
    Candidate,
    Company,
    ContractType,
    DialogueProposal,
    FeedEntry,
    FeedOrigin,
    GeocodedPlace,
    InteractionEvent,
    KarriaMemory,
    Location,
    LocationCriteria,
    MatchQuality,
    MemoryFacts,
    Offer,
    OfferState,
    RawPosting,
    Salary,
    ScrapeTarget,
    SearchCriteria,
    Source,
    TargetDemand,
    TargetOrigin,
    TargetStats,
    Widening,
    bands,
    canonical_text,
    content_hash,
    fold,
    simhash64,
)
from karria.modules.offers.domain.companies import CompanyRecord, ImageTraits, LogoStatus

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
PARIS = GeocodedPlace(
    city="Paris", postal_code="75001", department_code="75", latitude=48.8566, longitude=2.3522
)

_RANDOM = random.Random(3)  # noqa: S311 - texte de test déterministe, rien de secret
_WORDS = [f"mot{i}" for i in range(400)]
#: Une description longue et déterministe : y ajouter un mot donne un
#: quasi-doublon (distance de Hamming 2, mesurée), pas un doublon exact.
LONG_DESCRIPTION = " ".join(_RANDOM.choice(_WORDS) for _ in range(300))


class FixedClock:
    """Une horloge figée qu'on peut avancer."""

    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        """L'instant figé."""
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Fait avancer l'horloge."""
        self._now += delta


class RecordingUnitOfWork:
    """Une unité de travail sans base, qui compte ses transactions."""

    def __init__(self) -> None:
        self.transactions = 0

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        """Compte et laisse passer."""
        self.transactions += 1
        yield


# ---------------------------------------------------------------------
# Constructeurs
# ---------------------------------------------------------------------


def make_offer(**changes: Any) -> Offer:
    """Une offre canonique, active, géocodée à Paris, publiée la veille."""
    title = changes.pop("title", "Serveur H/F")
    company = changes.pop("company", Company(name="Brasserie du Nord"))
    description = changes.pop("description", "Service en salle, accueil des clients.")
    company_name = company.name if company else None
    base = Offer(
        id=uuid4(),
        source=Source.INDEED,
        source_id=f"in-{uuid4().hex[:10]}",
        title=title,
        company=company,
        description=description,
        location=Location(
            label="Paris (75)",
            city="Paris",
            postal_code="75001",
            department_code="75",
            latitude=PARIS.latitude,
            longitude=PARIS.longitude,
        ),
        contract_type=ContractType.CDI,
        remote=None,
        schedule=None,
        experience=None,
        salary=Salary(min_monthly=None, max_monthly=None, raw=None),
        published_at=NOW - timedelta(days=1),
        first_seen_at=NOW - timedelta(days=1),
        last_seen_at=NOW - timedelta(days=1),
        apply_url="https://exemple.fr/postuler",
        origin_url="https://exemple.fr/offre",
        content_hash=content_hash(title, company_name, description),
        simhash=simhash64(canonical_text(title, company_name, description)),
    )
    return dataclasses.replace(base, **changes)


def make_posting(**changes: Any) -> RawPosting:
    """Une annonce brute d'Indeed, à Paris, publiée hier."""
    base = RawPosting(
        source=Source.INDEED,
        source_id=f"in-{uuid4().hex[:10]}",
        title="Serveur H/F",
        description="Service en salle, accueil des clients. CDI temps plein.",
        company_name="Brasserie du Nord",
        location_label="Paris (75)",
        date_posted=(NOW - timedelta(days=1)).date(),
    )
    return dataclasses.replace(base, **changes)


def make_criteria(**changes: Any) -> SearchCriteria:
    """« Serveur à Paris », géocodé, 30 km."""
    data: dict[str, Any] = {
        "query": "serveur à Paris",
        "job_titles": ["serveur", "serveuse"],
        "keywords": ["restauration"],
        "location": LocationCriteria(
            label="Paris",
            latitude=PARIS.latitude,
            longitude=PARIS.longitude,
            department_code=PARIS.department_code,
        ),
    }
    data.update(changes)
    return SearchCriteria(**data)


def make_stored(
    user_id: UUID, criteria: SearchCriteria | None = None, **changes: Any
) -> StoredCriteria:
    """Des critères enregistrés, alertes actives."""
    chosen = criteria or make_criteria()
    base = StoredCriteria(
        user_id=user_id,
        criteria=chosen,
        fingerprint=chosen.fingerprint(),
        alerts_enabled=True,
        updated_at=NOW - timedelta(days=2),
    )
    return dataclasses.replace(base, **changes)


def make_entry(user_id: UUID, offer_id: UUID, **changes: Any) -> FeedEntry:
    """Une entrée de fil issue d'une recherche."""
    base = FeedEntry(
        user_id=user_id,
        offer_id=offer_id,
        score=0.8,
        quality=MatchQuality.HIGH,
        reasons=(),
        origin=FeedOrigin.SEARCH,
        created_at=NOW - timedelta(days=1),
    )
    return dataclasses.replace(base, **changes)


# ---------------------------------------------------------------------
# Le corpus
# ---------------------------------------------------------------------


class InMemoryOfferRepository:
    """`OfferRepository` en mémoire."""

    def __init__(self, offers: Sequence[Offer] = ()) -> None:
        self.offers: dict[UUID, Offer] = {offer.id: offer for offer in offers}
        self.saved: list[Offer] = []
        self.seen_calls: list[list[UUID]] = []
        self.near_calls = 0

    def add(self, *offers: Offer) -> None:
        """Pose des offres sans passer par le cas d'usage."""
        for offer in offers:
            self.offers[offer.id] = offer

    async def insert_many(self, offers: Sequence[Offer]) -> None:
        """Insère."""
        for offer in offers:
            self.offers[offer.id] = offer

    async def save(self, offer: Offer) -> None:
        """Remplace."""
        self.offers[offer.id] = offer
        self.saved.append(offer)

    async def find(self, offer_id: UUID) -> Offer | None:
        """Par identifiant."""
        return self.offers.get(offer_id)

    async def find_many(self, offer_ids: Sequence[UUID]) -> dict[UUID, Offer]:
        """Par identifiants."""
        return {i: self.offers[i] for i in offer_ids if i in self.offers}

    async def find_by_sources(
        self, keys: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], Offer]:
        """Par (source, identifiant de source)."""
        wanted = set(keys)
        return {
            (o.source.value, o.source_id): o
            for o in self.offers.values()
            if (o.source.value, o.source_id) in wanted
        }

    async def find_canonical_by_hashes(self, hashes: Sequence[str]) -> dict[str, Offer]:
        """La première canonique active de chaque condensat."""
        found: dict[str, Offer] = {}
        for offer in self.offers.values():
            if offer.content_hash in hashes and offer.is_canonical and offer.is_active:
                found.setdefault(offer.content_hash, offer)
        return found

    async def near_duplicate_candidates(
        self, *, bands: tuple[int, int, int, int], company_key: str | None, since: datetime
    ) -> Sequence[Offer]:
        """Même employeur, une bande commune, récentes, canoniques actives."""
        self.near_calls += 1
        wanted = bands
        return [
            o
            for o in self.offers.values()
            if o.is_canonical
            and o.is_active
            and o.published_at >= since
            and (fold(o.company.name) if o.company else None) == company_key
            and any(a == b for a, b in zip(_bands(o.simhash), wanted, strict=True))
        ]

    async def mark_seen(self, offer_ids: Sequence[UUID], *, at: datetime) -> None:
        """Rafraîchit `last_seen_at`."""
        self.seen_calls.append(list(offer_ids))
        for offer_id in offer_ids:
            if offer_id in self.offers:
                self.offers[offer_id] = dataclasses.replace(self.offers[offer_id], last_seen_at=at)

    async def page_active(
        self, *, filters: OfferFilters, limit: int, offset: int
    ) -> tuple[Sequence[Offer], int]:
        """Actives canoniques filtrées, récentes d'abord."""
        kept = [
            o
            for o in self.offers.values()
            if o.indexable
            and (not filters.contract_types or o.contract_type in filters.contract_types)
            and (not filters.cities or (o.location and o.location.city in filters.cities))
            and (filters.published_after is None or o.published_at >= filters.published_after)
            and o.id not in filters.exclude_offer_ids
        ]
        kept.sort(key=lambda o: o.published_at, reverse=True)
        return kept[offset : offset + limit], len(kept)

    async def mark_indexed(
        self, indexed: Sequence[tuple[UUID, str]], *, version: str, at: datetime
    ) -> None:
        """Pose version et condensat."""
        for offer_id, digest in indexed:
            self.offers[offer_id] = dataclasses.replace(
                self.offers[offer_id], indexed_version=version, indexed_hash=digest, indexed_at=at
            )

    async def mark_unindexed(self, offer_ids: Sequence[UUID]) -> None:
        """Efface la version."""
        for offer_id in offer_ids:
            self.offers[offer_id] = dataclasses.replace(
                self.offers[offer_id], indexed_version=None, indexed_hash=None, indexed_at=None
            )

    async def deactivate_published_before(self, moment: datetime, *, at: datetime) -> list[UUID]:
        """Désactive les anciennes."""
        expired = [o.id for o in self.offers.values() if o.is_active and o.published_at < moment]
        for offer_id in expired:
            self.offers[offer_id] = dataclasses.replace(self.offers[offer_id], is_active=False)
        return expired

    async def stale_index_ids(self, *, version: str, limit: int) -> list[UUID]:
        """À indexer, puis à retirer."""
        to_index = [
            o.id for o in self.offers.values() if o.indexable and o.indexed_version != version
        ]
        to_remove = [o.id for o in self.offers.values() if not o.indexable and o.is_indexed]
        return [*to_index, *to_remove][:limit]

    async def count_indexable(self) -> int:
        """Actives et canoniques."""
        return sum(1 for o in self.offers.values() if o.indexable)

    async def filter_options(
        self, offer_ids: Sequence[UUID] | None
    ) -> tuple[dict[ContractType, int], dict[str, int]]:
        """Agrégats."""
        pool = [o for o in self.offers.values() if o.indexable]
        if offer_ids is not None:
            pool = [o for o in pool if o.id in offer_ids]
        contracts: dict[ContractType, int] = {}
        cities: dict[str, int] = {}
        for offer in pool:
            if offer.contract_type:
                contracts[offer.contract_type] = contracts.get(offer.contract_type, 0) + 1
            if offer.location and offer.location.city:
                cities[offer.location.city] = cities.get(offer.location.city, 0) + 1
        return contracts, cities


def _bands(simhash: int) -> tuple[int, int, int, int]:
    return bands(simhash)


class InMemoryOutbox:
    """`IndexOutbox` en mémoire."""

    def __init__(self) -> None:
        self.items: list[OutboxItem] = []
        self._next = 1

    async def enqueue(self, offer_ids: Sequence[UUID], *, at: datetime) -> None:
        """Ajoute."""
        for offer_id in offer_ids:
            self.items.append(OutboxItem(id=self._next, offer_id=offer_id))
            self._next += 1

    async def claim(self, *, limit: int) -> Sequence[OutboxItem]:
        """Les plus anciennes."""
        return list(self.items[:limit])

    async def ack(self, item_ids: Sequence[int]) -> None:
        """Retire."""
        self.items = [item for item in self.items if item.id not in item_ids]

    async def pending(self) -> int:
        """Combien attendent."""
        return len(self.items)

    @property
    def offer_ids(self) -> list[UUID]:
        """Les offres inscrites, dans l'ordre."""
        return [item.offer_id for item in self.items]


class InMemoryOfferIndex:
    """`OfferIndex` en mémoire : la recherche rend `candidates` dans l'ordre posé."""

    def __init__(self, candidates: Sequence[UUID] = ()) -> None:
        self.points: dict[UUID, IndexedOffer] = {}
        self.payloads: list[UUID] = []
        self.deleted: list[UUID] = []
        self.candidates: list[UUID] = list(candidates)
        self.queries: list[IndexQuery] = []
        self.ready = False

    async def ensure_ready(self) -> None:
        """Rien à créer."""
        self.ready = True

    async def upsert(self, items: Sequence[IndexedOffer]) -> None:
        """Écrit."""
        for item in items:
            self.points[item.offer.id] = item

    async def update_payloads(self, offers: Sequence[Offer]) -> None:
        """Note."""
        self.payloads.extend(o.id for o in offers)

    async def delete(self, offer_ids: Sequence[UUID]) -> None:
        """Retire."""
        self.deleted.extend(offer_ids)
        for offer_id in offer_ids:
            self.points.pop(offer_id, None)

    async def search(self, query: IndexQuery) -> Sequence[Candidate]:
        """La fenêtre demandée des candidats posés."""
        self.queries.append(query)
        window = self.candidates[query.offset : query.offset + query.limit]
        return [
            Candidate(offer_id=i, retrieval_score=1.0 / (rank + 1)) for rank, i in enumerate(window)
        ]

    async def title_candidates(self, term: str, *, limit: int) -> Sequence[UUID]:
        """Les offres indexées dont le titre contient un mot du terme (plié)."""
        words = fold(term).split()
        hits = [
            offer_id
            for offer_id, item in self.points.items()
            if any(word in fold(item.offer.title) for word in words)
        ]
        return hits[:limit]

    async def count(self) -> int:
        """Nombre de points."""
        return len(self.points)


class InMemoryCriteriaIndex:
    """`CriteriaIndex` en mémoire : `similar` rend `matches`, alertes actives seulement."""

    def __init__(self) -> None:
        self.points: dict[UUID, tuple[tuple[float, ...], SearchCriteria, bool]] = {}
        self.matches: list[CriteriaMatch] = []
        self.similar_calls: list[Offer] = []
        self.ready = False

    async def ensure_ready(self) -> None:
        """Rien à créer."""
        self.ready = True

    async def upsert(
        self,
        user_id: UUID,
        dense: Sequence[float],
        criteria: SearchCriteria,
        *,
        alerts_enabled: bool,
    ) -> None:
        """Écrit."""
        self.points[user_id] = (tuple(dense), criteria, alerts_enabled)

    async def delete(self, user_id: UUID) -> None:
        """Retire."""
        self.points.pop(user_id, None)

    async def set_alerts_enabled(self, user_id: UUID, *, enabled: bool) -> None:
        """Bascule."""
        dense, criteria, _ = self.points[user_id]
        self.points[user_id] = (dense, criteria, enabled)

    async def similar(
        self, dense: Sequence[float], offer: Offer, *, limit: int, min_similarity: float
    ) -> Sequence[CriteriaMatch]:
        """Les correspondances posées au-dessus du seuil."""
        self.similar_calls.append(offer)
        return [m for m in self.matches if m.similarity >= min_similarity][:limit]


# ---------------------------------------------------------------------
# Les modèles et services
# ---------------------------------------------------------------------


class FakeEmbedder:
    """`TextEmbedder` : un vecteur constant, les textes retenus."""

    def __init__(self, version: str = "fake-model") -> None:
        self._version = version
        self.documents: list[str] = []
        self.queries: list[str] = []

    @property
    def version(self) -> str:
        """Le nom du modèle."""
        return self._version

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        """Un vecteur par texte."""
        self.documents.extend(texts)
        return [(1.0, 0.0) for _ in texts]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Le vecteur de la requête."""
        self.queries.append(text)
        return (0.0, 1.0)


class FakeDialogue:
    """`SearchDialogue` : rend la proposition posée, retient ce qu'on lui a dit."""

    def __init__(self, proposal: DialogueProposal | None = None) -> None:
        self.proposal = proposal or DialogueProposal(reply="Bonjour.", criteria=None)
        self.calls: list[dict[str, Any]] = []

    async def propose(
        self,
        *,
        history: Sequence[tuple[str, str]],
        current: SearchCriteria | None,
        message: str,
        memory: str | None = None,
        facts: str | None = None,
    ) -> DialogueProposal:
        """La proposition posée."""
        self.calls.append(
            {
                "history": list(history),
                "current": current,
                "message": message,
                "memory": memory,
                "facts": facts,
            }
        )
        return self.proposal


class FakeGeocoder:
    """`Geocoder` : un dictionnaire de lieux connus ; lève pour les requêtes piégées."""

    def __init__(self, places: Mapping[str, GeocodedPlace] | None = None) -> None:
        self.places = dict(places or {})
        self.failing: set[str] = set()
        self.queries: list[str] = []

    async def geocode(self, query: str) -> GeocodedPlace | None:
        """Le lieu, ou `None`."""
        self.queries.append(query)
        if query in self.failing:
            raise RuntimeError("géocodeur en panne")
        return self.places.get(query)


class FakeLanding:
    """`LandingStore` : garde les lots en mémoire, sous forme décodée."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.records: dict[str, list[dict[str, Any]]] = {}

    async def store(self, batch_id: UUID, payload: bytes, *, at: datetime) -> str:
        """Rend une clé."""
        key = f"collector/{batch_id.hex}.gz"
        self.objects[key] = payload
        return key

    async def read(self, key: str) -> list[dict[str, Any]]:
        """Les lignes posées pour cette clé."""
        return self.records.get(key, [])


class InMemoryTargetRepository:
    """`ScrapeTargetRepository` en mémoire."""

    def __init__(self) -> None:
        self.targets: dict[str, StoredTarget] = {}
        self.upserts: list[tuple[list[ScrapeTarget], bool]] = []
        self.scraped: list[tuple[str, TargetStats, datetime]] = []
        self.leased: list[tuple[list[str], datetime]] = []
        self.demands: dict[tuple[UUID, str], datetime] = {}
        self.weights: list[Mapping[str, int]] = []
        self.retired_before: list[datetime] = []
        self.retired = 0

    def add(
        self,
        target: ScrapeTarget,
        *,
        next_at: datetime | None = None,
        stats: TargetStats | None = None,
        last_scraped_at: datetime | None = None,
        thin_since: datetime | None = None,
    ) -> None:
        """Pose une cible due."""
        self.targets[target.key] = StoredTarget(
            key=target.key,
            target=target,
            next_scrape_at=next_at,
            last_scraped_at=last_scraped_at,
            stats=stats or TargetStats(),
            thin_since=thin_since,
        )

    async def upsert_many(
        self, targets: Sequence[ScrapeTarget], *, at: datetime, due_now: bool = False
    ) -> None:
        """Retient."""
        self.upserts.append((list(targets), due_now))
        for target in targets:
            previous = self.targets.get(target.key)
            if previous is None:
                self.targets[target.key] = StoredTarget(
                    key=target.key,
                    target=target,
                    next_scrape_at=at,
                    last_scraped_at=None,
                    thin_since=at if due_now else None,
                )
                continue
            self.targets[target.key] = dataclasses.replace(
                previous,
                next_scrape_at=at if due_now else previous.next_scrape_at,
                thin_since=at if due_now else previous.thin_since,
                is_active=True,
            )

    async def due(self, *, now: datetime, limit: int) -> Sequence[StoredTarget]:
        """Les échues, dans l'ordre d'insertion."""
        due = [
            t
            for t in self.targets.values()
            if t.is_active and (t.next_scrape_at is None or t.next_scrape_at <= now)
        ]
        return due[:limit]

    async def lease(self, keys: Sequence[str], *, until: datetime) -> None:
        """Repousse."""
        self.leased.append((list(keys), until))
        for key in keys:
            self.targets[key] = dataclasses.replace(self.targets[key], next_scrape_at=until)

    async def find(self, key: str) -> StoredTarget | None:
        """Par clé."""
        return self.targets.get(key)

    async def record_scrape(
        self, key: str, stats: TargetStats, *, at: datetime, next_at: datetime
    ) -> None:
        """Planifie la suivante et retient l'appris."""
        self.scraped.append((key, stats, next_at))
        stored = self.targets[key]
        self.targets[key] = dataclasses.replace(
            stored, next_scrape_at=next_at, last_scraped_at=at, stats=stats
        )

    async def replace_demands(self, user_id: UUID, keys: Sequence[str], *, at: datetime) -> None:
        """Les liens de la personne."""
        for user, key in list(self.demands):
            if user == user_id and key not in keys:
                del self.demands[user, key]
        for key in keys:
            self.demands[user_id, key] = at

    async def all_demands(self) -> Sequence[TargetDemand]:
        """Tous les liens."""
        return [
            TargetDemand(user_id=user, target_key=key, since=since)
            for (user, key), since in self.demands.items()
        ]

    async def set_weights(self, weights: Mapping[str, int]) -> None:
        """Retient et applique."""
        self.weights.append(dict(weights))
        for key, stored in self.targets.items():
            if stored.target.origin is TargetOrigin.DEMAND:
                self.targets[key] = dataclasses.replace(
                    stored, target=dataclasses.replace(stored.target, weight=weights.get(key, 0))
                )

    async def deactivate_demand_before(self, moment: datetime) -> int:
        """Retient l'instant, rend `retired`."""
        self.retired_before.append(moment)
        return self.retired

    async def count_active(self, origin: TargetOrigin | None = None) -> int:
        """Combien."""
        return sum(
            1
            for t in self.targets.values()
            if t.is_active and (origin is None or t.target.origin is origin)
        )

    async def catalogue(self, *, limit: int) -> Sequence[StoredTarget]:
        """Les actives, les plus pesantes d'abord."""
        active = [t for t in self.targets.values() if t.is_active]
        active.sort(key=lambda t: (-t.target.weight, t.key))
        return active[:limit]


class InMemoryRunRepository:
    """`IngestionRunRepository` en mémoire."""

    def __init__(self) -> None:
        self.reports: list[IngestionReport] = []

    async def record(self, report: IngestionReport) -> None:
        """Retient."""
        self.reports.append(report)


# ---------------------------------------------------------------------
# La personne
# ---------------------------------------------------------------------


class InMemoryCriteriaRepository:
    """`CriteriaRepository` en mémoire."""

    def __init__(self, *stored: StoredCriteria) -> None:
        self.stored: dict[UUID, StoredCriteria] = {s.user_id: s for s in stored}
        self.alerts: list[tuple[UUID, datetime]] = []

    async def find(self, user_id: UUID) -> StoredCriteria | None:
        """Par personne."""
        return self.stored.get(user_id)

    async def save(self, stored: StoredCriteria) -> None:
        """Remplace."""
        self.stored[stored.user_id] = stored

    async def delete(self, user_id: UUID) -> bool:
        """Oublie."""
        return self.stored.pop(user_id, None) is not None

    async def all_with_alerts(self) -> Sequence[StoredCriteria]:
        """Les actives."""
        return [s for s in self.stored.values() if s.alerts_enabled]

    async def find_many(self, user_ids: Sequence[UUID]) -> dict[UUID, StoredCriteria]:
        """Plusieurs."""
        return {i: self.stored[i] for i in user_ids if i in self.stored}

    async def record_alert(self, user_id: UUID, *, at: datetime) -> None:
        """Retient."""
        self.alerts.append((user_id, at))


class InMemoryConversationRepository:
    """`ConversationRepository` en mémoire."""

    def __init__(self) -> None:
        self.owners: dict[UUID, UUID] = {}
        self.messages: list[Message] = []
        self.turns_today = 0
        self.pending: dict[UUID, list[Widening]] = {}

    async def create(self, user_id: UUID, *, at: datetime) -> UUID:
        """Ouvre."""
        conversation_id = uuid4()
        self.owners[conversation_id] = user_id
        return conversation_id

    async def belongs_to(self, conversation_id: UUID, user_id: UUID) -> bool:
        """À elle ?"""
        return self.owners.get(conversation_id) == user_id

    async def add_message(
        self, conversation_id: UUID, *, role: str, content: str, at: datetime
    ) -> Message:
        """Ajoute."""
        message = Message(
            id=uuid4(), conversation_id=conversation_id, role=role, content=content, created_at=at
        )
        self.messages.append(message)
        return message

    async def recent_messages(self, conversation_id: UUID, *, limit: int) -> Sequence[Message]:
        """Les derniers."""
        return [m for m in self.messages if m.conversation_id == conversation_id][-limit:]

    async def set_pending_widenings(
        self, conversation_id: UUID, widenings: Sequence[Widening]
    ) -> None:
        """Garde."""
        self.pending[conversation_id] = list(widenings)

    async def pending_widenings(self, conversation_id: UUID) -> Sequence[Widening]:
        """Relit."""
        return list(self.pending.get(conversation_id, []))

    async def user_turns_since(self, user_id: UUID, since: datetime) -> int:
        """Le compteur posé."""
        return self.turns_today

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface."""
        mine = {c for c, owner in self.owners.items() if owner == user_id}
        self.owners = {c: o for c, o in self.owners.items() if c not in mine}
        before = len(self.messages)
        self.messages = [m for m in self.messages if m.conversation_id not in mine]
        return before - len(self.messages)


class InMemoryFeedRepository:
    """`FeedRepository` en mémoire. La page trie par score, ou par date d'offre si on la lui donne."""

    def __init__(self, offers: InMemoryOfferRepository | None = None) -> None:
        self.entries: dict[tuple[UUID, UUID], FeedEntry] = {}
        self.offers = offers
        self.queries: list[FeedQuery] = []
        self.notified: list[tuple[UUID, list[UUID]]] = []

    def add(self, *entries: FeedEntry) -> None:
        """Pose des entrées."""
        for entry in entries:
            self.entries[(entry.user_id, entry.offer_id)] = entry

    async def entries_of(self, user_id: UUID) -> Sequence[FeedEntry]:
        """Toutes."""
        return [e for (u, _), e in self.entries.items() if u == user_id]

    async def replace(
        self, user_id: UUID, entries: Sequence[FeedEntry], *, remove: Sequence[UUID]
    ) -> None:
        """Écrit et retire."""
        for offer_id in remove:
            self.entries.pop((user_id, offer_id), None)
        self.add(*entries)

    async def add_many(self, entries: Sequence[FeedEntry]) -> None:
        """Ajoute sans écraser."""
        for entry in entries:
            self.entries.setdefault((entry.user_id, entry.offer_id), entry)

    async def page(self, query: FeedQuery) -> tuple[Sequence[FeedEntry], int]:
        """Filtre les exclues, trie par score."""
        self.queries.append(query)
        kept = [
            e
            for e in await self.entries_of(query.user_id)
            if e.offer_id not in query.filters.exclude_offer_ids
        ]
        kept.sort(key=lambda e: e.score, reverse=True)
        return kept[query.offset : query.offset + query.limit], len(kept)

    async def find(self, user_id: UUID, offer_id: UUID) -> FeedEntry | None:
        """Une."""
        return self.entries.get((user_id, offer_id))

    async def mark_notified(
        self, user_id: UUID, offer_ids: Sequence[UUID], *, at: datetime
    ) -> None:
        """Note."""
        self.notified.append((user_id, list(offer_ids)))
        for offer_id in offer_ids:
            entry = self.entries[(user_id, offer_id)]
            self.entries[(user_id, offer_id)] = dataclasses.replace(entry, notified_at=at)

    async def offer_ids_of(self, user_id: UUID) -> Sequence[UUID]:
        """Les identifiants."""
        return [e.offer_id for e in await self.entries_of(user_id)]

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface."""
        mine = [k for k in self.entries if k[0] == user_id]
        for key in mine:
            del self.entries[key]
        return len(mine)


class InMemoryInteractionLog:
    """`InteractionLog` en mémoire."""

    def __init__(self) -> None:
        self.events: list[InteractionEvent] = []

    async def append(self, events: Sequence[InteractionEvent]) -> None:
        """Consigne."""
        self.events.extend(events)


class InMemoryStateRepository:
    """`OfferStateRepository` en mémoire."""

    def __init__(self) -> None:
        self.states: dict[tuple[UUID, UUID], OfferState] = {}

    def add(self, *states: OfferState) -> None:
        """Pose des états."""
        for state in states:
            self.states[(state.user_id, state.offer_id)] = state

    async def find(self, user_id: UUID, offer_id: UUID) -> OfferState | None:
        """Un."""
        return self.states.get((user_id, offer_id))

    async def find_many(self, user_id: UUID, offer_ids: Sequence[UUID]) -> dict[UUID, OfferState]:
        """Plusieurs."""
        return {i: self.states[(user_id, i)] for i in offer_ids if (user_id, i) in self.states}

    async def all_of_user(self, user_id: UUID) -> dict[UUID, OfferState]:
        """Tous."""
        return {o: s for (u, o), s in self.states.items() if u == user_id}

    async def offer_ids_where(
        self,
        user_id: UUID,
        *,
        saved: bool = False,
        dismissed: bool = False,
        seen: bool = False,
        applied: bool = False,
    ) -> Sequence[UUID]:
        """Au moins un indicateur demandé vrai."""
        return [
            o
            for (u, o), s in self.states.items()
            if u == user_id
            and (
                (saved and s.saved)
                or (dismissed and s.dismissed)
                or (seen and s.seen)
                or (applied and s.applied)
            )
        ]

    async def save(self, state: OfferState) -> None:
        """Remplace."""
        self.states[(state.user_id, state.offer_id)] = state

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface."""
        mine = [k for k in self.states if k[0] == user_id]
        for key in mine:
            del self.states[key]
        return len(mine)


class RecordingQueue:
    """`JobQueue` : retient les différés."""

    def __init__(self) -> None:
        self.index_syncs = 0
        self.batches: list[tuple[str, str | None]] = []
        self.memory_updates: list[tuple[UUID, str, str]] = []
        self.logo_fetches = 0

    async def defer_logo_fetch(self) -> None:
        """Compte."""
        self.logo_fetches += 1

    async def defer_index_sync(self) -> None:
        """Compte."""
        self.index_syncs += 1

    async def defer_ingest_batch(self, key: str, target_key: str | None) -> None:
        """Retient."""
        self.batches.append((key, target_key))

    async def defer_memory_update(self, user_id: UUID, *, user_message: str, reply: str) -> None:
        """Retient."""
        self.memory_updates.append((user_id, user_message, reply))


class InMemoryMemoryRepository:
    """`KarriaMemoryRepository` en mémoire."""

    def __init__(self) -> None:
        self.memories: dict[UUID, KarriaMemory] = {}

    async def find(self, user_id: UUID) -> KarriaMemory | None:
        """Par personne."""
        return self.memories.get(user_id)

    async def save(self, user_id: UUID, memory: KarriaMemory) -> None:
        """Remplace."""
        self.memories[user_id] = memory

    async def delete(self, user_id: UUID) -> bool:
        """Oublie."""
        return self.memories.pop(user_id, None) is not None


class FakeMemoryUpdater:
    """`MemoryUpdater` : rend les faits posés, retient les appels."""

    def __init__(self, proposed: MemoryFacts | None = None) -> None:
        self.proposed = proposed
        self.calls: list[tuple[MemoryFacts, str, str]] = []

    async def propose(
        self, *, current: MemoryFacts, user_message: str, reply: str
    ) -> MemoryFacts | None:
        """La proposition posée."""
        self.calls.append((current, user_message, reply))
        return self.proposed


class RecordingNotifier:
    """`UserNotifier` : retient les envois, rend `accepted`."""

    def __init__(self, accepted: int = 1) -> None:
        self.accepted = accepted
        self.sent: list[dict[str, Any]] = []

    async def notify(self, user_id: UUID, *, title: str, body: str, data: Mapping[str, str]) -> int:
        """Retient et rend `accepted`."""
        self.sent.append({"user_id": user_id, "title": title, "body": body, "data": dict(data)})
        return self.accepted


class InMemoryCompanyRepository:
    """`CompanyRepository` en mémoire ; les offres rattachées sont lues dans `offers`."""

    def __init__(self, offers: InMemoryOfferRepository | None = None) -> None:
        self.companies: dict[UUID, CompanyRecord] = {}
        self.links: dict[tuple[str, str], UUID] = {}
        self.offers = offers
        self.saved_logos: list[CompanyRecord] = []

    async def find_by_keys(self, keys: Sequence[str]) -> dict[str, list[CompanyRecord]]:
        """Par clé, dans l'ordre d'insertion."""
        found: dict[str, list[CompanyRecord]] = {}
        for record in self.companies.values():
            if record.key in keys:
                found.setdefault(record.key, []).append(record)
        return found

    async def save_many(self, records: Sequence[CompanyRecord]) -> None:
        """Remplace."""
        for record in records:
            self.companies[record.id] = record

    async def link_offers(self, links: Sequence[OfferLink]) -> int:
        """Ne compte que ce qui change."""
        changed = 0
        for link in links:
            key = (link.source, link.source_id)
            if self.links.get(key) != link.company_id:
                self.links[key] = link.company_id
                changed += 1
        return changed

    async def claim_logo_work(
        self, *, now: datetime, until: datetime, limit: int
    ) -> list[CompanyRecord]:
        """Les dues, sous bail."""
        due = [
            r
            for r in self.companies.values()
            if r.logo_status is LogoStatus.PENDING
            and r.logo_next_attempt_at is not None
            and r.logo_next_attempt_at <= now
        ][:limit]
        for record in due:
            self.companies[record.id] = dataclasses.replace(record, logo_next_attempt_at=until)
        return due

    async def save_logo(self, record: CompanyRecord) -> int:
        """Retient."""
        self.companies[record.id] = record
        self.saved_logos.append(record)
        return sum(1 for cid in self.links.values() if cid == record.id)

    async def unlinked_offers(
        self, *, after: UUID | None, limit: int, active_only: bool
    ) -> list[Offer]:
        """Celles du dépôt d'offres que rien ne rattache."""
        pool = sorted((self.offers.offers.values() if self.offers else []), key=lambda o: o.id.int)
        return [
            o
            for o in pool
            if o.company is not None
            and (o.source.value, o.source_id) not in self.links
            and (not active_only or o.is_active)
            and (after is None or o.id.int > after.int)
        ][:limit]


class ScriptedLogoFetcher:
    """`LogoFetcher` scripté : adresse → réponse ; tout le reste est un 404."""

    def __init__(
        self,
        images: Mapping[str, FetchedImage] | None = None,
        icons: Mapping[str, list[str]] | None = None,
    ) -> None:
        self.images = dict(images or {})
        self.icons = dict(icons or {})
        self.fetched: list[str] = []

    async def fetch_image(self, url: str) -> FetchedImage:
        """Rend le scénario."""
        self.fetched.append(url)
        return self.images.get(url, FetchedImage(payload=None, permanent=True))

    async def site_icons(self, domain: str) -> list[str]:
        """Rend le scénario."""
        return self.icons.get(domain, [])


class TraitsProcessor:
    """`LogoProcessor` : les octets sont la clé d'un jeu de mesures ; inconnus = illisibles."""

    def __init__(self, traits: Mapping[bytes, ImageTraits]) -> None:
        self.traits = dict(traits)

    def process(self, payload: bytes) -> ProcessedLogo | None:
        """Rend des tailles factices."""
        if payload not in self.traits:
            return None
        digest = payload.decode().ljust(32, "0")[:32]
        return ProcessedLogo(
            digest=digest, traits=self.traits[payload], renditions={128: b"s", 256: b"l"}
        )


class InMemoryLogoStore:
    """`LogoStore` en mémoire."""

    def __init__(self) -> None:
        self.stored: dict[str, ProcessedLogo] = {}

    async def has(self, digest: str) -> bool:
        """Présent ?"""
        return digest in self.stored

    async def put(self, logo: ProcessedLogo) -> None:
        """Range."""
        self.stored[logo.digest] = logo
