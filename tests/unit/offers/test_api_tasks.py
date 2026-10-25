"""Les travaux de la file : chacun appelle son cas d'usage, la synchronisation tient son budget."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from procrastinate import App, testing

from karria.modules.offers.api import tasks
from karria.modules.offers.application import LogoReport, SyncReport
from karria.modules.offers.infrastructure.queue import (
    TASK_EXPIRE,
    TASK_FETCH_LOGOS,
    TASK_INGEST_BATCH,
    TASK_RECONCILE,
    TASK_REFRESH_TARGETS,
    TASK_SYNC_INDEX,
    TASK_UPDATE_MEMORY,
)
from karria.shared.config import AppSettings, Environment, Settings

pytestmark = pytest.mark.unit

SETTINGS = Settings(app=AppSettings(environment=Environment.TEST))


@dataclass
class Recorder:
    """Un cas d'usage qui note ses appels et rend des résultats programmés."""

    results: list[Any] = field(default_factory=list)
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        return self.results.pop(0) if self.results else None


@pytest.fixture
def connector() -> testing.InMemoryConnector:
    return testing.InMemoryConnector()


@pytest.fixture
async def app(connector: testing.InMemoryConnector) -> Any:
    application = App(connector=connector)
    tasks.register_tasks(application, notifier=None)
    async with application.open_async():
        yield application


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: SETTINGS)


def queued(connector: testing.InMemoryConnector) -> list[str]:
    return [job["task_name"] for job in connector.jobs.values()]


def test_tous_les_travaux_sont_inscrits(app: App) -> None:
    assert {
        TASK_SYNC_INDEX,
        TASK_INGEST_BATCH,
        TASK_REFRESH_TARGETS,
        TASK_EXPIRE,
        TASK_RECONCILE,
        TASK_UPDATE_MEMORY,
        TASK_FETCH_LOGOS,
    } <= set(app.tasks)


async def test_un_lot_remis_est_ingere_avec_un_stockage_ouvert(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingest = Recorder()
    storage = object()
    opened: list[Any] = []

    async def open_storage(settings: Settings, resources: Any) -> Any:
        opened.append(settings)
        return storage

    def build(settings: Settings, given: Any, queue_app: Any) -> Recorder:
        assert given is storage
        assert queue_app is app
        return ingest

    monkeypatch.setattr(tasks, "build_object_storage", open_storage)
    monkeypatch.setattr(tasks, "build_ingest_batch", build)

    await app.tasks[TASK_INGEST_BATCH].func(key="collector/a.gz", target_key="serveur|paris")

    assert ingest.calls == [("collector/a.gz",)]
    assert opened == [SETTINGS]


async def test_le_pilotage_de_la_collecte(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    refresh = Recorder()
    monkeypatch.setattr(tasks, "build_refresh_targets", lambda: refresh)

    await app.tasks[TASK_REFRESH_TARGETS].func(timestamp=0)

    assert refresh.calls == [()]


async def test_l_expiration_relance_l_index(
    app: App, connector: testing.InMemoryConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    expire = Recorder()
    monkeypatch.setattr(tasks, "build_expire_offers", lambda: expire)

    await app.tasks[TASK_EXPIRE].func(timestamp=0)

    assert expire.calls == [()]
    assert queued(connector) == [TASK_SYNC_INDEX]


async def test_la_reconciliation_relance_l_index(
    app: App, connector: testing.InMemoryConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile = Recorder()
    monkeypatch.setattr(tasks, "build_reconcile_index", lambda _: reconcile)

    await app.tasks[TASK_RECONCILE].func(timestamp=0)

    assert reconcile.calls == [()]
    assert queued(connector) == [TASK_SYNC_INDEX]


async def test_la_synchronisation_vide_la_boite_puis_s_arrete(
    app: App, connector: testing.InMemoryConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = Recorder(results=[SyncReport(claimed=64), SyncReport(claimed=3), SyncReport()])
    seen: list[Any] = []

    def build(settings: Settings, notifier: Any) -> Recorder:
        seen.append(notifier)
        return sync

    monkeypatch.setattr(tasks, "build_sync_index", build)

    await app.tasks[TASK_SYNC_INDEX].func()

    assert len(sync.calls) == 3
    assert seen == [None]
    assert queued(connector) == []


async def test_au_dela_du_budget_la_synchronisation_se_re_differe(
    connector: testing.InMemoryConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = App(connector=connector)
    sync = Recorder(results=[SyncReport(claimed=64)])
    notifiers: list[Any] = []
    monkeypatch.setattr(
        tasks, "build_sync_index", lambda settings, notifier: notifiers.append(notifier) or sync
    )
    monkeypatch.setattr(tasks, "SYNC_BUDGET_SECONDS", -1.0)

    async with application.open_async():
        total = await tasks.run_sync(application, lambda: "notificateur")  # type: ignore[arg-type, return-value]

    assert total == 64
    assert notifiers == ["notificateur"]
    assert queued(connector) == [TASK_SYNC_INDEX]


class LogoRecorder(Recorder):
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(tuple(kwargs.items()))
        return self.results.pop(0) if self.results else LogoReport()


async def test_les_logos_se_cherchent_jusqu_a_epuisement(
    app: App, connector: testing.InMemoryConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = LogoRecorder(results=[LogoReport(claimed=200), LogoReport(claimed=4)])
    storage = object()
    built: list[Any] = []

    async def open_storage(settings: Settings, resources: Any) -> Any:
        return storage

    def build(given: Any, fetcher: Any) -> LogoRecorder:
        built.append((given, type(fetcher).__name__))
        return fetch

    monkeypatch.setattr(tasks, "build_object_storage", open_storage)
    monkeypatch.setattr(tasks, "build_fetch_logos", build)

    await app.tasks[TASK_FETCH_LOGOS].func()

    assert len(fetch.calls) == 3
    assert built == [(storage, "HttpLogoFetcher")]
    assert queued(connector) == []


async def test_au_dela_du_budget_les_logos_se_re_different(
    connector: testing.InMemoryConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = App(connector=connector)
    fetch = LogoRecorder(results=[LogoReport(claimed=200)])

    async def open_storage(settings: Settings, resources: Any) -> Any:
        return object()

    monkeypatch.setattr(tasks, "build_object_storage", open_storage)
    monkeypatch.setattr(tasks, "build_fetch_logos", lambda storage, fetcher: fetch)

    async with application.open_async():
        total = await tasks.run_logos(application, budget=-1.0)

    assert total == 200
    assert queued(connector) == [TASK_FETCH_LOGOS]
    assert fetch.calls == [(("concurrency", 16),)]


async def test_la_commande_de_rattrapage_ne_re_differe_rien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = LogoRecorder(results=[LogoReport(claimed=5)])

    async def open_storage(settings: Settings, resources: Any) -> Any:
        return object()

    monkeypatch.setattr(tasks, "build_object_storage", open_storage)
    monkeypatch.setattr(tasks, "build_fetch_logos", lambda storage, fetcher: fetch)

    assert await tasks.fetch_logos_within(budget=-1.0, concurrency=4) == 5
