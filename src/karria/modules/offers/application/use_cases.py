"""Cas d'usage du module `offers` : collecter, indexer, chercher, converser, alerter.

Lecture dans l'ordre du flux d'une offre :

1. **Collecte** — `RefreshScrapeTargets` décide quoi demander aux sites,
   `LeaseScrapeTargets` le confie au collecteur (un processus à part),
   `ReceiveBatch` atterrit ce qu'il remet, `IngestBatch` puis
   `IngestPostings` normalisent, dédoublonnent et enregistrent **par
   lot**, et inscrivent dans la boîte d'envoi ce que l'index doit apprendre.
2. **Index** — `SyncIndex` vide la boîte d'envoi : plonge ce qui est
   nouveau, met à jour les attributs de ce qui a seulement changé d'état,
   retire ce qui n'a plus sa place. `ExpireOffers` et `ReconcileIndex`
   l'entretiennent.
3. **Alertes** — `DispatchAlerts` confronte chaque offre fraîchement
   plongée aux critères des personnes (matching inversé) et prévient.
4. **Recherche** — `SearchOffers` interroge l'index puis laisse le
   domaine décider (filtre dur, porte d'intitulé) ; `ApplyCriteria` en fait
   le fil de la personne ; `ConverseSearch` fait parler Karria.
5. **Fil** — `GetFeed`, `GetOfferForUser`, `UpdateOfferState`,
   `MarkFeedSeen`, `GetFeedFilters`.

Chaque cas d'usage ne connaît que des ports ; tout se teste avec des
doublures en mémoire.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid4

from karria.modules.offers.application.ports import (
    CompanyRepository,
    ConversationRepository,
    CriteriaIndex,
    CriteriaRepository,
    FeedQuery,
    FeedRepository,
    Geocoder,
    IndexedOffer,
    IndexOutbox,
    IndexQuery,
    IngestionReport,
    IngestionRunRepository,
    InteractionLog,
    JobQueue,
    KarriaMemoryRepository,
    LandingStore,
    LogoFetcher,
    LogoProcessor,
    LogoStore,
    MemoryUpdater,
    Message,
    OfferFilters,
    OfferIndex,
    OfferLink,
    OfferRepository,
    OfferStateRepository,
    ScrapeTargetRepository,
    SearchDialogue,
    StoredCriteria,
    StoredTarget,
    TextEmbedder,
    UserNotifier,
)
from karria.modules.offers.domain import (
    ALERT_MIN_SCORE,
    ALERT_MIN_SIMILARITY,
    ASSISTANT_DAILY_LIMIT,
    ASSISTANT_RETRY_AFTER,
    BATCH_MAX_BYTES,
    BATCH_MAX_POSTINGS,
    DEMAND_TTL,
    IDENTITY_REPLY,
    NEAR_DUPLICATE_WINDOW_DAYS,
    OFFER_ACTIVE_TTL,
    PROBATION_MIN_GATE_RATE,
    RECENT_WINDOW,
    RETRIEVAL_LIMIT,
    TARGET_LEASE,
    TARGETS_PER_RUN,
    THIN_RESULTS,
    AssistantRateLimitedError,
    BatchRejectedError,
    ContractType,
    ConversationNotFoundError,
    CriteriaIncompleteError,
    DialogueOutcome,
    DialogueStatus,
    FeedEntry,
    FeedMode,
    FeedOrigin,
    FeedSort,
    Intent,
    InteractionEvent,
    InteractionKind,
    KarriaMemory,
    MemoryFacts,
    NearestSortUnavailableError,
    Offer,
    OfferNotFoundError,
    OfferState,
    PreFlag,
    RankedOffer,
    RawPosting,
    ScrapeOutcome,
    SearchCriteria,
    Source,
    TargetState,
    Widening,
    accepted_widening,
    after_scrape,
    announce_results,
    bands,
    body_text,
    build_notification,
    candidate_terms,
    canonical_text,
    choose,
    clean_suggestions,
    content_hash,
    decide,
    demand_scores,
    embedding_text,
    entries_to_remove,
    fold,
    gate_rate,
    gate_rejects,
    guard_reply,
    hamming,
    haversine_km,
    index_version,
    interval_for,
    is_fresh,
    is_near_duplicate,
    may_notify,
    merge_memory,
    normalize_posting,
    parse_location_label,
    phrase_widenings,
    posting_from_record,
    posting_hash,
    pre_classify,
    priority,
    published_at_of,
    rank,
    relaxations,
    render_pending,
    seed_targets,
    simhash64,
    snapshot,
    source_place,
    targets_of,
    title_relaxations,
    title_text,
    transitions,
    uuid7,
    validate_terms,
)
from karria.modules.offers.domain.companies import (
    CLAIM_LEASE,
    COMPANY_DEADLINE_SECONDS,
    MAX_COMPANIES_PER_DOMAIN,
    MAX_SHARED_FALLBACK_LOGO,
    PLACEHOLDER_HASHES,
    CompanyRecord,
    CompanySighting,
    LogoAttempt,
    LogoCandidate,
    LogoOrigin,
    LogoRejection,
    LogoStatus,
    choose_company,
    judge_logo,
    logo_candidates,
    merge_sighting,
    new_company,
    plausible_domain,
    settle_logo,
    sighting_of,
    site_icon_urls,
)
from karria.shared.clock import Clock
from karria.shared.database import UnitOfWork
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Offres plongées par passage de synchronisation. Mesuré le 2026-10-22 :
#: 64 textes de ~600 caractères occupent le serveur d'inférence ~4 s.
INDEX_BATCH_SIZE: Final = 64
#: Critères examinés par offre lors du matching inversé.
ALERT_CANDIDATES_PER_OFFER: Final = 200
#: Titres réels relus pour vérifier qu'un terme de collecte désigne un métier.
VOCABULARY_SAMPLE: Final = 40
#: Cibles échues examinées avant d'en choisir `limit` par priorité.
DUE_CANDIDATES_FACTOR: Final = 4
#: Une cible qui rapportait au moins cela et ne rapporte plus rien est une anomalie à voir.
YIELD_ANOMALY_FLOOR: Final = 5.0


# ---------------------------------------------------------------------
# 1. Collecte
# ---------------------------------------------------------------------


class RefreshScrapeTargets:
    """Entretient le catalogue : le socle, les poids de la demande, les cibles oubliées.

    Les cibles demandées naissent dans `ApplyCriteria` (termes validés
    contre le vocabulaire des titres) ; ici on ne fait que les repeser —
    combien de personnes derrière chacune — et éteindre celles que plus
    personne ne demande depuis `DEMAND_TTL`.
    """

    def __init__(self, *, targets: ScrapeTargetRepository, clock: Clock) -> None:
        self._targets = targets
        self._clock = clock

    async def execute(self) -> int:
        """Met le catalogue à jour. Rend le nombre de cibles demandées."""
        now = self._clock.now()
        demands = await self._targets.all_demands()
        weights: dict[str, int] = {}
        for demand in demands:
            weights[demand.target_key] = weights.get(demand.target_key, 0) + 1
        await self._targets.upsert_many(seed_targets(), at=now)
        await self._targets.set_weights(weights)
        retired = await self._targets.deactivate_demand_before(now - DEMAND_TTL)
        logger.info(
            "offers.targets_refreshed",
            demanded=len(weights),
            people=len({str(d.user_id) for d in demands}),
            retired=retired,
        )
        return len(weights)


@dataclass(slots=True)
class IngestStats:
    """Ce qu'un lot d'annonces est devenu."""

    received: int = 0
    inserted: int = 0
    refreshed: int = 0
    updated: int = 0
    duplicates: int = 0
    near_duplicates: int = 0
    #: Annonces que la source dit retirées, et qu'on connaissait encore actives.
    expired: int = 0
    #: Doublons dont la canonique vient de France Travail : ce que la cible
    #: JobSpy rapporte de redondant avec la couche de base.
    overlap_france_travail: int = 0
    errors: int = 0
    inserted_ids: list[UUID] = field(default_factory=list)


@dataclass(slots=True)
class _Batch:
    """L'état de travail d'un lot, le temps de `IngestPostings.execute`."""

    now: datetime
    stats: IngestStats
    new: list[Offer] = field(default_factory=list)
    changed: list[Offer] = field(default_factory=list)
    seen: list[UUID] = field(default_factory=list)
    #: Offres canoniques revues sous une date plus récente : republiées.
    republished: dict[UUID, Offer] = field(default_factory=dict)
    #: Offres connues que la source a retirées : désactivées, retirées de l'index.
    expired: list[Offer] = field(default_factory=list)
    renormalize: bool = False


class IngestPostings:
    """Normalise, dédoublonne et enregistre des annonces brutes, par lot.

    L'ordre des contrôles va du moins cher au plus cher (voir
    `domain/dedup.py`) : identifiant de source (une requête pour le lot),
    condensat exact (une requête), puis SimHash pour ce qui reste. Le
    plongement n'a lieu qu'ensuite, dans `SyncIndex`, et seulement pour
    les offres **canoniques** dont le contenu n'a jamais été plongé.
    """

    def __init__(
        self,
        *,
        offers: OfferRepository,
        outbox: IndexOutbox,
        geocoder: Geocoder,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._offers = offers
        self._outbox = outbox
        self._geocoder = geocoder
        self._clock = clock
        self._uow = uow

    async def execute(
        self, postings: Sequence[RawPosting], *, renormalize: bool = False
    ) -> IngestStats:
        """Traite un lot. Une annonce en échec n'arrête pas les autres.

        `renormalize` rejoue la normalisation des annonces déjà connues même
        inchangées — après une correction des règles de `normalization.py`.
        Le contenu étant le même, aucun plongement n'est recalculé : seuls
        les attributs filtrables de l'index sont réécrits.
        """
        batch = _Batch(
            now=self._clock.now(),
            stats=IngestStats(received=len(postings)),
            renormalize=renormalize,
        )
        unique: dict[tuple[str, str], RawPosting] = {}
        withdrawn: dict[tuple[str, str], RawPosting] = {}
        for posting in postings:
            key = (posting.source.value, posting.source_id)
            if posting.expired:
                withdrawn.setdefault(key, posting)
            elif not posting.title.strip():
                batch.stats.errors += 1
            else:
                unique.setdefault(key, posting)
        known = await self._offers.find_by_sources([*unique, *withdrawn])
        for key in withdrawn:
            if key not in unique and (offer := known.get(key)) is not None and offer.is_active:
                batch.expired.append(dataclasses.replace(offer, is_active=False))
                batch.stats.expired += 1

        candidates: list[Offer] = []
        for key, posting in unique.items():
            try:
                offer = await self._triage(posting, known.get(key), batch)
            except Exception:
                batch.stats.errors += 1
                logger.exception(
                    "offers.ingest_failed", source=posting.source.value, source_id=posting.source_id
                )
                continue
            if offer is not None:
                candidates.append(offer)

        await self._deduplicate(candidates, batch)
        await self._persist(batch)
        stats = batch.stats
        logger.info(
            "offers.ingested",
            received=stats.received,
            inserted=stats.inserted,
            refreshed=stats.refreshed,
            updated=stats.updated,
            duplicates=stats.duplicates,
            near_duplicates=stats.near_duplicates,
            expired=stats.expired,
            errors=stats.errors,
        )
        return stats

    async def _triage(
        self, posting: RawPosting, known: Offer | None, batch: _Batch
    ) -> Offer | None:
        """Revue, modifiée ou nouvelle. Rend l'offre à dédoublonner, `None` si simplement revue."""
        if (
            known is not None
            and not batch.renormalize
            and known.content_hash == posting_hash(posting)
        ):
            batch.seen.append(known.id)
            batch.stats.refreshed += 1
            if known.is_canonical:
                self._republish(known, batch, published_at_of(posting, now=batch.now))
            return None
        # Une source qui situe elle-même ses annonces (France Travail) n'a
        # pas besoin du géocodeur : c'est un appel réseau de moins par offre.
        place = source_place(posting)
        if place is None:
            hint = parse_location_label(posting.location_label)
            place = await self._geocoder.geocode(hint.query) if hint.query else None
        offer = normalize_posting(posting, now=batch.now, place=place)
        company = offer.company.name if offer.company else None
        offer = dataclasses.replace(
            offer,
            content_hash=content_hash(offer.title, company, offer.description),
            simhash=simhash64(canonical_text(offer.title, company, offer.description)),
        )
        if known is not None:
            # Même annonce, contenu modifié : on garde son identité et son
            # histoire ; la déduplication et le plongement se rejouent.
            offer = dataclasses.replace(
                offer,
                id=known.id,
                first_seen_at=known.first_seen_at,
                is_active=True,
                indexed_version=known.indexed_version,
                indexed_hash=known.indexed_hash,
                indexed_at=known.indexed_at,
                published_at=max(known.published_at, offer.published_at),
            )
            batch.changed.append(offer)
            batch.stats.updated += 1
        return offer

    def _republish(self, canonical: Offer, batch: _Batch, published_at: datetime) -> None:
        """Une offre canonique revue sous une date plus récente vit plus longtemps.

        La date d'une grappe de doublons est celle de sa dernière parution :
        l'annonce reprise vingt jours plus tard sur un autre site garde la
        canonique en vie, au lieu de mourir avec elle.
        """
        current = batch.republished.get(canonical.id, canonical)
        if published_at > current.published_at:
            batch.republished[canonical.id] = dataclasses.replace(
                current, published_at=published_at, last_seen_at=batch.now, is_active=True
            )

    async def _deduplicate(self, candidates: list[Offer], batch: _Batch) -> None:
        """Doublons exacts (base et lot), puis quasi-doublons (base et lot)."""
        changed_ids = {offer.id for offer in batch.changed}
        canonical_by_hash = await self._offers.find_canonical_by_hashes(
            list({offer.content_hash for offer in candidates})
        )
        accepted: list[Offer] = []
        for candidate in candidates:
            canonical = canonical_by_hash.get(candidate.content_hash)
            if canonical is not None and canonical.id == candidate.id:
                canonical = None
            if canonical is not None:
                batch.stats.duplicates += 1
            else:
                canonical = await self._near_duplicate(candidate, accepted, batch.now)
                if canonical is not None:
                    batch.stats.near_duplicates += 1
            if canonical is not None and canonical.source is Source.FRANCE_TRAVAIL:
                batch.stats.overlap_france_travail += 1
            offer = dataclasses.replace(
                candidate, duplicate_of=canonical.id if canonical is not None else None
            )
            if canonical is not None:
                batch.seen.append(canonical.id)
                self._republish(canonical, batch, offer.published_at)
            else:
                canonical_by_hash[offer.content_hash] = offer
                accepted.append(offer)
            if offer.id in changed_ids:
                batch.changed = [offer if o.id == offer.id else o for o in batch.changed]
            else:
                batch.new.append(offer)
                if offer.duplicate_of is None:
                    batch.stats.inserted += 1
                    batch.stats.inserted_ids.append(offer.id)

    async def _near_duplicate(
        self, offer: Offer, accepted: Sequence[Offer], now: datetime
    ) -> Offer | None:
        company_key = fold(offer.company.name) if offer.company else None
        for other in accepted:
            other_key = fold(other.company.name) if other.company else None
            if other_key == company_key and is_near_duplicate(other.simhash, offer.simhash):
                return other
        candidates = await self._offers.near_duplicate_candidates(
            bands=bands(offer.simhash),
            company_key=company_key,
            since=now - timedelta(days=NEAR_DUPLICATE_WINDOW_DAYS),
        )
        best: Offer | None = None
        for candidate in candidates:
            if candidate.id == offer.id or not candidate.is_canonical:
                continue
            if is_near_duplicate(candidate.simhash, offer.simhash) and (
                best is None
                or hamming(candidate.simhash, offer.simhash) < hamming(best.simhash, offer.simhash)
            ):
                best = candidate
        return best

    async def _persist(self, batch: _Batch) -> None:
        """Une transaction : les offres, et ce que l'index doit en apprendre."""
        republished_ids = set(batch.republished)
        to_sync = [
            *(offer.id for offer in batch.new if offer.duplicate_of is None),
            *(offer.id for offer in batch.changed),
            *republished_ids,
            *(offer.id for offer in batch.expired),
        ]
        seen = [
            offer_id for offer_id in dict.fromkeys(batch.seen) if offer_id not in republished_ids
        ]
        async with self._uow.atomic():
            if batch.new:
                await self._offers.insert_many(batch.new)
            for offer in [*batch.changed, *batch.republished.values(), *batch.expired]:
                await self._offers.save(offer)
            if seen:
                await self._offers.mark_seen(seen, at=batch.now)
            if to_sync:
                await self._outbox.enqueue(list(dict.fromkeys(to_sync)), at=batch.now)


