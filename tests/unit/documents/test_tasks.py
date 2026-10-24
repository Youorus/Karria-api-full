"""Les travaux de file de `documents`, ses fabriques hors requête et sa porte d'entrée."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from procrastinate import App, testing

from karria.modules import documents
from karria.modules.documents.api import dependencies as deps
from karria.modules.documents.api import tasks
from karria.modules.documents.application import (
    ExplainAts,
    ForgetUser,
    IngestTemplateVersion,
    SubmitTemplate,
)
from karria.modules.documents.infrastructure import (
    KarriaDocumentEngine,
    LlmAtsExplainer,
    ProcrastinateTemplateJobs,
    TortoiseGeneratedCvRepository,
    TortoiseTemplateRepository,
    TortoiseUserTemplateRepository,
)
from karria.modules.documents.infrastructure.queue import TASK_EXPLAIN_ATS, TASK_INGEST_TEMPLATE
from karria.shared.clock import SystemClock
from karria.shared.config import AppSettings, Environment, Settings, StorageSettings
from karria.shared.storage import InMemoryObjectStorage

pytestmark = pytest.mark.unit

SETTINGS = Settings(app=AppSettings(environment=Environment.TEST))


@dataclass
class Recorder:
    """Un cas d'usage qui note ses appels."""

    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def execute(self, *args: Any) -> None:
        self.calls.append(args)


@pytest.fixture
async def app() -> Any:
    application = App(connector=testing.InMemoryConnector())
    tasks.register_tasks(application)
    async with application.open_async():
        yield application


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: SETTINGS)


def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage(StorageSettings(), "test")


class TestTravaux:
    def test_les_deux_travaux_sont_inscrits_avec_une_reprise(self, app: App) -> None:
        assert {TASK_INGEST_TEMPLATE, TASK_EXPLAIN_ATS} <= set(app.tasks)
        assert app.tasks[TASK_INGEST_TEMPLATE].retry_strategy is not None

    async def test_l_ingestion_ouvre_un_stockage_et_analyse_la_version(
        self, app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ingest = Recorder()
        opened = storage()
        seen: list[Any] = []

        async def open_storage(settings: Settings, resources: Any) -> Any:
            seen.append(settings)
            return opened

        def build(settings: Settings, given: Any) -> Recorder:
            assert given is opened
            return ingest

        monkeypatch.setattr(tasks, "build_object_storage", open_storage)
        monkeypatch.setattr(tasks, "build_ingest_template", build)
        version_id = uuid4()

        await app.tasks[TASK_INGEST_TEMPLATE].func(version_id=str(version_id))

        assert ingest.calls == [(version_id,)]
        assert seen == [SETTINGS]

    async def test_l_explication_redige_le_score_de_l_export(
        self, app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        explain = Recorder()
        monkeypatch.setattr(tasks, "build_explain_ats", lambda settings: explain)
        generated_id = uuid4()

        await app.tasks[TASK_EXPLAIN_ATS].func(generated_id=str(generated_id))

        assert explain.calls == [(generated_id,)]


class TestFabriques:
    def test_les_fournisseurs_rendent_les_adaptateurs_reels(self) -> None:
        assert isinstance(deps.get_clock(), SystemClock)
        assert isinstance(deps.get_template_repository(), TortoiseTemplateRepository)
        assert isinstance(deps.get_user_template_repository(), TortoiseUserTemplateRepository)
        assert isinstance(deps.get_generated_cv_repository(), TortoiseGeneratedCvRepository)
        assert isinstance(deps.get_engine(SETTINGS, storage()), KarriaDocumentEngine)
        assert isinstance(
            deps.get_jobs(App(connector=testing.InMemoryConnector())), (ProcrastinateTemplateJobs)
        )
        assert isinstance(deps.get_explainer(object()), LlmAtsExplainer)  # type: ignore[arg-type]

    async def test_sans_branchement_personne_n_a_de_cv_a_rendre(self) -> None:
        assert await deps.get_cv_source().snapshot(uuid4()) is None

    def test_les_fabriques_hors_requete_assemblent_leurs_cas_d_usage(self) -> None:
        jobs = ProcrastinateTemplateJobs(App(connector=testing.InMemoryConnector()))

        assert isinstance(deps.build_ingest_template(SETTINGS, storage()), IngestTemplateVersion)
        assert isinstance(deps.build_submit_template(storage(), jobs), SubmitTemplate)
        assert isinstance(deps.build_explain_ats(SETTINGS), ExplainAts)
        assert isinstance(deps.build_forget_user(storage()), ForgetUser)


def test_la_porte_d_entree_du_module_expose_son_routeur_et_ses_tables() -> None:
    assert documents.MODULE.name == "documents"
    assert documents.MODULE.router is not None
    assert documents.MODULE.models_module == "karria.modules.documents.infrastructure.models"
    assert documents.register_tasks is tasks.register_tasks
    assert documents.get_cv_source is deps.get_cv_source
    assert set(documents.__all__) >= {"MODULE", "CvSource", "ForgetUser", "register_tasks"}


class _Deferrer:
    def __init__(
        self, sink: list[tuple[str, str, dict[str, str]]], task: str, lock: str, fail: bool
    ) -> None:
        self.sink, self.task, self.lock, self.fail = sink, task, lock, fail

    async def defer_async(self, **kwargs: str) -> None:
        from karria.shared.queue import AlreadyEnqueued  # noqa: PLC0415

        if self.fail:
            raise AlreadyEnqueued("déjà")
        self.sink.append((self.task, self.lock, kwargs))


class _QueueApp:
    def __init__(self, *, fail: bool = False) -> None:
        self.deferred: list[tuple[str, str, dict[str, str]]] = []
        self.fail = fail

    def configure_task(self, task: str, *, queueing_lock: str) -> _Deferrer:
        return _Deferrer(self.deferred, task, queueing_lock, self.fail)


async def test_la_file_differe_ingestion_et_explication_avec_un_verrou_par_objet() -> None:
    from uuid import uuid4  # noqa: PLC0415

    from karria.modules.documents.infrastructure.queue import ProcrastinateTemplateJobs  # noqa: PLC0415

    app = _QueueApp()
    jobs = ProcrastinateTemplateJobs(app)  # type: ignore[arg-type]
    version, generated = uuid4(), uuid4()
    await jobs.defer_ingestion(version)
    await jobs.defer_explanation(generated)
    assert app.deferred == [
        (
            "documents.ingest_template",
            f"documents.ingest_template:{version}",
            {"version_id": str(version)},
        ),
        (
            "documents.explain_ats",
            f"documents.explain_ats:{generated}",
            {"generated_id": str(generated)},
        ),
    ]


async def test_un_travail_deja_en_file_n_est_pas_double() -> None:
    from uuid import uuid4  # noqa: PLC0415

    from karria.modules.documents.infrastructure.queue import ProcrastinateTemplateJobs  # noqa: PLC0415

    app = _QueueApp(fail=True)
    await ProcrastinateTemplateJobs(app).defer_ingestion(uuid4())  # type: ignore[arg-type]
    assert app.deferred == []
