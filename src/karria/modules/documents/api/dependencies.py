"""Câblage des dépendances du module `documents`.

Chaque port a sa fonction fournisseur : un test la remplace par
`app.dependency_overrides` (dépôts en mémoire, moteur factice). `get_cv_source`
**refuse** par défaut : `bootstrap/wiring.py` le branche sur le module `cv`
(règle 4) ; oublié, tout rendu répond `cv_missing` plutôt que de lire ailleurs.
"""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header

from karria.modules.documents.application import (
    AcquireFreeTemplate,
    AtsExplainer,
    CvSnapshot,
    CvSource,
    DeleteGeneratedCv,
    DocumentEngine,
    ExplainAts,
    ExportCv,
    GeneratedCvRepository,
    GetGeneratedCv,
    GetSelection,
    GetTemplate,
    IngestTemplateVersion,
    ListGeneratedCvs,
    ListOwnedTemplates,
    ListTemplates,
    PreviewTemplate,
    RecordTemplateView,
    SelectTemplate,
    SubmitTemplate,
    TemplateJobs,
    TemplateRepository,
    UserTemplateRepository,
)
from karria.modules.documents.application.use_cases import CvRenderer
from karria.modules.documents.application.use_cases import ForgetUser as ForgetDocuments
from karria.modules.documents.infrastructure import (
    FontLibrary,
    GotenbergClient,
    KarriaDocumentEngine,
    LlmAtsExplainer,
    ProcrastinateTemplateJobs,
    TortoiseGeneratedCvRepository,
    TortoiseTemplateRepository,
    TortoiseUserTemplateRepository,
    VisionLineLabeler,
)
from karria.shared.clock import Clock, SystemClock
from karria.shared.config import Settings, get_settings
from karria.shared.database import TortoiseUnitOfWork
from karria.shared.errors.exceptions import AuthenticationError
from karria.shared.http.dependencies import (
    LanguageModelDep,
    ObjectStorageDep,
    UnitOfWorkDep,
    get_queue_app,
)
from karria.shared.llm import build_language_model
from karria.shared.queue import App as QueueApp
from karria.shared.storage import ObjectStorage

SettingsDep = Annotated[Settings, Depends(get_settings)]


class NoCvSource:
    """Le refus par défaut : sans branchement, personne n'a de CV à rendre."""

    async def snapshot(self, user_id: UUID) -> CvSnapshot | None:
        del user_id
        return None


# --- Ports : ce que les tests remplacent ----------------------------------


def get_clock() -> Clock:
    return SystemClock()


def get_template_repository() -> TemplateRepository:
    return TortoiseTemplateRepository()


def get_user_template_repository() -> UserTemplateRepository:
    return TortoiseUserTemplateRepository()


def get_generated_cv_repository() -> GeneratedCvRepository:
    return TortoiseGeneratedCvRepository()


def get_cv_source() -> CvSource:
    return NoCvSource()


def build_engine(settings: Settings, storage: ObjectStorage) -> DocumentEngine:
    """Le moteur de documents (Gotenberg, polices, modèle de vision pour l'analyse)."""
    gotenberg = GotenbergClient(
        settings.documents.gotenberg_url,
        timeout_seconds=settings.documents.gotenberg_timeout_seconds,
    )
    labeler = VisionLineLabeler(build_language_model(settings))
    return KarriaDocumentEngine(FontLibrary(storage), labeler, gotenberg)


def get_engine(settings: SettingsDep, storage: ObjectStorageDep) -> DocumentEngine:
    return build_engine(settings, storage)


def get_jobs(queue: Annotated[QueueApp, Depends(get_queue_app)]) -> TemplateJobs:
    return ProcrastinateTemplateJobs(queue)


ClockDep = Annotated[Clock, Depends(get_clock)]
TemplatesDep = Annotated[TemplateRepository, Depends(get_template_repository)]
OwnedDep = Annotated[UserTemplateRepository, Depends(get_user_template_repository)]
GeneratedDep = Annotated[GeneratedCvRepository, Depends(get_generated_cv_repository)]
CvSourceDep = Annotated[CvSource, Depends(get_cv_source)]
EngineDep = Annotated[DocumentEngine, Depends(get_engine)]
JobsDep = Annotated[TemplateJobs, Depends(get_jobs)]


