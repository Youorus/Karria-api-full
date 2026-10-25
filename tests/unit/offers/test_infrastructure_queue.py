"""La file vue d'`offers` : un différé par verrou, le doublon silencieux."""

from __future__ import annotations

from uuid import uuid4

import pytest
from procrastinate import App, testing

from karria.modules.offers.infrastructure.queue import (
    TASK_FETCH_LOGOS,
    TASK_INGEST_BATCH,
    TASK_SYNC_INDEX,
    TASK_UPDATE_MEMORY,
    ProcrastinateJobQueue,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def connector() -> testing.InMemoryConnector:
    return testing.InMemoryConnector()


@pytest.fixture
async def queue_app(connector: testing.InMemoryConnector) -> App:
    app = App(connector=connector)
    async with app.open_async():
        yield app


async def test_deux_resynchronisations_ne_font_qu_un_travail(
    queue_app: App, connector: testing.InMemoryConnector
) -> None:
    queue = ProcrastinateJobQueue(queue_app)

    await queue.defer_index_sync()
    await queue.defer_index_sync()

    jobs = list(connector.jobs.values())
    assert [(job["task_name"], job["queueing_lock"]) for job in jobs] == [
        (TASK_SYNC_INDEX, TASK_SYNC_INDEX)
    ]


async def test_un_lot_porte_sa_cle_et_sa_cible(
    queue_app: App, connector: testing.InMemoryConnector
) -> None:
    await ProcrastinateJobQueue(queue_app).defer_ingest_batch("collector/a.gz", "serveur|paris")

    job = next(iter(connector.jobs.values()))
    assert job["task_name"] == TASK_INGEST_BATCH
    assert job["args"] == {"key": "collector/a.gz", "target_key": "serveur|paris"}
    assert job["queueing_lock"] == f"{TASK_INGEST_BATCH}:collector/a.gz"


async def test_un_lot_sans_cible_ne_porte_que_sa_cle(
    queue_app: App, connector: testing.InMemoryConnector
) -> None:
    queue = ProcrastinateJobQueue(queue_app)

    await queue.defer_ingest_batch("collector/b.gz", None)
    await queue.defer_ingest_batch("collector/b.gz", None)

    jobs = list(connector.jobs.values())
    assert [job["args"] for job in jobs] == [{"key": "collector/b.gz"}]


async def test_une_mise_a_jour_de_memoire_porte_le_tour_sans_verrou(
    queue_app: App, connector: testing.InMemoryConnector
) -> None:
    user = uuid4()
    queue = ProcrastinateJobQueue(queue_app)

    await queue.defer_memory_update(user, user_message="serveur à Lyon", reply="C'est noté.")
    await queue.defer_memory_update(user, user_message="plutôt en CDI", reply="D'accord.")

    jobs = list(connector.jobs.values())
    assert [job["task_name"] for job in jobs] == [TASK_UPDATE_MEMORY, TASK_UPDATE_MEMORY]
    assert jobs[0]["args"] == {
        "user_id": str(user),
        "user_message": "serveur à Lyon",
        "reply": "C'est noté.",
    }
    assert jobs[0]["queueing_lock"] is None


async def test_un_seul_passage_de_logos_en_attente(
    queue_app: App, connector: testing.InMemoryConnector
) -> None:
    queue = ProcrastinateJobQueue(queue_app)

    await queue.defer_logo_fetch()
    await queue.defer_logo_fetch()

    assert [job["task_name"] for job in connector.jobs.values()] == [TASK_FETCH_LOGOS]