@dataclass(frozen=True, slots=True)
class LeasedTarget:
    """Une cible confiée au collecteur : un terme court, un lieu, un rayon — rien d'une personne."""

    key: str
    search_term: str
    location: str
    distance_km: int | None
    #: Ce que le dernier passage avait trouvé : le collecteur y lit un « zéro silencieux ».
    last_found: int = 0


class LeaseScrapeTargets:
    """Ce que le collecteur doit aller chercher maintenant — le pilotage par la demande.

    Les cibles rendues sont **louées** : leur prochaine collecte est
    repoussée de `TARGET_LEASE`, pour que deux collecteurs (ou deux passages
    rapprochés) ne demandent pas la même chose. Le collecteur rend la main
    en remettant un lot (`ReceiveBatch`), qui fixe la vraie échéance suivante.
    """

    def __init__(self, *, targets: ScrapeTargetRepository, clock: Clock) -> None:
        self._targets = targets
        self._clock = clock

    async def execute(self, *, limit: int = TARGETS_PER_RUN) -> list[LeasedTarget]:
        """Les cibles dues, par priorité : demande, manque, rendement, ancienneté, redondance."""
        now = self._clock.now()
        candidates = await self._targets.due(now=now, limit=limit * DUE_CANDIDATES_FACTOR)
        if not candidates:
            logger.info("offers.targets_leased", count=0)
            return []
        demand = demand_scores(await self._targets.all_demands(), now)
        ordered = sorted(
            candidates,
            key=lambda stored: priority(
                stored.target,
                stored.stats,
                demand=demand.get(stored.key, 0.0),
                thin_since=stored.thin_since,
                last_scraped_at=stored.last_scraped_at,
                now=now,
            ),
            reverse=True,
        )[:limit]
        await self._targets.lease([stored.key for stored in ordered], until=now + TARGET_LEASE)
        logger.info("offers.targets_leased", count=len(ordered), candidates=len(candidates))
        return [
            LeasedTarget(
                key=stored.key,
                search_term=stored.target.query_term,
                location=stored.target.location,
                distance_km=stored.target.distance_km,
                last_found=stored.stats.last_found,
            )
            for stored in ordered
        ]


class ReceiveBatch:
    """Le collecteur remet un lot : on l'atterrit tel quel, on diffère son ingestion.

    La requête du collecteur ne fait qu'écrire un objet et différer un
    travail : elle reste courte, et une ingestion en échec se rejoue depuis
    l'atterrissage sans redemander quoi que ce soit au collecteur.
    """

    def __init__(
        self,
        *,
        landing: LandingStore,
        targets: ScrapeTargetRepository,
        queue: JobQueue,
        clock: Clock,
    ) -> None:
        self._landing = landing
        self._targets = targets
        self._queue = queue
        self._clock = clock

    async def execute(self, payload: bytes, *, target_key: str | None) -> str:
        """Rend la clé du lot atterri."""
        if len(payload) > BATCH_MAX_BYTES:
            raise BatchRejectedError("batch_too_large")
        if target_key is not None and await self._targets.find(target_key) is None:
            raise BatchRejectedError("target_unknown")
        key = await self._landing.store(uuid4(), payload, at=self._clock.now())
        await self._queue.defer_ingest_batch(key, target_key)
        logger.info("offers.batch_received", key=key, bytes=len(payload), target=target_key)
        return key


