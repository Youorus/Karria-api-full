"""La file durable du socle : l'URL libpq, l'application non ouverte, la dépendance HTTP."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from procrastinate import App, PsycopgConnector

from karria.bootstrap import lifespan as module
from karria.shared.config import (
    AppSettings,
    DatabaseSettings,
    Environment,
    Settings,
    StorageSettings,
)
from karria.shared.http.dependencies import get_queue_app
from karria.shared.queue import build_queue_app, conninfo_from
from karria.shared.storage import InMemoryObjectStorage

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        ("asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
    ],
)
def test_l_url_de_tortoise_devient_une_url_libpq(url: str, expected: str) -> None:
    assert conninfo_from(DatabaseSettings(url=url)) == expected


def test_l_application_de_file_parle_psycopg_sans_se_connecter() -> None:
    app = build_queue_app(DatabaseSettings(url="postgres://u:p@h/db"))

    assert isinstance(app, App)
    assert isinstance(app.connector, PsycopgConnector)


def test_la_route_lit_la_file_ouverte_par_le_lifespan() -> None:
    queue = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(queue_app=queue)))

    assert get_queue_app(request) is queue  # type: ignore[arg-type]


async def test_le_lifespan_ouvre_la_file_pour_la_duree_du_processus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Queue:
        @asynccontextmanager
        async def open_async(self) -> Any:
            events.append("queue_open")
            yield self
            events.append("queue_close")

    queue = Queue()

    async def connect(config: Any) -> None:
        events.append("connect")

    async def disconnect() -> None:
        events.append("disconnect")

    async def storage(settings: Any, resources: Any) -> InMemoryObjectStorage:
        return InMemoryObjectStorage(StorageSettings())

    monkeypatch.setattr(
        module, "get_settings", lambda: Settings(app=AppSettings(environment=Environment.TEST))
    )
    monkeypatch.setattr(module, "connect", connect)
    monkeypatch.setattr(module, "disconnect", disconnect)
    monkeypatch.setattr(module, "build_object_storage", storage)
    monkeypatch.setattr(module, "build_queue_app", lambda _: queue)
    app = FastAPI()

    async with module.lifespan(app):
        assert app.state.queue_app is queue
        events.append("serving")

    assert events == ["connect", "queue_open", "serving", "disconnect", "queue_close"]
