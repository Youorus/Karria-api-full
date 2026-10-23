"""Le worker : il inscrit les travaux des modules, ouvre la base et la file, puis exécute."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from karria.bootstrap import worker
from karria.bootstrap.worker import WORKER_CONCURRENCY, build_worker_app, run_worker
from karria.modules.offers.infrastructure.queue import TASK_SYNC_INDEX
from karria.shared.queue import Status, UniqueViolation

pytestmark = pytest.mark.unit


def test_le_worker_connait_les_travaux_d_offers() -> None:
    app = build_worker_app()

    assert TASK_SYNC_INDEX in app.tasks


class FakeApp:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.options: dict[str, Any] = {}

    @asynccontextmanager
    async def open_async(self) -> AsyncIterator[FakeApp]:
        self.events.append("open")
        yield self
        self.events.append("close")

    async def run_worker_async(self, **options: Any) -> None:
        self.options = options
        self.events.append("run")


async def test_le_worker_ouvre_execute_et_referme(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FakeApp()
    events: list[str] = []

    async def connect(config: Any) -> None:
        events.append("connect")

    async def disconnect() -> None:
        events.append("disconnect")

    monkeypatch.setattr(worker, "connect", connect)
    monkeypatch.setattr(worker, "disconnect", disconnect)
    monkeypatch.setattr(worker, "configure_logging", lambda _: None)
    monkeypatch.setattr(worker, "build_worker_app", lambda: app)

    await run_worker()

    assert events == ["connect", "disconnect"]
    assert app.events == ["open", "run", "close"]
    assert app.options["concurrency"] == WORKER_CONCURRENCY


async def test_une_panne_du_worker_referme_la_base(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def connect(config: Any) -> None:
        events.append("connect")

    async def disconnect() -> None:
        events.append("disconnect")

    def broken() -> Any:
        raise RuntimeError("file injoignable")

    monkeypatch.setattr(worker, "connect", connect)
    monkeypatch.setattr(worker, "disconnect", disconnect)
    monkeypatch.setattr(worker, "configure_logging", lambda _: None)
    monkeypatch.setattr(worker, "build_worker_app", broken)

    with pytest.raises(RuntimeError):
        await run_worker()

    assert events == ["connect", "disconnect"]


class FakeJobManager:
    """Le gestionnaire de travaux de procrastinate, réduit à ce que la maintenance appelle."""

    def __init__(self, stalled: list[Any]) -> None:
        self.stalled = stalled
        self.retried: list[Any] = []
        self.pruned: list[int] = []
        self.asked: list[int] = []
        self.finished: list[tuple[int, Any, bool]] = []

    async def get_stalled_jobs(self, *, seconds_since_heartbeat: int) -> list[Any]:
        self.asked.append(seconds_since_heartbeat)
        return self.stalled

    async def retry_job(self, job: Any) -> None:
        if getattr(job, "duplicate", False):
            raise UniqueViolation(
                constraint_name="procrastinate_jobs_queueing_lock_idx", queueing_lock="x"
            )
        self.retried.append(job)

    async def finish_job_by_id_async(self, *, job_id: int, status: Any, delete_job: bool) -> None:
        self.finished.append((job_id, status, delete_job))

    async def prune_stalled_workers(self, *, seconds_since_heartbeat: int) -> None:
        self.pruned.append(seconds_since_heartbeat)


class FakeJob:
    def __init__(self, job_id: int, *, duplicate: bool = False) -> None:
        self.id = job_id
        self.task_name = "offers.sync_index"
        self.duplicate = duplicate


async def test_un_orphelin_dont_le_double_attend_deja_est_clos_en_echec() -> None:
    manager = FakeJobManager([FakeJob(7, duplicate=True)])
    app: Any = type("A", (), {"job_manager": manager})()

    assert await worker.retry_stalled_jobs(app) == 1
    assert manager.retried == []
    assert manager.finished == [(7, Status.FAILED, False)]


async def test_les_travaux_orphelins_sont_relances_et_les_workers_morts_oublies() -> None:
    jobs = [FakeJob(1), FakeJob(2)]
    manager = FakeJobManager(jobs)
    app: Any = type("A", (), {"job_manager": manager})()

    assert await worker.retry_stalled_jobs(app) == 2
    assert manager.retried == jobs
    assert manager.asked == manager.pruned == [worker.STALLED_AFTER_SECONDS]


async def test_la_maintenance_est_inscrite_et_appelle_la_relance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from procrastinate import App, testing  # noqa: PLC0415

    app = App(connector=testing.InMemoryConnector())
    worker.register_queue_maintenance(app)
    calls: list[Any] = []

    async def retry(given: Any) -> int:
        calls.append(given)
        return 0

    monkeypatch.setattr(worker, "retry_stalled_jobs", retry)

    await app.tasks[worker.TASK_RETRY_STALLED].func(timestamp=0)

    assert calls == [app]
    assert worker.TASK_RETRY_STALLED in build_worker_app().tasks
