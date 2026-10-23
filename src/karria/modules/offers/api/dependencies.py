"""Câblage du module `offers`.

Chaque **port** a sa fonction fournisseur, que les tests remplacent par
`app.dependency_overrides`. Les cas d'usage sont assemblés à partir de ces
ports et jamais remplacés. Les mêmes fabriques servent au worker et aux
commandes (`api/tasks.py`, `scripts/offers.py`), hors requête, via `build_*`.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header

from karria.modules.offers.application import (
    ApplyCriteria,
    ClearCriteria,
    ConversationRepository,
    ConverseSearch,
    CriteriaIndex,
    CriteriaRepository,
    DispatchAlerts,
    ExpireOffers,
    FeedRepository,
    ForgetUser,
    Geocoder,
    GetCriteria,
    GetFeed,
    GetFeedFilters,
    GetKarriaMemory,
    GetOfferForUser,
    IndexOutbox,
    IngestBatch,
    IngestionRunRepository,
    IngestPostings,
    JobQueue,
    KarriaMemoryRepository,
    LandingStore,
    LeaseScrapeTargets,
    MarkFeedSeen,
    OfferIndex,
    OfferRepository,
    OfferStateRepository,
    ReceiveBatch,
    ReconcileIndex,
    RefreshScrapeTargets,
    ResetKarriaMemory,
    ScrapeTargetRepository,
    SearchDialogue,
    SearchOffers,
    SetAlertsEnabled,
    SyncIndex,
    TextEmbedder,
    UpdateKarriaMemory,
    UpdateOfferState,
    UserNotifier,
)
from karria.modules.offers.infrastructure import (
    BanGeocoder,
    CachedGeocoder,
    LlmDialogue,
    LlmMemoryUpdater,
    ObjectStorageLandingStore,
    ProcrastinateJobQueue,
    QdrantCriteriaIndex,
    QdrantOfferIndex,
    TeiEmbedder,
    TortoiseConversationRepository,
    TortoiseCriteriaRepository,
    TortoiseFeedRepository,
    TortoiseGeocodeCache,
    TortoiseIndexOutbox,
    TortoiseIngestionRunRepository,
    TortoiseKarriaMemoryRepository,
    TortoiseOfferRepository,
    TortoiseOfferStateRepository,
    TortoiseScrapeTargetRepository,
    build_transport,
    collection_name,
)
from karria.modules.offers.infrastructure.constitution import CONSTITUTION_VERSION
from karria.shared.clock import Clock, SystemClock
from karria.shared.config import Settings
from karria.shared.database import TortoiseUnitOfWork
from karria.shared.errors import AuthenticationError
from karria.shared.http.dependencies import (
    LanguageModelDep,
    ObjectStorageDep,
    QueueAppDep,
    SettingsDep,
    UnitOfWorkDep,
)
from karria.shared.llm import build_language_model
from karria.shared.logging import get_logger
from karria.shared.queue import App as QueueApp
from karria.shared.storage import ObjectStorage

logger = get_logger(__name__)

# --- Ports : ce que les tests remplacent ----------------------------------


def get_clock() -> Clock:
    """L'horloge système."""
    return SystemClock()


def get_offer_repository() -> OfferRepository:
    """Les offres, dans PostgreSQL."""
    return TortoiseOfferRepository()


def get_outbox() -> IndexOutbox:
    """La boîte d'envoi vers l'index."""
    return TortoiseIndexOutbox()


def get_target_repository() -> ScrapeTargetRepository:
    """Les cibles de collecte."""
    return TortoiseScrapeTargetRepository()


def get_run_repository() -> IngestionRunRepository:
    """La trace des collectes."""
    return TortoiseIngestionRunRepository()


def get_criteria_repository() -> CriteriaRepository:
    """Les critères des personnes."""
    return TortoiseCriteriaRepository()


def get_conversation_repository() -> ConversationRepository:
    """Les conversations avec Karria."""
    return TortoiseConversationRepository()


def get_feed_repository() -> FeedRepository:
    """Les fils."""
    return TortoiseFeedRepository()


def get_state_repository() -> OfferStateRepository:
    """Ce que chaque personne a fait de chaque offre."""
    return TortoiseOfferStateRepository()


def get_offer_index(settings: SettingsDep) -> OfferIndex:
    """L'index des offres, dans la collection de cet environnement."""
    return QdrantOfferIndex(
        build_transport(settings.qdrant),
        collection_name(settings.qdrant, settings.app.environment.value, "offers"),
        settings.inference.embedding_dimension,
    )


def get_criteria_index(settings: SettingsDep) -> CriteriaIndex:
    """L'index des critères, pour les alertes."""
    return QdrantCriteriaIndex(
        build_transport(settings.qdrant),
        collection_name(settings.qdrant, settings.app.environment.value, "criteria"),
        settings.inference.embedding_dimension,
    )