class IngestBatch:
    """Ingère un lot atterri, et clôt la cible qu'il servait."""

    def __init__(
        self,
        *,
        landing: LandingStore,
        ingest: IngestPostings,
        targets: ScrapeTargetRepository,
        runs: IngestionRunRepository,
        queue: JobQueue,
        clock: Clock,
        companies: AttachCompanies | None = None,
    ) -> None:
        self._landing = landing
        self._ingest = ingest
        self._targets = targets
        self._runs = runs
        self._queue = queue
        self._clock = clock
        self._companies = companies

    async def execute(self, key: str, *, target_key: str | None) -> IngestionReport:
        """Lit, traduit, ingère, et rend le bilan."""
        started = self._clock.now()
        records = await self._landing.read(key)
        postings = [p for p in (posting_from_record(r) for r in records[:BATCH_MAX_POSTINGS]) if p]
        rejected = len(records) - len(postings)
        stats = await self._ingest.execute(postings) if postings else IngestStats()
        if stats.inserted or stats.updated or stats.expired:
            await self._queue.defer_index_sync()
        await self._attach_companies(postings)
        now = self._clock.now()
        target = await self._targets.find(target_key) if target_key is not None else None
        if target is not None:
            await self._settle_target(target, records, stats, now)
        report = IngestionReport(
            run_id=uuid4(),
            started_at=started,
            finished_at=now,
            targets=1 if target_key else 0,
            fetched=len(records),
            inserted=stats.inserted,
            refreshed=stats.refreshed,
            duplicates=stats.duplicates,
            near_duplicates=stats.near_duplicates,
            errors=stats.errors + rejected,
            updated=stats.updated,
            landing_keys=(key,),
        )
        await self._runs.record(report)
        logger.info(
            "offers.batch_ingested",
            key=key,
            fetched=report.fetched,
            inserted=report.inserted,
            refreshed=report.refreshed,
            updated=report.updated,
            duplicates=report.duplicates + report.near_duplicates,
            rejected=rejected,
            new_share=round(report.new_share, 3),
        )
        return report

    async def _attach_companies(self, postings: Sequence[RawPosting]) -> None:
        """Les employeurs du lot, et leurs logos à chercher.

        Une panne ici ne perd pas le lot : les offres sont déjà enregistrées,
        le rattrapage (`scripts.companies backfill`) reprendra le lien.
        """
        if self._companies is None:
            return
        try:
            report = await self._companies.execute(
                [s for s in (sighting_of(p) for p in postings) if s is not None]
            )
            if report.logos_due:
                await self._queue.defer_logo_fetch()
        except Exception:
            logger.exception("offers.companies_attach_failed")

    async def _settle_target(
        self,
        target: StoredTarget,
        records: Sequence[Mapping[str, object]],
        stats: IngestStats,
        now: datetime,
    ) -> None:
        """Rendement, redondance avec France Travail, porte d'intitulé : la cible apprend.

        Une cible à l'essai qui ne rapporte rien, ou dont les titres ne
        disent pas son métier, est rétrogradée ; une cible qui rapportait et
        se tait d'un coup est une anomalie journalisée (page changée ?).
        """
        titles = [title for r in records if isinstance(title := r.get("title"), str)]
        outcome = ScrapeOutcome(
            found=len(records),
            new=stats.inserted,
            overlap_france_travail=stats.overlap_france_travail,
            gate_rate=gate_rate(target.target.search_term, titles),
        )
        learned = after_scrape(target.stats, outcome)
        await self._targets.record_scrape(
            target.key, learned, at=now, next_at=now + interval_for(target.target.origin, learned)
        )
        if target.stats.state is TargetState.PROBATION and learned.state is TargetState.DEMOTED:
            logger.warning(
                "offers.target_demoted",
                target=target.key,
                found=outcome.found,
                gate_rate=outcome.gate_rate,
            )
        elif outcome.gate_rate is not None and outcome.gate_rate < PROBATION_MIN_GATE_RATE:
            # Le signal de la boucle des synonymes : les titres disent le métier autrement.
            logger.info(
                "offers.target_offtarget",
                target=target.key,
                found=outcome.found,
                new=outcome.new,
                gate_rate=round(outcome.gate_rate, 2),
            )
        if target.stats.yield_ema >= YIELD_ANOMALY_FLOOR and outcome.new == 0:
            logger.warning(
                "offers.target_yield_anomaly",
                target=target.key,
                expected=target.stats.yield_ema,
                found=outcome.found,
            )


# ---------------------------------------------------------------------
# 2. Index
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncReport:
    """Ce qu'un passage de synchronisation a fait."""

    claimed: int = 0
    embedded: int = 0
    payloads: int = 0
    removed: int = 0
    alerts: AlertSummary | None = None


