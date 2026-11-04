"""Le travail de file de `cv` : inscrit sous verrou, il matérialise avec le vocabulaire reçu."""

from __future__ import annotations

from typing import Any

import pytest
from procrastinate import App, testing

from doubles import InMemorySuggestionIndex, StaticOfferVocabulary
from karria.modules.cv.api import dependencies, tasks
from karria.modules.cv.api.tasks import TASK_REFRESH_SUGGESTIONS, run_refresh
from karria.modules.cv.domain import SuggestionKind

pytestmark = pytest.mark.unit


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> InMemorySuggestionIndex:
    memory = InMemorySuggestionIndex()
    monkeypatch.setattr(dependencies, "get_suggestion_index", lambda: memory)
    return memory


def test_le_travail_est_inscrit_sous_verrou() -> None:
    app = App(connector=testing.InMemoryConnector())
    tasks.register_tasks(app, vocabulary=lambda: StaticOfferVocabulary({}))

    task = app.tasks[TASK_REFRESH_SUGGESTIONS]
    assert task.lock == TASK_REFRESH_SUGGESTIONS
    assert task.queueing_lock == TASK_REFRESH_SUGGESTIONS
    assert (TASK_REFRESH_SUGGESTIONS, TASK_REFRESH_SUGGESTIONS) in (
        app.periodic_registry.periodic_tasks
    )


async def test_une_materialisation_ecrit_le_vocabulaire_recu(
    index: InMemorySuggestionIndex,
) -> None:
    vocabulary = StaticOfferVocabulary({SuggestionKind.CITY: [("Lyon", 3)]})

    report = await run_refresh(lambda: vocabulary)

    assert report.written[SuggestionKind.CITY] == 1
    assert [e.label for e in index.entries[SuggestionKind.CITY]] == ["Lyon"]


async def test_le_travail_de_file_appelle_la_materialisation(
    index: InMemorySuggestionIndex,
) -> None:
    connector = testing.InMemoryConnector()
    app = App(connector=connector)
    tasks.register_tasks(
        app, vocabulary=lambda: StaticOfferVocabulary({SuggestionKind.TITLE: [("Vendeur", 5)]})
    )
    async with app.open_async():
        await app.configure_task(TASK_REFRESH_SUGGESTIONS).defer_async()
        jobs: dict[int, dict[str, Any]] = connector.jobs
        assert [job["task_name"] for job in jobs.values()] == [TASK_REFRESH_SUGGESTIONS]
        await app.run_worker_async(wait=False)

    assert [e.label for e in index.entries[SuggestionKind.TITLE]] == ["Vendeur"]