def get_embedder(settings: SettingsDep) -> TextEmbedder:
    """Le serveur de plongements."""
    return TeiEmbedder.from_settings(settings.inference)


def get_geocoder(settings: SettingsDep) -> Geocoder:
    """La BAN derrière ses deux caches."""
    return CachedGeocoder(BanGeocoder.from_settings(settings.geocoding), TortoiseGeocodeCache())


def get_landing_store(storage: ObjectStorageDep) -> LandingStore:
    """L'atterrissage des lots du collecteur."""
    return ObjectStorageLandingStore(storage)


def get_job_queue(queue_app: QueueAppDep) -> JobQueue:
    """La file durable ouverte par le `lifespan`."""
    return ProcrastinateJobQueue(queue_app)


def get_search_dialogue(llm: LanguageModelDep) -> SearchDialogue:
    """Le dialogue, par le port LLM routé (tâche `search_dialogue`)."""
    return LlmDialogue(llm)


class SilentNotifier:
    """Le port `UserNotifier` tant que `bootstrap` ne l'a pas branché : n'envoie rien, le dit."""

    async def notify(self, user_id: UUID, *, title: str, body: str, data: Mapping[str, str]) -> int:
        """Journalise et rend 0."""
        _ = title, body, data
        logger.warning("offers.notifier_unwired", user_id=str(user_id))
        return 0


def get_user_notifier() -> UserNotifier:
    """**Branché par `bootstrap`** sur `devices.SendPushToUser`. Muet par défaut."""
    return SilentNotifier()


def require_collector(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    """Les routes internes n'acceptent que le jeton de service du collecteur.

    Comparaison à temps constant ; sans jeton configuré, personne n'entre.
    """
    expected = settings.collector.token.get_secret_value()
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise AuthenticationError(
            "Jeton de service invalide.", details={"reason": "collector_token_invalid"}
        )


ClockDep = Annotated[Clock, Depends(get_clock)]
OfferRepositoryDep = Annotated[OfferRepository, Depends(get_offer_repository)]
TargetRepositoryDep = Annotated[ScrapeTargetRepository, Depends(get_target_repository)]
CriteriaRepositoryDep = Annotated[CriteriaRepository, Depends(get_criteria_repository)]
ConversationRepositoryDep = Annotated[ConversationRepository, Depends(get_conversation_repository)]
FeedRepositoryDep = Annotated[FeedRepository, Depends(get_feed_repository)]
StateRepositoryDep = Annotated[OfferStateRepository, Depends(get_state_repository)]
OfferIndexDep = Annotated[OfferIndex, Depends(get_offer_index)]
CriteriaIndexDep = Annotated[CriteriaIndex, Depends(get_criteria_index)]
EmbedderDep = Annotated[TextEmbedder, Depends(get_embedder)]
GeocoderDep = Annotated[Geocoder, Depends(get_geocoder)]
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]
LandingStoreDep = Annotated[LandingStore, Depends(get_landing_store)]
SearchDialogueDep = Annotated[SearchDialogue, Depends(get_search_dialogue)]


# --- Cas d'usage : assemblés, jamais remplacés ----------------------------


def get_search_offers(
    embedder: EmbedderDep,
    index: OfferIndexDep,
    offers: OfferRepositoryDep,
) -> SearchOffers:
    """La recherche hybride."""
    return SearchOffers(embedder=embedder, index=index, offers=offers)


SearchOffersDep = Annotated[SearchOffers, Depends(get_search_offers)]


