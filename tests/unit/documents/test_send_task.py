"""Le travail d'envoi, ses fabriques et le branchement de l'adresse du compte (ADR-0031)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from procrastinate import App, testing

from karria.bootstrap.app import create_app
from karria.bootstrap.wiring import documents_account_directory_from_identity
from karria.modules.documents import build_render_document, get_account_directory
from karria.modules.documents.api import dependencies as deps
from karria.modules.documents.api import tasks
from karria.modules.documents.application import DeliverDocuments, RenderDocument
from karria.modules.documents.infrastructure import (
    MailDocumentMailer,
    ProcrastinateTemplateJobs,
    StorageLogoFetcher,
    TortoiseSendRepository,
)
from karria.modules.documents.infrastructure.queue import TASK_SEND_DOCUMENTS
from karria.shared.config import AppSettings, Environment, Settings, StorageSettings
from karria.shared.storage import InMemoryObjectStorage

pytestmark = pytest.mark.unit

SETTINGS = Settings(app=AppSettings(environment=Environment.TEST))


@dataclass
class Recorder:
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


class TestTravail:
    def test_l_envoi_est_inscrit_avec_une_reprise(self, app: App) -> None:
        assert TASK_SEND_DOCUMENTS in app.tasks
        assert app.tasks[TASK_SEND_DOCUMENTS].retry_strategy is not None

    async def test_l_envoi_ouvre_un_stockage_et_remet(
        self, app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deliver = Recorder()
        opened = InMemoryObjectStorage(StorageSettings(), "test")

        async def open_storage(settings: Settings, resources: Any) -> Any:
            return opened

        def build(settings: Settings, given: Any) -> Recorder:
            assert given is opened
            return deliver

        monkeypatch.setattr(tasks, "build_object_storage", open_storage)
        monkeypatch.setattr(tasks, "build_deliver_documents", build)
        send_id = uuid4()

        await app.tasks[TASK_SEND_DOCUMENTS].func(send_id=str(send_id))

        assert deliver.calls == [(send_id,)]

    async def test_la_file_differe_un_envoi_sous_verrou(self) -> None:
        queue = App(connector=testing.InMemoryConnector())
        tasks.register_tasks(queue)
        send_id = uuid4()
        async with queue.open_async():
            await ProcrastinateTemplateJobs(queue).defer_send(send_id)
            await ProcrastinateTemplateJobs(queue).defer_send(send_id)
            jobs = list(queue.connector.jobs.values())  # type: ignore[attr-defined]
        assert len(jobs) == 1
        assert jobs[0]["task_name"] == TASK_SEND_DOCUMENTS
        assert jobs[0]["queueing_lock"] == f"{TASK_SEND_DOCUMENTS}:{send_id}"


class TestFabriques:
    def test_les_fournisseurs_rendent_les_adaptateurs_reels(self) -> None:
        assert isinstance(deps.get_send_repository(), TortoiseSendRepository)
        assert isinstance(deps.get_logo_fetcher(SETTINGS), StorageLogoFetcher)
        assert isinstance(deps.get_document_mailer(SETTINGS), MailDocumentMailer)

    async def test_sans_branchement_aucune_adresse_de_compte(self) -> None:
        assert await deps.get_account_directory().email_of(uuid4()) is None

    def test_les_fabriques_hors_requete_assemblent_leurs_cas_d_usage(self) -> None:
        storage = InMemoryObjectStorage(StorageSettings(), "test")
        assert isinstance(deps.build_deliver_documents(SETTINGS, storage), DeliverDocuments)
        assert isinstance(build_render_document(SETTINGS, storage), RenderDocument)


def test_l_adresse_du_compte_est_branchee_sur_identity() -> None:
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))
    assert (
        app.dependency_overrides[get_account_directory] is documents_account_directory_from_identity
    )