class SyncIndex:
    """Vide la boîte d'envoi : l'index rejoint la base.

    Pour chaque offre inscrite, l'opération se déduit de son état :

    - indexable et déjà plongée sur ce contenu à cette version → seuls les
      attributs filtrables sont réécrits (aucun calcul de plongement) ;
    - indexable sinon → plongement, puis écriture ;
    - plus indexable (expirée, devenue doublon, disparue) → retrait.

    Les alertes partent des offres **nouvellement plongées** : une offre
    dont seuls les attributs changent n'en déclenche pas.
    """

    def __init__(
        self,
        *,
        offers: OfferRepository,
        outbox: IndexOutbox,
        embedder: TextEmbedder,
        index: OfferIndex,
        alerts: DispatchAlerts | None,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._offers = offers
        self._outbox = outbox
        self._embedder = embedder
        self._index = index
        self._alerts = alerts
        self._clock = clock
        self._uow = uow

    @property
    def version(self) -> str:
        """La version d'index courante : modèle et composition du texte."""
        return index_version(self._embedder.version)

    async def execute(self, *, limit: int = INDEX_BATCH_SIZE) -> SyncReport:
        """Traite un lot de la boîte d'envoi."""
        items = await self._outbox.claim(limit=limit)
        if not items:
            return SyncReport()
        ids = list(dict.fromkeys(item.offer_id for item in items))
        found = await self._offers.find_many(ids)
        version = self.version
        to_remove = [i for i in ids if i not in found or not found[i].indexable]
        live = [found[i] for i in ids if i in found and found[i].indexable]
        to_refresh = [offer for offer in live if offer.embedding_current(version)]
        to_embed = [offer for offer in live if not offer.embedding_current(version)]

        indexed: list[IndexedOffer] = []
        if to_embed:
            vectors = await self._embedder.embed_documents([embedding_text(o) for o in to_embed])
            indexed = [
                IndexedOffer(
                    offer=offer,
                    dense=tuple(vector),
                    title_text=title_text(offer),
                    body_text=body_text(offer),
                )
                for offer, vector in zip(to_embed, vectors, strict=True)
            ]
            await self._index.upsert(indexed)
        if to_refresh:
            await self._index.update_payloads(to_refresh)
        if to_remove:
            await self._index.delete(to_remove)
        now = self._clock.now()
        async with self._uow.atomic():
            await self._offers.mark_indexed(
                [(offer.id, offer.content_hash) for offer in [*to_embed, *to_refresh]],
                version=version,
                at=now,
            )
            await self._offers.mark_unindexed([i for i in to_remove if i in found])
            await self._outbox.ack([item.id for item in items])
        summary = await self._dispatch(indexed)
        logger.info(
            "offers.index_synced",
            claimed=len(items),
            embedded=len(indexed),
            payloads=len(to_refresh),
            removed=len(to_remove),
            version=version,
        )
        return SyncReport(
            claimed=len(items),
            embedded=len(indexed),
            payloads=len(to_refresh),
            removed=len(to_remove),
            alerts=summary,
        )

    async def _dispatch(self, indexed: Sequence[IndexedOffer]) -> AlertSummary | None:
        """Les alertes ne doivent jamais bloquer l'index : une panne se journalise."""
        if self._alerts is None or not indexed:
            return None
        try:
            return await self._alerts.execute(indexed)
        except Exception:
            logger.exception("offers.alerts_failed", offers=len(indexed))
            return None


class ExpireOffers:
    """Désactive les offres publiées (ou republiées) depuis plus de `OFFER_ACTIVE_TTL`."""

    def __init__(
        self, *, offers: OfferRepository, outbox: IndexOutbox, clock: Clock, uow: UnitOfWork
    ) -> None:
        self._offers = offers
        self._outbox = outbox
        self._clock = clock
        self._uow = uow

    async def execute(self) -> int:
        """Rend le nombre désactivé ; l'index les retire au passage suivant."""
        now = self._clock.now()
        async with self._uow.atomic():
            expired = await self._offers.deactivate_published_before(now - OFFER_ACTIVE_TTL, at=now)
            if expired:
                await self._outbox.enqueue(expired, at=now)
        logger.info("offers.expired", count=len(expired))
        return len(expired)


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """L'écart entre la base et l'index, et ce qui a été réinscrit."""

    expected: int
    indexed: int
    requeued: int


class ReconcileIndex:
    """Réinscrit ce que l'index ignore ou porte à tort — et tout, au changement de modèle.

    Commande explicite (`scripts/offers.py reconcile`), jamais au démarrage :
    c'est elle qu'on lance après un changement de modèle ou de composition
    du texte plongé, en connaissant son coût.
    """

    def __init__(
        self,
        *,
        offers: OfferRepository,
        outbox: IndexOutbox,
        index: OfferIndex,
        embedder: TextEmbedder,
        clock: Clock,
    ) -> None:
        self._offers = offers
        self._outbox = outbox
        self._index = index
        self._embedder = embedder
        self._clock = clock

    async def execute(self, *, limit: int = 100_000) -> ReconcileReport:
        """Compare, réinscrit, et rend le bilan."""
        version = index_version(self._embedder.version)
        stale = await self._offers.stale_index_ids(version=version, limit=limit)
        if stale:
            await self._outbox.enqueue(stale, at=self._clock.now())
        report = ReconcileReport(
            expected=await self._offers.count_indexable(),
            indexed=await self._index.count(),
            requeued=len(stale),
        )
        logger.info(
            "offers.index_reconciled",
            expected=report.expected,
            indexed=report.indexed,
            requeued=report.requeued,
        )
        return report


# ---------------------------------------------------------------------
# 4. Recherche
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Ce qu'une recherche rend : les offres classées, et le vecteur de la demande."""

    ranked: list[RankedOffer]
    offers: dict[UUID, Offer]
    query_vector: tuple[float, ...]
    #: Candidats retrouvés par l'index.
    candidates: int
    #: Retenues par le filtre dur, écartées par la porte d'intitulé : les voisines.
    rejected: tuple[Offer, ...] = ()


class SearchOffers:
    """Rappel hybride sur l'index, puis la décision du domaine (filtre dur, porte d'intitulé)."""

    def __init__(
        self,
        *,
        embedder: TextEmbedder,
        index: OfferIndex,
        offers: OfferRepository,
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._offers = offers

    async def execute(
        self,
        criteria: SearchCriteria,
        *,
        exclude: frozenset[UUID] = frozenset(),
        limit: int = RETRIEVAL_LIMIT,
    ) -> SearchResult:
        """Cherche pour ces critères ; `limit` borne le rappel (moins pour un simple comptage)."""
        dense = await self._embedder.embed_query(criteria.dense_text())
        candidates = await self._index.search(
            IndexQuery(
                dense=dense,
                title_terms=" ".join(criteria.job_titles),
                body_terms=" ".join(criteria.lexical_terms()),
                criteria=criteria,
                limit=limit,
            )
        )
        window = [c for c in candidates if c.offer_id not in exclude]
        # Sans la description : ni le filtre dur ni la porte d'intitulé ne la lisent.
        found = (
            await self._offers.find_many([c.offer_id for c in window], light=True) if window else {}
        )
        ordered = [found[c.offer_id] for c in window if c.offer_id in found]
        ranked = rank(ordered, criteria)
        rejected = tuple(gate_rejects(ordered, criteria))
        logger.info(
            "offers.searched", candidates=len(candidates), kept=len(ranked), rejected=len(rejected)
        )
        return SearchResult(
            ranked=ranked,
            offers=found,
            query_vector=dense,
            candidates=len(candidates),
            rejected=rejected,
        )


@dataclass(frozen=True, slots=True)
class AppliedCriteria:
    """Ce que l'application de critères a produit."""

    stored: StoredCriteria
    results: int
    #: La recherche elle-même (ses voisines nourrissent les élargissements).
    search: SearchResult | None = None


class ApplyCriteria:
    """Fait d'un jeu de critères le fil de la personne, l'inscrit aux alertes, et pèse sur la collecte.

    **La recherche ne lit que notre index.** Aucune conversation ne déclenche
    une requête à France Travail ni à un site : ce cas d'usage ne connaît ni
    collecteur ni source. Il ne fait que demander des cibles — courtes,
    canoniques, validées contre le vocabulaire des titres réels — que le
    planificateur collectera de son côté ; une recherche maigre les rend
    dues tout de suite, et les alertes préviennent quand les offres arrivent.
    """

    def __init__(
        self,
        *,
        criteria: CriteriaRepository,
        criteria_index: CriteriaIndex,
        search: SearchOffers,
        feed: FeedRepository,
        states: OfferStateRepository,
        targets: ScrapeTargetRepository,
        index: OfferIndex,
        offers: OfferRepository,
        geocoder: Geocoder,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._criteria = criteria
        self._criteria_index = criteria_index
        self._search = search
        self._feed = feed
        self._states = states
        self._targets = targets
        self._index = index
        self._offers = offers
        self._geocoder = geocoder
        self._clock = clock
        self._uow = uow

    async def execute(self, user_id: UUID, criteria: SearchCriteria) -> AppliedCriteria:
        """Cherche, reconstruit le fil, enregistre les critères."""
        criteria = await self.locate(criteria)
        if not criteria.is_searchable:
            raise CriteriaIncompleteError(criteria.missing())
        now = self._clock.now()
        previous = await self._criteria.find(user_id)
        states = await self._states.all_of_user(user_id)
        dismissed = frozenset(i for i, state in states.items() if state.dismissed)
        result = await self._search.execute(criteria, exclude=dismissed)
        stored = StoredCriteria(
            user_id=user_id,
            criteria=criteria,
            fingerprint=criteria.fingerprint(),
            alerts_enabled=previous.alerts_enabled if previous else True,
            updated_at=now,
            last_alert_at=previous.last_alert_at if previous else None,
            alerts_sent_on=previous.alerts_sent_on if previous else None,
            alerts_sent_today=previous.alerts_sent_today if previous else 0,
        )
        current = await self._feed.entries_of(user_id)
        entries = [
            FeedEntry(
                user_id=user_id,
                offer_id=item.offer_id,
                score=item.score,
                quality=item.quality,
                reasons=item.reasons,
                origin=FeedOrigin.SEARCH,
                created_at=now,
            )
            for item in result.ranked
        ]
        removals = entries_to_remove(list(current), {e.offer_id for e in entries}, states)
        async with self._uow.atomic():
            await self._criteria.save(stored)
            await self._feed.replace(user_id, entries, remove=removals)
        await self._criteria_index.upsert(
            user_id, result.query_vector, criteria, alerts_enabled=stored.alerts_enabled
        )
        thin = len(entries) < THIN_RESULTS
        targets = targets_of(criteria, await self._validated_terms(criteria))
        if targets:
            await self._targets.upsert_many(targets, at=now, due_now=thin)
        await self._targets.replace_demands(user_id, [t.key for t in targets], at=now)
        logger.info(
            "offers.criteria_applied",
            user_id=str(user_id),
            entries=len(entries),
            removed=len(removals),
            thin=thin,
            targets=[t.key for t in targets],
        )
        return AppliedCriteria(stored=stored, results=len(entries), search=result)

    async def dismissed_of(self, user_id: UUID) -> frozenset[UUID]:
        """Les offres que la personne a écartées : jamais recomptées."""
        states = await self._states.all_of_user(user_id)
        return frozenset(i for i, state in states.items() if state.dismissed)

    async def _validated_terms(self, criteria: SearchCriteria) -> list[str]:
        """Les termes de collecte confirmés par des titres réels de l'index.

        Un terme que l'index rapproche de titres dont aucun ne dit ce métier
        est refusé (il chercherait autre chose). Un terme que l'index ne
        connaît pas du tout est admis **à l'essai** : le corpus est peut-être
        jeune, et le premier passage tranchera (`after_scrape`).
        """
        terms = candidate_terms(criteria)
        samples: dict[str, list[str]] = {}
        unknown: list[str] = []
        for term in terms:
            ids = await self._index.title_candidates(term, limit=VOCABULARY_SAMPLE)
            if not ids:
                unknown.append(term)
                continue
            found = await self._offers.find_many(ids, light=True)
            samples[term] = [offer.title for offer in found.values()]
        kept = validate_terms([t for t in terms if t in samples], samples)
        for term in terms:
            if term in samples and term not in kept:
                logger.info("offers.target_term_rejected", term=term, sampled=len(samples[term]))
        return [t for t in terms if t in kept or t in unknown]

    async def locate(self, criteria: SearchCriteria) -> SearchCriteria:
        """Un lieu nommé mais sans coordonnées ni département passe par le géocodeur.

        Le modèle de langage nomme le lieu (« Paris »), il ne le situe pas :
        c'est ici que « Paris » devient un point, **avant** que le domaine
        décide si la demande est complète.
        """
        location = criteria.location
        if location is None or location.is_usable or not location.label.strip():
            return criteria
        place = await self._geocoder.geocode(location.label)
        if place is None:
            logger.info("offers.location_unresolved", label=location.label)
            return criteria
        return criteria.model_copy(
            update={
                "location": location.model_copy(
                    update={
                        "latitude": place.latitude,
                        "longitude": place.longitude,
                        "department_code": place.department_code,
                        "city": place.city,
                    }
                )
            }
        )


class GetCriteria:
    """Les critères courants d'une personne."""

    def __init__(self, *, criteria: CriteriaRepository) -> None:
        self._criteria = criteria

    async def execute(self, user_id: UUID) -> StoredCriteria | None:
        """Rend les critères, ou `None`."""
        return await self._criteria.find(user_id)


class ClearCriteria:
    """Oublie les critères : le fil redevient « toutes les offres », les alertes cessent."""

    def __init__(
        self,
        *,
        criteria: CriteriaRepository,
        criteria_index: CriteriaIndex,
        feed: FeedRepository,
        uow: UnitOfWork,
    ) -> None:
        self._criteria = criteria
        self._criteria_index = criteria_index
        self._feed = feed
        self._uow = uow

    async def execute(self, user_id: UUID) -> None:
        """Efface critères et fil ; les états (sauvegardes…) restent."""
        async with self._uow.atomic():
            await self._criteria.delete(user_id)
            await self._feed.delete_all_of_user(user_id)
        await self._criteria_index.delete(user_id)
        logger.info("offers.criteria_cleared", user_id=str(user_id))


class SetAlertsEnabled:
    """Active ou coupe les alertes, sans toucher aux critères."""

    def __init__(
        self, *, criteria: CriteriaRepository, criteria_index: CriteriaIndex, clock: Clock
    ) -> None:
        self._criteria = criteria
        self._criteria_index = criteria_index
        self._clock = clock

    async def execute(self, user_id: UUID, *, enabled: bool) -> StoredCriteria | None:
        """Rend les critères mis à jour, ou `None` s'il n'y en a pas."""
        stored = await self._criteria.find(user_id)
        if stored is None:
            return None
        updated = dataclasses.replace(stored, alerts_enabled=enabled, updated_at=self._clock.now())
        await self._criteria.save(updated)
        await self._criteria_index.set_alerts_enabled(user_id, enabled=enabled)
        return updated


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """Ce qu'un tour de conversation rend à l'app."""

    conversation_id: UUID
    message: Message
    status: DialogueStatus
    criteria: StoredCriteria | None
    results_count: int | None
    suggestions: tuple[str, ...] = ()
    intent: Intent = Intent.SEARCH
    #: Un garde-fou a repéré ou remplacé quelque chose (journalisé).
    guarded: str | None = None
    #: Les élargissements proposés, chiffrés.
    widenings: tuple[Widening, ...] = ()


#: Rappel borné pour compter un élargissement : assez pour chiffrer, moins cher qu'une recherche.
WIDENING_COUNT_LIMIT: Final = 1_000


class SuggestWidenings:
    """Compte ce que chaque relâchement rapporterait — dans notre index, en parallèle — et choisit.

    Le modèle de langage ne voit ces chiffres qu'une fois comptés ; il ne les
    produit jamais. Les intitulés voisins viennent des offres que la porte
    d'intitulé a écartées dans la recherche courante : leur gain est connu
    sans requête.
    """

    def __init__(self, *, search: SearchOffers) -> None:
        self._search = search

    async def execute(
        self,
        criteria: SearchCriteria,
        *,
        current: int,
        rejected: Sequence[Offer] = (),
        exclude: frozenset[UUID] = frozenset(),
    ) -> list[Widening]:
        """Les propositions, classées par gain."""
        candidates = relaxations(criteria)
        results = await asyncio.gather(
            *(
                self._search.execute(r.criteria, exclude=exclude, limit=WIDENING_COUNT_LIMIT)
                for r in candidates
            )
        )
        counted = [(r, len(res.ranked)) for r, res in zip(candidates, results, strict=True)]
        counted.extend((r, current) for r in title_relaxations(criteria, rejected))
        chosen = choose(current, counted)
        logger.info(
            "offers.widenings_suggested",
            current=current,
            candidates=len(counted),
            chosen=[(w.id, w.gain) for w in chosen],
        )
        return chosen


class ConverseSearch:
    """Un message de la personne → une réponse de Karria, et peut-être une recherche.

    La défense en profondeur (ADR-0018) : la constitution est épinglée par
    l'adaptateur à chaque appel ; ici, **avant** l'appel, un classement bon
    marché (`pre_classify`) répond lui-même aux questions d'identité et
    signale les injections ; **après** l'appel, `guard_reply` remplace une
    réponse qui nomme un fournisseur, laisse fuir la consigne ou invente une
    offre. Le contexte du modèle = constitution + mémoire de la personne +
    fenêtre récente (`RECENT_WINDOW`), jamais la conversation entière. La
    mémoire se met à jour **après** le tour, par la file, hors du chemin de
    la réponse.
    """

    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        criteria: CriteriaRepository,
        dialogue: SearchDialogue,
        apply: ApplyCriteria,
        memory: KarriaMemoryRepository,
        queue: JobQueue,
        clock: Clock,
        constitution_version: str,
        widen: SuggestWidenings | None = None,
    ) -> None:
        self._conversations = conversations
        self._criteria = criteria
        self._dialogue = dialogue
        self._apply = apply
        self._widen = widen
        self._memory = memory
        self._queue = queue
        self._clock = clock
        self._constitution_version = constitution_version

    async def execute(
        self, user_id: UUID, *, conversation_id: UUID | None, content: str
    ) -> ConversationTurn:
        """Joue un tour."""
        now = self._clock.now()
        turns = await self._conversations.user_turns_since(user_id, now - timedelta(days=1))
        if turns >= ASSISTANT_DAILY_LIMIT:
            raise AssistantRateLimitedError(retry_after=int(ASSISTANT_RETRY_AFTER.total_seconds()))

        if conversation_id is None:
            conversation_id = await self._conversations.create(user_id, at=now)
        elif not await self._conversations.belongs_to(conversation_id, user_id):
            raise ConversationNotFoundError(conversation_id)

        stored = await self._criteria.find(user_id)
        pending = await self._conversations.pending_widenings(conversation_id)
        flag = pre_classify(content)
        guarded: str | None = flag.value if flag else None
        results: int | None = None
        widenings: list[Widening] = []
        accepted = accepted_widening(content, pending)
        if flag is PreFlag.IDENTITY_PROBE:
            # La question d'identité ne passe pas par le modèle : la réponse
            # est celle de la constitution, identique à chaque fois.
            outcome = DialogueOutcome(
                status=DialogueStatus.CHAT,
                reply=IDENTITY_REPLY,
                criteria=stored.criteria if stored else None,
                intent=Intent.OFF_TOPIC,
            )
            reply = IDENTITY_REPLY
        elif accepted is not None:
            # Une réponse rapide reprise telle quelle : l'élargissement s'applique
            # sans repasser par le modèle — exact, immédiat.
            applied = await self._apply.execute(user_id, accepted.criteria)
            stored, results = applied.stored, applied.results
            outcome = DialogueOutcome(
                status=DialogueStatus.SEARCH,
                reply=f"C'est fait, j'ai élargi : {accepted.label}.",
                criteria=accepted.criteria,
                intent=Intent.REFINE,
            )
            widenings = await self._widenings_for(user_id, applied)
            reply = f"{outcome.reply} {announce_results(results)} {phrase_widenings(results, widenings)}".strip()
        else:
            memory = await self._memory.find(user_id)
            history = [
                (m.role, m.content)
                for m in await self._conversations.recent_messages(
                    conversation_id, limit=RECENT_WINDOW
                )
            ]
            proposal = await self._dialogue.propose(
                history=history,
                current=stored.criteria if stored else None,
                message=content,
                memory=memory.facts.render() if memory and not memory.facts.is_empty else None,
                facts=render_pending(pending),
            )
            if proposal.criteria is not None:
                proposal = dataclasses.replace(
                    proposal, criteria=await self._apply.locate(proposal.criteria)
                )
            outcome = decide(proposal, stored.criteria if stored else None)
            verdict = guard_reply(outcome.reply)
            reply = verdict.reply
            if verdict.replaced:
                guarded = verdict.reason
                logger.warning(
                    "offers.guard_replaced",
                    user_id=str(user_id),
                    reason=verdict.reason,
                    constitution=self._constitution_version,
                )
            if outcome.status is DialogueStatus.SEARCH and outcome.criteria is not None:
                applied = await self._apply.execute(user_id, outcome.criteria)
                stored, results = applied.stored, applied.results
                widenings = await self._widenings_for(user_id, applied)
                reply = (
                    f"{reply} {announce_results(results)} {phrase_widenings(results, widenings)}"
                ).strip()
        if outcome.status is DialogueStatus.SEARCH:
            await self._conversations.set_pending_widenings(conversation_id, widenings)
        suggestions = clean_suggestions((*(w.message for w in widenings), *outcome.suggestions))

        await self._conversations.add_message(conversation_id, role="user", content=content, at=now)
        message = await self._conversations.add_message(
            conversation_id, role="assistant", content=reply, at=self._clock.now()
        )
        if flag is not PreFlag.IDENTITY_PROBE and outcome.intent is not Intent.OFF_TOPIC:
            await self._queue.defer_memory_update(user_id, user_message=content, reply=reply)
        logger.info(
            "offers.conversation_turn",
            user_id=str(user_id),
            status=outcome.status.value,
            intent=outcome.intent.value,
            results=results,
            guarded=guarded,
            constitution=self._constitution_version,
        )
        return ConversationTurn(
            conversation_id=conversation_id,
            message=message,
            status=outcome.status,
            criteria=stored,
            results_count=results,
            suggestions=suggestions,
            intent=outcome.intent,
            guarded=guarded,
            widenings=tuple(widenings),
        )

    async def _widenings_for(self, user_id: UUID, applied: AppliedCriteria) -> list[Widening]:
        """Ce que Karria pourrait proposer d'élargir après cette recherche."""
        if self._widen is None or applied.search is None:
            return []
        states = await self._apply.dismissed_of(user_id)
        return await self._widen.execute(
            applied.stored.criteria,
            current=applied.results,
            rejected=applied.search.rejected,
            exclude=states,
        )


class UpdateKarriaMemory:
    """Après un tour : le modèle propose ce que Karria doit retenir, la règle fusionne et borne."""

    def __init__(
        self,
        *,
        memory: KarriaMemoryRepository,
        updater: MemoryUpdater,
        clock: Clock,
        constitution_version: str,
    ) -> None:
        self._memory = memory
        self._updater = updater
        self._clock = clock
        self._constitution_version = constitution_version

    async def execute(self, user_id: UUID, *, user_message: str, reply: str) -> KarriaMemory | None:
        """Rend la mémoire mise à jour, ou `None` si rien n'a changé."""
        current = await self._memory.find(user_id)
        facts = current.facts if current else MemoryFacts()
        proposed = await self._updater.propose(
            current=facts, user_message=user_message, reply=reply
        )
        if proposed is None or proposed.is_empty:
            return None
        merged = merge_memory(facts, proposed)
        updated = KarriaMemory(
            facts=merged,
            constitution_version=self._constitution_version,
            updated_at=self._clock.now(),
        )
        await self._memory.save(user_id, updated)
        logger.info(
            "offers.memory_updated",
            user_id=str(user_id),
            jobs=len(merged.target_jobs),
            constraints=len(merged.constraints),
        )
        return updated


class GetKarriaMemory:
    """Ce que Karria sait de la personne — elle peut le voir."""

    def __init__(self, *, memory: KarriaMemoryRepository) -> None:
        self._memory = memory

    async def execute(self, user_id: UUID) -> KarriaMemory | None:
        """La mémoire, ou `None`."""
        return await self._memory.find(user_id)


class ResetKarriaMemory:
    """La personne efface ce que Karria a retenu d'elle."""

    def __init__(self, *, memory: KarriaMemoryRepository) -> None:
        self._memory = memory

    async def execute(self, user_id: UUID) -> bool:
        """`True` si quelque chose a été effacé."""
        erased = await self._memory.delete(user_id)
        logger.info("offers.memory_reset", user_id=str(user_id), erased=erased)
        return erased


# ---------------------------------------------------------------------
# 3. Alertes
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlertSummary:
    """Ce qu'un passage d'alertes a fait."""

    offers_examined: int = 0
    people_matched: int = 0
    entries_added: int = 0
    notifications_sent: int = 0


class DispatchAlerts:
    """Matching inversé : chaque offre fraîchement indexée cherche ses lecteurs."""

    def __init__(
        self,
        *,
        criteria: CriteriaRepository,
        criteria_index: CriteriaIndex,
        feed: FeedRepository,
        states: OfferStateRepository,
        notifier: UserNotifier,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._criteria = criteria
        self._criteria_index = criteria_index
        self._feed = feed
        self._states = states
        self._notifier = notifier
        self._clock = clock
        self._uow = uow

    async def execute(self, indexed: Sequence[IndexedOffer]) -> AlertSummary:
        """Confronte ces offres aux critères, enrichit les fils, prévient."""
        now = self._clock.now()
        by_user: dict[UUID, list[Offer]] = {}
        examined = 0
        for item in indexed:
            if not item.offer.indexable or not is_fresh(item.offer, now=now):
                continue
            examined += 1
            matches = await self._criteria_index.similar(
                item.dense,
                item.offer,
                limit=ALERT_CANDIDATES_PER_OFFER,
                min_similarity=ALERT_MIN_SIMILARITY,
            )
            for match in matches:
                by_user.setdefault(match.user_id, []).append(item.offer)
        if not by_user:
            return AlertSummary(offers_examined=examined)

        stored = await self._criteria.find_many(list(by_user))
        added = sent = people = 0
        for user_id, offers in by_user.items():
            criteria = stored.get(user_id)
            if criteria is None or not criteria.alerts_enabled:
                continue
            new_entries, high = await self._settle(criteria, offers, now)
            if not new_entries:
                continue
            people += 1
            added += len(new_entries)
            await self._feed.add_many(new_entries)
            if high and may_notify(
                now=now,
                last_alert_at=criteria.last_alert_at,
                sent_today=criteria.alerts_sent_today
                if _same_day(criteria.alerts_sent_on, now)
                else 0,
            ):
                accepted = await self._notifier.notify(
                    user_id, build_notification(criteria.criteria.summary(), high)
                )
                if accepted:
                    sent += 1
                    async with self._uow.atomic():
                        await self._criteria.record_alert(user_id, at=now)
                        await self._feed.mark_notified(user_id, [o.id for o in high], at=now)
        logger.info(
            "offers.alerts_dispatched", examined=examined, people=people, entries=added, sent=sent
        )
        return AlertSummary(
            offers_examined=examined,
            people_matched=people,
            entries_added=added,
            notifications_sent=sent,
        )

    async def _settle(
        self, stored: StoredCriteria, offers: Sequence[Offer], now: datetime
    ) -> tuple[list[FeedEntry], list[Offer]]:
        """Filtre dur, porte d'intitulé, et ce qui mérite une notification."""
        criteria = stored.criteria
        accepted = [o for o in offers if criteria.accepts(o)]
        if not accepted:
            return [], []
        states = await self._states.find_many(stored.user_id, [o.id for o in accepted])
        already = set(await self._feed.offer_ids_of(stored.user_id))
        fresh = [
            o
            for o in accepted
            if not (o.id in states and states[o.id].dismissed) and o.id not in already
        ]
        if not fresh:
            return [], []
        ranked = rank(fresh, criteria)
        by_id = {o.id: o for o in fresh}
        entries = [
            FeedEntry(
                user_id=stored.user_id,
                offer_id=item.offer_id,
                score=item.score,
                quality=item.quality,
                reasons=item.reasons,
                origin=FeedOrigin.ALERT,
                created_at=now,
            )
            for item in ranked
        ]
        high = [by_id[item.offer_id] for item in ranked if item.score >= ALERT_MIN_SCORE]
        return entries, high


def _same_day(moment: datetime | None, now: datetime) -> bool:
    return moment is not None and moment.date() == now.date()


# ---------------------------------------------------------------------
# 5. Le fil
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeedItem:
    """Une offre telle que la personne la voit : l'offre, sa place, son état.

    `state` vaut `None` pour un lecteur anonyme : il n'a rien fait de
    l'offre, et il n'y a personne à qui rattacher un état.
    """

    offer: Offer
    entry: FeedEntry | None
    state: OfferState | None
    #: Distance au lieu de la recherche, en km arrondis au dixième ; `None` sans point de
    #: référence ou sans coordonnées.
    distance_km: float | None = None


@dataclass(frozen=True, slots=True)
class FeedPage:
    """Une page du fil."""

    mode: FeedMode
    items: list[FeedItem]
    total: int
    criteria_summary: str | None
    #: L'ordre réellement servi : `RELEVANCE` demandé sans recherche devient `RECENT`.
    sort: FeedSort = FeedSort.RECENT
    #: « Plus proches » est possible : la personne a une recherche localisée.
    nearest_available: bool = False


def _distance_or_last(distance: float | None) -> float:
    return math.inf if distance is None else distance


def distance_to(offer: Offer, near: tuple[float, float] | None) -> float | None:
    """La distance affichable de l'offre au point, ou `None`."""
    location = offer.location
    if near is None or location is None or location.latitude is None or location.longitude is None:
        return None
    return round(haversine_km(near[0], near[1], location.latitude, location.longitude), 1)


@dataclass(frozen=True, slots=True)
class FeedRequest:
    """Ce que la route demande.

    `user_id` à `None` : un lecteur **anonyme** (ADR-0025). Il ne voit que
    `ALL_OFFERS`, sans critères, sans état, sans filtre personnel — la route
    a déjà refusé `saved_only` et consorts.
    """

    user_id: UUID | None
    limit: int
    offset: int
    sort: FeedSort = FeedSort.RELEVANCE
    saved_only: bool = False
    unseen_only: bool = False
    unapplied_only: bool = False
    contract_types: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    published_after: datetime | None = None
    #: Le mode voulu par l'app ; `None` le déduit des critères (le comportement d'origine).
    #: `ALL_OFFERS` montre tout le corpus **sans toucher aux critères** : la recherche reste
    #: enregistrée et ses alertes actives. `SEARCH` sans critères retombe sur `ALL_OFFERS`
    #: (il n'y a rien à montrer d'autre) ; la page rend toujours le mode réellement servi.
    mode: FeedMode | None = None


class GetFeed:
    """Le fil : les offres retenues si des critères existent, tout le corpus sinon.

    L'app peut demander `ALL_OFFERS` alors que des critères existent : c'est la « pause »
    de la recherche, qui ne supprime plus les critères et laisse donc les alertes vivre.
    """

    def __init__(
        self,
        *,
        criteria: CriteriaRepository,
        feed: FeedRepository,
        offers: OfferRepository,
        states: OfferStateRepository,
    ) -> None:
        self._criteria = criteria
        self._feed = feed
        self._offers = offers
        self._states = states

    async def execute(self, request: FeedRequest) -> FeedPage:
        """Une page.

        Raises:
            NearestSortUnavailableError: `NEAREST` sans recherche localisée.
        """
        if request.user_id is None:
            return await self._anonymous(request)
        stored = await self._criteria.find(request.user_id)
        mode = FeedMode.SEARCH if stored else FeedMode.ALL_OFFERS
        if request.mode is FeedMode.ALL_OFFERS:
            mode = FeedMode.ALL_OFFERS
        summary = stored.criteria.summary() if stored else None
        # Le point de référence vaut dans les deux vues : « Toutes les offres »
        # triées par distance partent aussi du lieu de la recherche.
        near = stored.criteria.reference_point if stored else None
        if request.sort == FeedSort.NEAREST and near is None:
            raise NearestSortUnavailableError
        sort = request.sort
        if mode is FeedMode.ALL_OFFERS and sort == FeedSort.RELEVANCE:
            sort = FeedSort.RECENT

        contracts = tuple(ContractType(c) for c in request.contract_types if c in set(ContractType))
        if request.saved_only:
            return await self._saved(
                request,
                FeedPage(
                    mode=mode,
                    items=[],
                    total=0,
                    criteria_summary=summary,
                    sort=sort,
                    nearest_available=near is not None,
                ),
                contracts,
                near,
            )

        excluded = list(
            await self._states.offer_ids_where(
                request.user_id,
                dismissed=True,
                seen=request.unseen_only,
                applied=request.unapplied_only,
            )
        )
        filters = OfferFilters(
            contract_types=contracts,
            cities=request.cities,
            published_after=request.published_after,
            exclude_offer_ids=tuple(excluded),
        )
        if mode is FeedMode.ALL_OFFERS:
            offers, total = await self._offers.page_active(
                filters=filters,
                limit=request.limit,
                offset=request.offset,
                near=near if sort == FeedSort.NEAREST else None,
            )
            entries: dict[UUID, FeedEntry] = {}
            ordered = list(offers)
        else:
            page, total = await self._feed.page(
                FeedQuery(
                    user_id=request.user_id,
                    limit=request.limit,
                    offset=request.offset,
                    sort=sort,
                    unseen_only=request.unseen_only,
                    filters=filters,
                    near=near,
                )
            )
            found = await self._offers.find_many([e.offer_id for e in page])
            entries = {e.offer_id: e for e in page}
            ordered = [found[e.offer_id] for e in page if e.offer_id in found]
        states = await self._states.find_many(request.user_id, [o.id for o in ordered])
        items = [
            FeedItem(
                offer=o,
                entry=entries.get(o.id),
                state=states.get(o.id) or OfferState(user_id=request.user_id, offer_id=o.id),
                distance_km=distance_to(o, near),
            )
            for o in ordered
        ]
        return FeedPage(
            mode=mode,
            items=items,
            total=total,
            criteria_summary=summary,
            sort=sort,
            nearest_available=near is not None,
        )

    async def _anonymous(self, request: FeedRequest) -> FeedPage:
        """Le corpus actif, le plus récent d'abord : aucun état, aucune personnalisation."""
        if request.sort == FeedSort.NEAREST:
            # Un invité n'a pas de recherche, donc pas de lieu d'où mesurer.
            raise NearestSortUnavailableError
        contracts = tuple(ContractType(c) for c in request.contract_types if c in set(ContractType))
        offers, total = await self._offers.page_active(
            filters=OfferFilters(
                contract_types=contracts,
                cities=request.cities,
                published_after=request.published_after,
            ),
            limit=request.limit,
            offset=request.offset,
        )
        items = [FeedItem(offer=o, entry=None, state=None) for o in offers]
        return FeedPage(mode=FeedMode.ALL_OFFERS, items=items, total=total, criteria_summary=None)

    async def _saved(
        self,
        request: FeedRequest,
        shell: FeedPage,
        contracts: tuple[ContractType, ...],
        near: tuple[float, float] | None,
    ) -> FeedPage:
        """Les sauvegardées, **quelle que soit la vue** : une offre gardée ne se perd pas.

        Les autres filtres (contrat, ville, période) et le tri s'y appliquent comme au
        fil. La liste est courte (des dizaines) : filtrée et triée ici.
        """
        assert request.user_id is not None  # noqa: S101 - `execute` a déjà écarté l'anonyme
        ids = list(await self._states.offer_ids_where(request.user_id, saved=True))
        found = await self._offers.find_many(ids)
        states = await self._states.find_many(request.user_id, ids)
        kept = [
            o
            for o in (found[i] for i in ids if i in found)
            if (not contracts or o.contract_type in contracts)
            and (
                not request.cities or (o.location is not None and o.location.city in request.cities)
            )
            and (request.published_after is None or o.published_at >= request.published_after)
        ]
        if shell.sort == FeedSort.NEAREST:
            # Deux passes stables : la date départage, la distance range ; sans
            # coordonnées, en dernier.
            kept.sort(key=lambda o: (o.published_at, str(o.id)), reverse=True)
            kept.sort(key=lambda o: _distance_or_last(distance_to(o, near)))
        elif shell.sort == FeedSort.RECENT:
            kept.sort(key=lambda o: (o.published_at, str(o.id)), reverse=True)
        else:
            kept.sort(
                key=lambda o: states[o.id].saved_at or states[o.id].updated_at or o.published_at,
                reverse=True,
            )
        window = kept[request.offset : request.offset + request.limit]
        entries = {e.offer_id: e for e in await self._feed.entries_of(request.user_id)}
        items = [
            FeedItem(
                offer=o,
                entry=entries.get(o.id),
                state=states[o.id],
                distance_km=distance_to(o, near),
            )
            for o in window
        ]
        return dataclasses.replace(shell, items=items, total=len(kept))


class GetOfferForUser:
    """Une offre, avec sa place dans le fil de la personne et son état."""

    def __init__(
        self, *, offers: OfferRepository, feed: FeedRepository, states: OfferStateRepository
    ) -> None:
        self._offers = offers
        self._feed = feed
        self._states = states

    async def execute(self, user_id: UUID | None, offer_id: UUID) -> FeedItem:
        """Rend l'offre même inactive : une offre sauvegardée reste lisible.

        Sans compte (`user_id` à `None`), l'offre seule : ni place, ni état.
        """
        offer = await self._offers.find(offer_id)
        if offer is None:
            raise OfferNotFoundError(offer_id)
        if user_id is None:
            return FeedItem(offer=offer, entry=None, state=None)
        entry = await self._feed.find(user_id, offer_id)
        state = await self._states.find(user_id, offer_id) or OfferState(
            user_id=user_id, offer_id=offer_id
        )
        return FeedItem(offer=offer, entry=entry, state=state)


@dataclass(frozen=True, slots=True)
class StateChanges:
    """Ce que l'app rapporte sur une offre."""

    seen: bool | None = None
    opened: bool | None = None
    link_clicked: bool | None = None
    saved: bool | None = None
    applied: bool | None = None
    dismissed: bool | None = None
    #: La place de la carte au moment du geste, et le temps passé sur la fiche (journal).
    position: int | None = None
    dwell_ms: int | None = None


class UpdateOfferState:
    """Enregistre ce que la personne a fait d'une offre — et le consigne pour apprendre."""

    def __init__(
        self,
        *,
        offers: OfferRepository,
        feed: FeedRepository,
        states: OfferStateRepository,
        clock: Clock,
        criteria: CriteriaRepository | None = None,
        log: InteractionLog | None = None,
    ) -> None:
        self._offers = offers
        self._feed = feed
        self._states = states
        self._clock = clock
        self._criteria = criteria
        self._log = log

    async def execute(self, user_id: UUID, offer_id: UUID, changes: StateChanges) -> FeedItem:
        """Applique les changements et rend l'offre à jour."""
        offer = await self._offers.find(offer_id)
        if offer is None:
            raise OfferNotFoundError(offer_id)
        now = self._clock.now()
        current = await self._states.find(user_id, offer_id) or OfferState(
            user_id=user_id, offer_id=offer_id
        )
        updated = current.with_changes(
            at=now,
            seen=changes.seen,
            opened=changes.opened,
            link_clicked=changes.link_clicked,
            saved=changes.saved,
            applied=changes.applied,
            dismissed=changes.dismissed,
        )
        await self._states.save(updated)
        entry = await self._feed.find(user_id, offer_id)
        await self._journal(user_id, offer, entry, current, updated, changes, now)
        logger.debug("offers.state_updated", user_id=str(user_id), offer_id=str(offer_id))
        return FeedItem(offer=offer, entry=entry, state=updated)

    async def _journal(  # noqa: PLR0917 - les sept sont le contexte d'un seul geste
        self,
        user_id: UUID,
        offer: Offer,
        entry: FeedEntry | None,
        before: OfferState,
        after: OfferState,
        changes: StateChanges,
        now: datetime,
    ) -> None:
        if self._log is None:
            return
        kinds = transitions(before, after)
        if changes.dwell_ms is not None:
            kinds.append(InteractionKind.DWELL)
        if not kinds:
            return
        stored = await self._criteria.find(user_id) if self._criteria is not None else None
        shot = snapshot(offer, entry, stored.criteria if stored else None, now=now)
        await self._log.append(
            [
                InteractionEvent(
                    user_id=user_id,
                    offer_id=offer.id,
                    kind=kind,
                    at=now,
                    snapshot=shot,
                    position=changes.position,
                    dwell_ms=changes.dwell_ms if kind is InteractionKind.DWELL else None,
                )
                for kind in kinds
            ]
        )


@dataclass(frozen=True, slots=True)
class Impression:
    """Une carte affichée, et sa place dans le fil tel qu'affiché (`None` : inconnue)."""

    offer_id: UUID
    position: int | None = None


class MarkFeedSeen:
    """L'app signale ce qu'elle a affiché, par lots — chaque affichage est consigné."""

    def __init__(
        self,
        *,
        states: OfferStateRepository,
        clock: Clock,
        offers: OfferRepository | None = None,
        feed: FeedRepository | None = None,
        criteria: CriteriaRepository | None = None,
        log: InteractionLog | None = None,
    ) -> None:
        self._states = states
        self._clock = clock
        self._offers = offers
        self._feed = feed
        self._criteria = criteria
        self._log = log

    async def execute(self, user_id: UUID, impressions: Sequence[Impression]) -> int:
        """Rend le nombre d'offres nouvellement marquées vues."""
        now = self._clock.now()
        unique = list({i.offer_id: i for i in impressions}.values())
        known = await self._states.find_many(user_id, [i.offer_id for i in unique])
        updated = 0
        for impression in unique:
            state = known.get(impression.offer_id) or OfferState(
                user_id=user_id, offer_id=impression.offer_id
            )
            if state.seen:
                continue
            await self._states.save(state.with_changes(at=now, seen=True))
            updated += 1
        await self._journal(user_id, unique, now)
        return updated

    async def _journal(
        self, user_id: UUID, impressions: Sequence[Impression], now: datetime
    ) -> None:
        if self._log is None or self._offers is None or self._feed is None:
            return
        offers = await self._offers.find_many([i.offer_id for i in impressions], light=True)
        if not offers:
            return
        entries = {e.offer_id: e for e in await self._feed.entries_of(user_id)}
        stored = await self._criteria.find(user_id) if self._criteria is not None else None
        criteria = stored.criteria if stored else None
        await self._log.append(
            [
                InteractionEvent(
                    user_id=user_id,
                    offer_id=i.offer_id,
                    kind=InteractionKind.IMPRESSION,
                    at=now,
                    snapshot=snapshot(
                        offers[i.offer_id], entries.get(i.offer_id), criteria, now=now
                    ),
                    position=i.position,
                )
                for i in impressions
                if i.offer_id in offers
            ]
        )


@dataclass(frozen=True, slots=True)
class FeedFilterOptions:
    """Les valeurs de filtre disponibles, avec leurs effectifs."""

    contract_types: Mapping[str, int]
    cities: Mapping[str, int]


class GetFeedFilters:
    """Ce qu'on peut filtrer dans le fil de la personne."""

    def __init__(
        self, *, criteria: CriteriaRepository, feed: FeedRepository, offers: OfferRepository
    ) -> None:
        self._criteria = criteria
        self._feed = feed
        self._offers = offers

    async def execute(
        self, user_id: UUID | None, mode: FeedMode | None = None
    ) -> FeedFilterOptions:
        """Contrats et villes du fil (ou de tout le corpus actif sans critères).

        `mode` suit la même règle que `GetFeed` : `ALL_OFFERS` lit tout le corpus actif même
        si des critères existent ; `None` ou `SEARCH` le déduisent des critères. Sans compte,
        toujours le corpus.
        """
        stored = await self._criteria.find(user_id) if user_id is not None else None
        searching = user_id is not None and stored is not None and mode is not FeedMode.ALL_OFFERS
        ids = list(await self._feed.offer_ids_of(user_id)) if searching and user_id else None
        contracts, cities = await self._offers.filter_options(ids)
        return FeedFilterOptions(
            contract_types={kind.value: count for kind, count in contracts.items()}, cities=cities
        )


class ForgetUser:
    """Réaction à la suppression d'un compte : critères, fil, états, conversations."""

    def __init__(
        self,
        *,
        criteria: CriteriaRepository,
        criteria_index: CriteriaIndex,
        feed: FeedRepository,
        states: OfferStateRepository,
        conversations: ConversationRepository,
        targets: ScrapeTargetRepository,
        memory: KarriaMemoryRepository,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._criteria = criteria
        self._criteria_index = criteria_index
        self._feed = feed
        self._states = states
        self._conversations = conversations
        self._targets = targets
        self._memory = memory
        self._clock = clock
        self._uow = uow

    async def execute(self, user_id: UUID) -> None:
        """Efface tout ce qui concerne la personne. Idempotent."""
        async with self._uow.atomic():
            await self._criteria.delete(user_id)
            await self._memory.delete(user_id)
            await self._targets.replace_demands(user_id, [], at=self._clock.now())
            await self._feed.delete_all_of_user(user_id)
            await self._states.delete_all_of_user(user_id)
            await self._conversations.delete_all_of_user(user_id)
        await self._criteria_index.delete(user_id)
        logger.info("offers.user_forgotten", user_id=str(user_id))


# ---------------------------------------------------------------------
# Les employeurs et leurs logos
# ---------------------------------------------------------------------


@dataclass(slots=True)
class AttachReport:
    """Ce qu'un rattachement a fait."""

    created: int = 0
    updated: int = 0
    linked: int = 0
    #: Entreprises dont le logo est à chercher dès maintenant.
    logos_due: int = 0


class AttachCompanies:
    """Reconnaît l'employeur de chaque annonce, le crée au besoin, y rattache l'offre.

    Une requête pour toutes les clés du lot, une écriture pour ce qui a
    changé, une mise à jour pour les liens : un lot de 2 000 annonces coûte
    trois allers-retours, pas 2 000.
    """

    def __init__(
        self,
        *,
        companies: CompanyRepository,
        clock: Clock,
        new_id: Callable[[], UUID] = uuid7,
    ) -> None:
        self._companies = companies
        self._clock = clock
        self._new_id = new_id

    async def execute(self, sightings: Sequence[CompanySighting]) -> AttachReport:
        """Rattache ; idempotent (revoir la même annonce ne change rien)."""
        report = AttachReport()
        if not sightings:
            return report
        now = self._clock.now()
        known = await self._companies.find_by_keys(list({s.key for s in sightings}))
        dirty: dict[UUID, CompanyRecord] = {}
        created: set[UUID] = set()
        links: list[OfferLink] = []
        for sighting in sightings:
            family = known.setdefault(sighting.key, [])
            current = choose_company(family, sighting.domain)
            if current is None:
                record = new_company(self._new_id(), sighting, now=now)
                family.append(record)
                created.add(record.id)
            else:
                record = merge_sighting(current, sighting, now=now)
                if record == current:
                    record = current
                else:
                    family[family.index(current)] = record
            if record is not current:
                dirty[record.id] = record
            if sighting.offer is not None:
                links.append(OfferLink(sighting.offer[0], sighting.offer[1], record.id))
        if dirty:
            # Un lot concurrent a pu créer la même entreprise entre la lecture
            # et l'écriture : les offres se rattachent à celle qui est en base.
            persisted = await self._companies.save_many(list(dirty.values()))
            links = [
                OfferLink(
                    link.source, link.source_id, persisted.get(link.company_id, link.company_id)
                )
                for link in links
            ]
        report.created = len(created)
        report.updated = len(dirty) - len(created)
        report.linked = await self._companies.link_offers(links) if links else 0
        report.logos_due = sum(
            1
            for r in dirty.values()
            if r.logo_status is LogoStatus.PENDING
            and (r.logo_next_attempt_at is None or r.logo_next_attempt_at <= now)
        )
        return report


@dataclass(slots=True)
class LogoReport:
    """Ce qu'un passage de recherche de logos a donné."""

    claimed: int = 0
    ready: int = 0
    missing: int = 0
    retry: int = 0
    stored: int = 0
    #: Entreprises dont le logo générique a été retiré après ce passage.
    purged: int = 0
    #: Refus par raison (`too_small`, `placeholder`, `http`…), pour mesurer.
    rejections: dict[str, int] = field(default_factory=dict)


#: Entreprises prises par passage : un passage tient en une à deux minutes.
LOGO_BATCH_SIZE: Final = 200
#: Entreprises traitées en même temps ; le débit par site est borné par
#: l'adaptateur (`HttpLogoFetcher`), pas ici.
LOGO_CONCURRENCY: Final = 16


class FetchCompanyLogos:
    """Cherche, juge, normalise et range le logo des entreprises qui n'en ont pas.

    Candidats dans l'ordre de `logo_candidates` (source, V1), puis les
    icônes du site. Le premier qui passe `judge_logo` gagne ; un échec
    passager (délai, 5xx) se réessaie plus tard (`settle_logo`), un échec
    définitif ne se retente pas avec la même adresse.
    """

    def __init__(
        self,
        *,
        companies: CompanyRepository,
        fetcher: LogoFetcher,
        processor: LogoProcessor,
        store: LogoStore,
        clock: Clock,
    ) -> None:
        self._companies = companies
        self._fetcher = fetcher
        self._processor = processor
        self._store = store
        self._clock = clock

    async def execute(
        self, *, limit: int = LOGO_BATCH_SIZE, concurrency: int = LOGO_CONCURRENCY
    ) -> LogoReport:
        """Un passage ; rend son bilan (`claimed == 0` : plus rien à faire)."""
        now = self._clock.now()
        claimed = await self._companies.claim_logo_work(
            now=now, until=now + CLAIM_LEASE, limit=limit
        )
        report = LogoReport(claimed=len(claimed))
        gate = asyncio.Semaphore(concurrency)

        async def one(record: CompanyRecord) -> None:
            async with gate:
                try:
                    async with asyncio.timeout(COMPANY_DEADLINE_SECONDS):
                        settled = await self._resolve(record, report)
                except TimeoutError:
                    _count(report, "deadline")
                    settled = settle_logo(
                        record, LogoAttempt(transient_failure=True), now=self._clock.now()
                    )
                await self._companies.save_logo(settled)
            if settled.logo_status is LogoStatus.READY:
                report.ready += 1
            elif settled.logo_status is LogoStatus.MISSING:
                report.missing += 1
            else:
                report.retry += 1

        await asyncio.gather(*(one(record) for record in claimed))
        if report.ready:
            report.purged = await self._companies.purge_shared_logos(MAX_SHARED_FALLBACK_LOGO)
        if claimed:
            logger.info(
                "offers.logos_fetched",
                claimed=report.claimed,
                ready=report.ready,
                missing=report.missing,
                retry=report.retry,
                stored=report.stored,
                purged=report.purged,
                rejections=report.rejections,
            )
        return report

    async def _resolve(self, record: CompanyRecord, report: LogoReport) -> CompanyRecord:
        rejected: list[str] = []
        transient = False
        tried: set[str] = set()

        async def attempt(candidate: LogoCandidate) -> str | None:
            nonlocal transient
            tried.add(candidate.url)
            outcome = await self._try(candidate, report)
            if outcome is _TRANSIENT:
                transient = True
                return None
            if outcome is None:
                rejected.append(candidate.url)
                return None
            return outcome

        for candidate in logo_candidates(record):
            if (digest := await attempt(candidate)) is not None:
                return settle_logo(
                    record, LogoAttempt(digest, candidate.origin), now=self._clock.now()
                )
        if (
            plausible_domain(record.domain, record.key)
            and await self._companies.domain_share(record.domain) <= MAX_COMPANIES_PER_DOMAIN
        ):
            icons = [*await self._fetcher.site_icons(record.domain), *site_icon_urls(record.domain)]
            for url in dict.fromkeys(icons):
                if url in tried or url in record.logo_rejected_urls:
                    continue
                if (digest := await attempt(LogoCandidate(url, LogoOrigin.FAVICON))) is not None:
                    return settle_logo(
                        record,
                        LogoAttempt(digest, LogoOrigin.FAVICON, tuple(rejected)),
                        now=self._clock.now(),
                    )
        return settle_logo(
            record,
            LogoAttempt(rejected_urls=tuple(rejected), transient_failure=transient),
            now=self._clock.now(),
        )

    async def _try(self, candidate: LogoCandidate, report: LogoReport) -> str | None:
        """Le condensat du logo rangé, `None` si refusé, `_TRANSIENT` si à réessayer."""
        fetched = await self._fetcher.fetch_image(candidate.url)
        if fetched.payload is None:
            _count(report, "http")
            return None if fetched.permanent else _TRANSIENT
        processed = self._processor.process(fetched.payload)
        if processed is None:
            _count(report, LogoRejection.UNREADABLE.value)
            return None
        verdict = judge_logo(processed.traits, candidate.origin)
        if verdict is None and processed.digest in PLACEHOLDER_HASHES:
            verdict = LogoRejection.PLACEHOLDER
        if verdict is not None:
            _count(report, verdict.value)
            return None
        if not await self._store.has(processed.digest):
            await self._store.put(processed)
            report.stored += 1
        return processed.digest


#: Marque d'un échec passager, distincte d'un refus (`None`).
_TRANSIENT: Final = "\x00transient"


def _count(report: LogoReport, reason: str) -> None:
    report.rejections[reason] = report.rejections.get(reason, 0) + 1


@dataclass(slots=True)
class BackfillReport:
    """Ce que le rattrapage des offres existantes a fait."""

    offers: int = 0
    linked: int = 0
    created: int = 0


class BackfillCompanies:
    """Rattache les offres déjà en base à leur entreprise, par paquets (commande ponctuelle)."""

    def __init__(self, *, companies: CompanyRepository, attach: AttachCompanies) -> None:
        self._companies = companies
        self._attach = attach

    async def execute(self, *, batch: int = 2_000, active_only: bool = True) -> BackfillReport:
        """Jusqu'à épuisement, dans l'ordre des identifiants ; une offre sans clé exploitable reste."""
        report = BackfillReport()
        after: UUID | None = None
        while offers := await self._companies.unlinked_offers(
            after=after, limit=batch, active_only=active_only
        ):
            after = offers[-1].id
            sightings = [s for s in (sighting_of_offer(o) for o in offers) if s is not None]
            report.offers += len(offers)
            attached = await self._attach.execute(sightings)
            report.linked += attached.linked
            report.created += attached.created
            logger.info("offers.companies_backfilled", offers=report.offers, linked=report.linked)
        return report


def sighting_of_offer(offer: Offer) -> CompanySighting | None:
    """L'employeur d'une offre déjà normalisée (rattrapage)."""
    if offer.company is None:
        return None
    return sighting_of(
        RawPosting(
            source=offer.source,
            source_id=offer.source_id,
            title=offer.title,
            description="",
            company_name=offer.company.name,
            company_logo_url=offer.company.logo_url,
            company_url=offer.company.website,
            job_url_direct=offer.apply_url,
        )
    )