def require_admin(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    """Les routes `/internal/templates` n'acceptent que le jeton d'administration (temps constant)."""
    expected = settings.documents.admin_token.get_secret_value()
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise AuthenticationError(
            "Jeton d'administration invalide.", details={"reason": "admin_token_invalid"}
        )


# --- Cas d'usage ---------------------------------------------------------


def get_list_templates(templates: TemplatesDep) -> ListTemplates:
    return ListTemplates(templates)


def get_get_template(templates: TemplatesDep) -> GetTemplate:
    return GetTemplate(templates)


def get_record_view(templates: TemplatesDep) -> RecordTemplateView:
    return RecordTemplateView(templates)


def get_list_owned(owned: OwnedDep) -> ListOwnedTemplates:
    return ListOwnedTemplates(owned)


def get_acquire(templates: TemplatesDep, owned: OwnedDep, clock: ClockDep) -> AcquireFreeTemplate:
    return AcquireFreeTemplate(templates, owned, clock)


def get_get_selection(owned: OwnedDep) -> GetSelection:
    return GetSelection(owned)


def get_select(templates: TemplatesDep, owned: OwnedDep, clock: ClockDep) -> SelectTemplate:
    return SelectTemplate(templates, owned, clock)


def get_renderer(
    templates: TemplatesDep,
    owned: OwnedDep,
    cvs: CvSourceDep,
    engine: EngineDep,
    storage: ObjectStorageDep,
) -> CvRenderer:
    return CvRenderer(templates, owned, cvs, engine, storage)


RendererDep = Annotated[CvRenderer, Depends(get_renderer)]


def get_preview(
    renderer: RendererDep, engine: EngineDep, storage: ObjectStorageDep
) -> PreviewTemplate:
    return PreviewTemplate(renderer, engine, storage)


def get_export(
    *,
    renderer: RendererDep,
    templates: TemplatesDep,
    owned: OwnedDep,
    generated: GeneratedDep,
    engine: EngineDep,
    storage: ObjectStorageDep,
    jobs: JobsDep,
    clock: ClockDep,
) -> ExportCv:
    return ExportCv(
        renderer=renderer,
        templates=templates,
        owned=owned,
        generated=generated,
        engine=engine,
        storage=storage,
        jobs=jobs,
        clock=clock,
    )


def get_list_generated(generated: GeneratedDep) -> ListGeneratedCvs:
    return ListGeneratedCvs(generated)


def get_get_generated(generated: GeneratedDep) -> GetGeneratedCv:
    return GetGeneratedCv(generated)


def get_delete_generated(generated: GeneratedDep, storage: ObjectStorageDep) -> DeleteGeneratedCv:
    return DeleteGeneratedCv(generated, storage)


def get_submit(
    templates: TemplatesDep,
    storage: ObjectStorageDep,
    jobs: JobsDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> SubmitTemplate:
    return SubmitTemplate(templates, storage, jobs, clock, uow)


def get_explainer(llm: LanguageModelDep) -> AtsExplainer:
    return LlmAtsExplainer(llm)


# --- Hors requête : le worker et les commandes ------------------------------


def build_ingest_template(settings: Settings, storage: ObjectStorage) -> IngestTemplateVersion:
    """L'ingestion d'une version, pour le travail `documents.ingest_template` et la commande."""
    return IngestTemplateVersion(
        get_template_repository(), build_engine(settings, storage), storage, get_clock()
    )


def build_submit_template(storage: ObjectStorage, jobs: TemplateJobs) -> SubmitTemplate:
    """Le dépôt d'un modèle, pour la commande `scripts/templates.py`."""
    return SubmitTemplate(
        get_template_repository(), storage, jobs, get_clock(), TortoiseUnitOfWork()
    )


def build_explain_ats(settings: Settings) -> ExplainAts:
    """L'explication d'un score, pour le travail `documents.explain_ats`."""
    return ExplainAts(
        get_generated_cv_repository(), LlmAtsExplainer(build_language_model(settings))
    )


def build_forget_user(storage: ObjectStorage) -> ForgetDocuments:
    """L'oubli d'un compte supprimé, branché sur `AccountDeleted` par `bootstrap/wiring.py`."""
    return ForgetDocuments(get_generated_cv_repository(), get_user_template_repository(), storage)