def get_apply_criteria(  # noqa: PLR0917 - FastAPI injecte par position
    criteria: CriteriaRepositoryDep,
    criteria_index: CriteriaIndexDep,
    search: SearchOffersDep,
    feed: FeedRepositoryDep,
    states: StateRepositoryDep,
    targets: TargetRepositoryDep,
    index: OfferIndexDep,
    offers: OfferRepositoryDep,
    geocoder: GeocoderDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> ApplyCriteria:
    """Des critères au fil."""
    return ApplyCriteria(
        criteria=criteria,
        criteria_index=criteria_index,
        search=search,
        feed=feed,
        states=states,
        targets=targets,
        index=index,
        offers=offers,
        geocoder=geocoder,
        clock=clock,
        uow=uow,
    )


ApplyCriteriaDep = Annotated[ApplyCriteria, Depends(get_apply_criteria)]


def get_memory_repository() -> KarriaMemoryRepository:
    """La mémoire de Karria sur chaque personne."""
    return TortoiseKarriaMemoryRepository()


MemoryRepositoryDep = Annotated[KarriaMemoryRepository, Depends(get_memory_repository)]


def get_converse_search(  # noqa: PLR0917 - FastAPI injecte par position
    conversations: ConversationRepositoryDep,
    criteria: CriteriaRepositoryDep,
    dialogue: SearchDialogueDep,
    apply: ApplyCriteriaDep,
    memory: MemoryRepositoryDep,
    queue: JobQueueDep,
    clock: ClockDep,
) -> ConverseSearch:
    """Un tour de conversation, sous la constitution courante."""
    return ConverseSearch(
        conversations=conversations,
        criteria=criteria,
        dialogue=dialogue,
        apply=apply,
        memory=memory,
        queue=queue,
        clock=clock,
        constitution_version=CONSTITUTION_VERSION,
    )


def get_get_memory(memory: MemoryRepositoryDep) -> GetKarriaMemory:
    """Lecture de la mémoire."""
    return GetKarriaMemory(memory=memory)


def get_reset_memory(memory: MemoryRepositoryDep) -> ResetKarriaMemory:
    """Effacement de la mémoire."""
    return ResetKarriaMemory(memory=memory)


GetMemoryDep = Annotated[GetKarriaMemory, Depends(get_get_memory)]
ResetMemoryDep = Annotated[ResetKarriaMemory, Depends(get_reset_memory)]


def get_get_criteria(criteria: CriteriaRepositoryDep) -> GetCriteria:
    """Lecture des critères."""
    return GetCriteria(criteria=criteria)


def get_clear_criteria(
    criteria: CriteriaRepositoryDep,
    criteria_index: CriteriaIndexDep,
    feed: FeedRepositoryDep,
    uow: UnitOfWorkDep,
) -> ClearCriteria:
    """Oubli des critères."""
    return ClearCriteria(criteria=criteria, criteria_index=criteria_index, feed=feed, uow=uow)


def get_set_alerts_enabled(
    criteria: CriteriaRepositoryDep, criteria_index: CriteriaIndexDep, clock: ClockDep
) -> SetAlertsEnabled:
    """Bascule des alertes."""
    return SetAlertsEnabled(criteria=criteria, criteria_index=criteria_index, clock=clock)


def get_get_feed(
    criteria: CriteriaRepositoryDep,
    feed: FeedRepositoryDep,
    offers: OfferRepositoryDep,
    states: StateRepositoryDep,
) -> GetFeed:
    """Une page du fil."""
    return GetFeed(criteria=criteria, feed=feed, offers=offers, states=states)


def get_get_offer(
    offers: OfferRepositoryDep, feed: FeedRepositoryDep, states: StateRepositoryDep
) -> GetOfferForUser:
    """Une offre pour une personne."""
    return GetOfferForUser(offers=offers, feed=feed, states=states)


def get_update_state(
    offers: OfferRepositoryDep, feed: FeedRepositoryDep, states: StateRepositoryDep, clock: ClockDep
) -> UpdateOfferState:
    """Une interaction."""
    return UpdateOfferState(offers=offers, feed=feed, states=states, clock=clock)


def get_mark_seen(states: StateRepositoryDep, clock: ClockDep) -> MarkFeedSeen:
    """Le « vu » par lots."""
    return MarkFeedSeen(states=states, clock=clock)


def get_get_feed_filters(
    criteria: CriteriaRepositoryDep, feed: FeedRepositoryDep, offers: OfferRepositoryDep
) -> GetFeedFilters:
    """Les options de filtre."""
    return GetFeedFilters(criteria=criteria, feed=feed, offers=offers)


def get_lease_targets(targets: TargetRepositoryDep, clock: ClockDep) -> LeaseScrapeTargets:
    """Le pilotage du collecteur."""
    return LeaseScrapeTargets(targets=targets, clock=clock)


def get_receive_batch(
    landing: LandingStoreDep, targets: TargetRepositoryDep, queue: JobQueueDep, clock: ClockDep
) -> ReceiveBatch:
    """La remise d'un lot par le collecteur."""
    return ReceiveBatch(landing=landing, targets=targets, queue=queue, clock=clock)


LeaseTargetsDep = Annotated[LeaseScrapeTargets, Depends(get_lease_targets)]
ReceiveBatchDep = Annotated[ReceiveBatch, Depends(get_receive_batch)]
ConverseSearchDep = Annotated[ConverseSearch, Depends(get_converse_search)]
GetCriteriaDep = Annotated[GetCriteria, Depends(get_get_criteria)]
ClearCriteriaDep = Annotated[ClearCriteria, Depends(get_clear_criteria)]
SetAlertsEnabledDep = Annotated[SetAlertsEnabled, Depends(get_set_alerts_enabled)]
GetFeedDep = Annotated[GetFeed, Depends(get_get_feed)]
GetOfferDep = Annotated[GetOfferForUser, Depends(get_get_offer)]
UpdateStateDep = Annotated[UpdateOfferState, Depends(get_update_state)]
MarkSeenDep = Annotated[MarkFeedSeen, Depends(get_mark_seen)]
GetFeedFiltersDep = Annotated[GetFeedFilters, Depends(get_get_feed_filters)]


# --- Hors requête : worker, commandes et ponts du bus ----------------------


def build_forget_user() -> ForgetUser:
    """L'oubli d'une personne, pour le pont `AccountDeleted` de `bootstrap/wiring.py`."""
    from karria.shared.config import get_settings  # noqa: PLC0415 - hors requête, la configuration se lit ici

    settings = get_settings()
    return ForgetUser(
        criteria=get_criteria_repository(),
        criteria_index=get_criteria_index(settings),
        feed=get_feed_repository(),
        states=get_state_repository(),
        conversations=get_conversation_repository(),
        targets=get_target_repository(),
        memory=get_memory_repository(),
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


def build_update_memory(settings: Settings) -> UpdateKarriaMemory:
    """La mise à jour de la mémoire, pour le travail `offers.update_memory`."""
    return UpdateKarriaMemory(
        memory=get_memory_repository(),
        updater=LlmMemoryUpdater(build_language_model(settings)),
        clock=get_clock(),
        constitution_version=CONSTITUTION_VERSION,
    )


def build_search_offers(settings: Settings) -> SearchOffers:
    """La recherche, hors requête."""
    return SearchOffers(
        embedder=get_embedder(settings),
        index=get_offer_index(settings),
        offers=get_offer_repository(),
    )


def build_apply_criteria(settings: Settings) -> ApplyCriteria:
    """Des critères au fil, hors requête (évaluation, amorçage d'une demande)."""
    return ApplyCriteria(
        criteria=get_criteria_repository(),
        criteria_index=get_criteria_index(settings),
        search=build_search_offers(settings),
        feed=get_feed_repository(),
        states=get_state_repository(),
        targets=get_target_repository(),
        index=get_offer_index(settings),
        offers=get_offer_repository(),
        geocoder=get_geocoder(settings),
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


def build_lease_targets() -> LeaseScrapeTargets:
    """La location des cibles, hors requête."""
    return LeaseScrapeTargets(targets=get_target_repository(), clock=get_clock())


def build_ingest_postings(settings: Settings) -> IngestPostings:
    """L'ingestion d'un lot d'annonces brutes (collecte, ou rejeu de l'atterrissage)."""
    return IngestPostings(
        offers=get_offer_repository(),
        outbox=get_outbox(),
        geocoder=get_geocoder(settings),
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


def build_ingest_batch(
    settings: Settings, storage: ObjectStorage, queue_app: QueueApp
) -> IngestBatch:
    """L'ingestion d'un lot atterri, pour le travail `offers.ingest_batch`."""
    return IngestBatch(
        landing=ObjectStorageLandingStore(storage),
        ingest=build_ingest_postings(settings),
        targets=get_target_repository(),
        runs=get_run_repository(),
        queue=ProcrastinateJobQueue(queue_app),
        clock=get_clock(),
    )


def build_refresh_targets() -> RefreshScrapeTargets:
    """Le pilotage de la collecte par la demande."""
    return RefreshScrapeTargets(targets=get_target_repository(), clock=get_clock())


def build_dispatch_alerts(settings: Settings, notifier: UserNotifier) -> DispatchAlerts:
    """Les alertes, avec le notificateur que `bootstrap` fournit."""
    return DispatchAlerts(
        criteria=get_criteria_repository(),
        criteria_index=get_criteria_index(settings),
        feed=get_feed_repository(),
        states=get_state_repository(),
        notifier=notifier,
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


def build_sync_index(settings: Settings, notifier: UserNotifier | None) -> SyncIndex:
    """La resynchronisation de l'index ; sans notificateur, aucune alerte ne part."""
    return SyncIndex(
        offers=get_offer_repository(),
        outbox=get_outbox(),
        embedder=get_embedder(settings),
        index=get_offer_index(settings),
        alerts=build_dispatch_alerts(settings, notifier) if notifier is not None else None,
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


def build_expire_offers() -> ExpireOffers:
    """L'expiration."""
    return ExpireOffers(
        offers=get_offer_repository(),
        outbox=get_outbox(),
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


def build_reconcile_index(settings: Settings) -> ReconcileIndex:
    """La réconciliation base ↔ index."""
    return ReconcileIndex(
        offers=get_offer_repository(),
        outbox=get_outbox(),
        index=get_offer_index(settings),
        embedder=get_embedder(settings),
        clock=get_clock(),
    )


async def ensure_indexes(settings: Settings) -> None:
    """Crée les collections Qdrant si besoin (commande explicite, jamais au démarrage)."""
    await get_offer_index(settings).ensure_ready()
    await get_criteria_index(settings).ensure_ready()
