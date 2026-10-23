"""Le câblage d'`offers` : chaque fournisseur rend le bon adaptateur, chaque fabrique assemble."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from procrastinate import App, testing

from karria.modules.offers.api import dependencies as deps
from karria.modules.offers.application import (
    ApplyCriteria,
    ClearCriteria,
    ConverseSearch,
    DispatchAlerts,
    ExpireOffers,
    ForgetUser,
    GetCriteria,
    GetFeed,
    GetFeedFilters,
    GetKarriaMemory,
    GetOfferForUser,
    IngestBatch,
    IngestPostings,
    LeaseScrapeTargets,
    MarkFeedSeen,
    ReceiveBatch,
    ReconcileIndex,
    RefreshScrapeTargets,
    ResetKarriaMemory,
    SearchOffers,
    SetAlertsEnabled,
    SyncIndex,
    UpdateOfferState,
)
from karria.modules.offers.infrastructure import (
    CachedGeocoder,
    LlmDialogue,
    ObjectStorageLandingStore,
    ProcrastinateJobQueue,
    QdrantCriteriaIndex,
    QdrantOfferIndex,
    TeiEmbedder,
    TortoiseIndexOutbox,
    TortoiseIngestionRunRepository,
    TortoiseKarriaMemoryRepository,
    TortoiseOfferRepository,
    TortoiseScrapeTargetRepository,
)
from karria.shared.clock import SystemClock
from karria.shared.config import (
    AppSettings,
    CollectorAccessSettings,
    Environment,
    Settings,
    StorageSettings,
)
from karria.shared.errors import AuthenticationError
from karria.shared.llm import DisabledLanguageModel
from karria.shared.storage.memory import InMemoryObjectStorage

pytestmark = pytest.mark.unit


@pytest.fixture
def settings() -> Settings:
    return Settings(app=AppSettings(environment=Environment.TEST))


@pytest.fixture
def queue_app() -> App:
    return App(connector=testing.InMemoryConnector())


class TestPorts:
    def test_chaque_port_a_son_adaptateur(self, settings: Settings, queue_app: App) -> None:
        assert isinstance(deps.get_clock(), SystemClock)
        assert isinstance(deps.get_offer_repository(), TortoiseOfferRepository)
        assert isinstance(deps.get_outbox(), TortoiseIndexOutbox)
        assert isinstance(deps.get_target_repository(), TortoiseScrapeTargetRepository)
        assert isinstance(deps.get_run_repository(), TortoiseIngestionRunRepository)
        for provider in (
            deps.get_criteria_repository,
            deps.get_conversation_repository,
            deps.get_feed_repository,
            deps.get_state_repository,
        ):
            assert provider() is not None
        assert isinstance(deps.get_offer_index(settings), QdrantOfferIndex)
        assert isinstance(deps.get_criteria_index(settings), QdrantCriteriaIndex)
        assert isinstance(deps.get_embedder(settings), TeiEmbedder)
        assert isinstance(
            deps.get_landing_store(InMemoryObjectStorage(StorageSettings())),
            ObjectStorageLandingStore,
        )
        assert isinstance(deps.get_geocoder(settings), CachedGeocoder)
        assert isinstance(deps.get_job_queue(queue_app), ProcrastinateJobQueue)

    def test_les_collections_portent_l_environnement(self, settings: Settings) -> None:
        index = deps.get_offer_index(settings)
        assert index._name == "karria-test-offers"
        assert deps.get_criteria_index(settings)._name == "karria-test-criteria"

    def test_le_dialogue_recoit_le_port_llm(self) -> None:
        assert isinstance(deps.get_search_dialogue(DisabledLanguageModel()), LlmDialogue)

    async def test_le_notificateur_par_defaut_n_envoie_rien(self) -> None:
        notifier = deps.get_user_notifier()

        assert await notifier.notify(uuid4(), title="t", body="b", data={}) == 0


class TestCasDUsage:
    def test_les_fournisseurs_assemblent_les_cas_d_usage(self) -> None:
        any_port: Any = object()
        search = deps.get_search_offers(any_port, any_port, any_port)
        apply = deps.get_apply_criteria(
            any_port,
            any_port,
            search,
            any_port,
            any_port,
            any_port,
            any_port,
            any_port,
            any_port,
            any_port,
            any_port,
        )

        assert isinstance(search, SearchOffers)
        assert isinstance(apply, ApplyCriteria)
        assert isinstance(
            deps.get_converse_search(
                any_port, any_port, any_port, apply, any_port, any_port, any_port, search
            ),
            ConverseSearch,
        )
        assert isinstance(deps.get_get_memory(any_port), GetKarriaMemory)
        assert isinstance(deps.get_reset_memory(any_port), ResetKarriaMemory)
        assert isinstance(deps.get_memory_repository(), TortoiseKarriaMemoryRepository)
        assert isinstance(deps.get_get_criteria(any_port), GetCriteria)
        assert isinstance(
            deps.get_clear_criteria(any_port, any_port, any_port, any_port), ClearCriteria
        )
        assert isinstance(
            deps.get_set_alerts_enabled(any_port, any_port, any_port), SetAlertsEnabled
        )
        assert isinstance(deps.get_get_feed(any_port, any_port, any_port, any_port), GetFeed)
        assert isinstance(deps.get_get_offer(any_port, any_port, any_port), GetOfferForUser)
        assert isinstance(
            deps.get_update_state(any_port, any_port, any_port, any_port, any_port, any_port),
            UpdateOfferState,
        )
        assert isinstance(
            deps.get_mark_seen(any_port, any_port, any_port, any_port, any_port, any_port),
            MarkFeedSeen,
        )
        assert isinstance(deps.get_get_feed_filters(any_port, any_port, any_port), GetFeedFilters)
        assert isinstance(deps.get_lease_targets(any_port, any_port), LeaseScrapeTargets)
        assert isinstance(
            deps.get_receive_batch(any_port, any_port, any_port, any_port), ReceiveBatch
        )


class TestFabriquesHorsRequete:
    def test_le_worker_et_les_commandes_s_assemblent(
        self, settings: Settings, queue_app: App
    ) -> None:
        notifier = deps.get_user_notifier()

        assert isinstance(deps.build_forget_user(), ForgetUser)
        assert isinstance(deps.build_search_offers(settings), SearchOffers)
        assert isinstance(deps.build_ingest_postings(settings), IngestPostings)
        assert isinstance(deps.build_refresh_targets(), RefreshScrapeTargets)
        assert isinstance(deps.build_dispatch_alerts(settings, notifier), DispatchAlerts)
        assert isinstance(deps.build_expire_offers(), ExpireOffers)
        assert isinstance(deps.build_reconcile_index(settings), ReconcileIndex)
        assert isinstance(
            deps.build_ingest_batch(settings, InMemoryObjectStorage(StorageSettings()), queue_app),
            IngestBatch,
        )

    def test_sans_notificateur_la_synchronisation_n_alerte_pas(self, settings: Settings) -> None:
        silent = deps.build_sync_index(settings, None)
        loud = deps.build_sync_index(settings, deps.get_user_notifier())

        assert isinstance(silent, SyncIndex)
        assert silent._alerts is None
        assert isinstance(loud._alerts, DispatchAlerts)

    async def test_les_collections_se_creent_a_la_demande(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created: list[str] = []

        class Index:
            def __init__(self, name: str) -> None:
                self._name = name

            async def ensure_ready(self) -> None:
                created.append(self._name)

        monkeypatch.setattr(deps, "get_offer_index", lambda _: Index("offers"))
        monkeypatch.setattr(deps, "get_criteria_index", lambda _: Index("criteria"))

        await deps.ensure_indexes(settings)

        assert created == ["offers", "criteria"]


class TestJetonDuCollecteur:
    def _settings(self, token: str) -> Settings:
        return Settings(
            app=AppSettings(environment=Environment.TEST),
            collector=CollectorAccessSettings(token=token),
        )

    def test_le_bon_jeton_ouvre_les_routes_internes(self) -> None:
        settings = self._settings("s3cret")

        deps.require_collector(settings, "Bearer s3cret")

        assert settings.collector.enabled is True

    @pytest.mark.parametrize("header", [None, "Bearer faux", "s3cre"])
    def test_un_jeton_absent_ou_faux_est_refuse(self, header: str | None) -> None:
        with pytest.raises(AuthenticationError) as error:
            deps.require_collector(self._settings("s3cret"), header)

        assert error.value.details["reason"] == "collector_token_invalid"

    def test_sans_jeton_configure_personne_n_entre(self) -> None:
        settings = self._settings("")

        with pytest.raises(AuthenticationError):
            deps.require_collector(settings, "Bearer ")

        assert settings.collector.enabled is False
