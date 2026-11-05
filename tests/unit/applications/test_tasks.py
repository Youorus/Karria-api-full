"""Le travail de file d'`applications` : inscrit, et il appelle l'adaptation."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from procrastinate import App, testing

from doubles import PermissiveCvValidator
from karria.modules.applications.api import tasks
from karria.modules.applications.infrastructure import TASK_ADAPT_CV, ProcrastinateDossierJobs

pytestmark = pytest.mark.unit


def test_le_travail_est_inscrit_avec_une_reprise() -> None:
    app = App(connector=testing.InMemoryConnector())
    tasks.register_tasks(app, validator=PermissiveCvValidator)

    task = app.tasks[TASK_ADAPT_CV]
    assert task.retry_strategy is not None


async def test_differer_pose_un_verrou_par_dossier_et_le_travail_appelle_le_cas_d_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = testing.InMemoryConnector()
    app = App(connector=connector)
    tasks.register_tasks(app, validator=PermissiveCvValidator)
    seen: list[str] = []

    class Adapt:
        async def execute(self, dossier_id: object) -> None:
            seen.append(str(dossier_id))

    monkeypatch.setattr(tasks, "build_adapt_cv", lambda settings, validator: Adapt())
    dossier_id = uuid4()
    async with app.open_async():
        jobs = ProcrastinateDossierJobs(app)
        await jobs.defer_adaptation(dossier_id)
        await jobs.defer_adaptation(dossier_id)  # même verrou : absorbé
        queued: dict[int, dict[str, Any]] = connector.jobs
        assert [j["task_name"] for j in queued.values()] == [TASK_ADAPT_CV]
        assert next(iter(queued.values()))["queueing_lock"] == f"{TASK_ADAPT_CV}:{dossier_id}"
        await app.run_worker_async(wait=False)

    assert seen == [str(dossier_id)]
