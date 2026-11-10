"""Câblage des dépendances du module `documents`.

Chaque port a sa fonction fournisseur : un test la remplace par
`app.dependency_overrides` (dépôts en mémoire, moteur factice). `get_cv_source`
**refuse** par défaut : `bootstrap/wiring.py` le branche sur le module `cv`
(règle 4) ; oublié, tout rendu répond `cv_missing` plutôt que de lire ailleurs.
De même `get_account_directory` (l'adresse du compte pour `to: null`,
ADR-0031) ne connaît personne tant qu'`identity` n'est pas branché.
"""

from __future__ import annotations

import hmac
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from karria.modules.documents.application import (
    AccountDirectory,
    AcquireFreeTemplate,
    AtsExplainer,
    CvSnapshot,
    CvSource,
    DeleteGeneratedCv,
    DeliverDocuments,
    DocumentEngine,
    DocumentMailer,
    ExplainAts,
    ExportCv,
    ExportLetter,
    GeneratedCvRepository,
    GetGeneratedCv,
    GetSelection,
    GetSend,
    GetTemplate,
    IngestTemplateVersion,
    ListGeneratedCvs,
    ListOwnedTemplates,
    ListTemplates,
    LogoFetcher,
    OriginThrottle,
    PreviewTemplate,
    PublishPlainLetterPreview,
    RecordTemplateView,
    RefreshTemplateColors,
    RenderDocument,
    SelectTemplate,
    SendDocuments,
    SendRepository,
    SubmitTemplate,
    TemplateJobs,
    TemplateRepository,
    TemplateShop,
    UserTemplateRepository,
)
from karria.modules.documents.application.use_cases import CvRenderer
from karria.modules.documents.application.use_cases import ForgetUser as ForgetDocuments
from karria.modules.documents.domain import (
    GUEST_RATE_LIMIT,
    GUEST_RATE_WINDOW_SECONDS,
    RENDER_RATE_LIMIT,
    RENDER_RATE_WINDOW_SECONDS,
)
from karria.modules.documents.infrastructure import (
    FontLibrary,
    GotenbergClient,
    KarriaDocumentEngine,
    LetterLineLabeler,
    LlmAtsExplainer,
    MailDocumentMailer,
    ProcrastinateTemplateJobs,
    StorageLogoFetcher,
    TortoiseGeneratedCvRepository,
    TortoiseSendRepository,
    TortoiseTemplateRepository,
    TortoiseUserTemplateRepository,
    VisionLayoutPlanner,
)
from karria.shared.auth import CurrentPrincipalDep, Principal, allow_anonymous
from karria.shared.clock import Clock, SystemClock
from karria.shared.config import Settings, get_settings
from karria.shared.database import TortoiseUnitOfWork
from karria.shared.errors.exceptions import AuthenticationError, RateLimitedError
from karria.shared.http import SlidingWindowLimiter
from karria.shared.http.dependencies import (
    LanguageModelDep,
    ObjectStorageDep,
    UnitOfWorkDep,
    get_queue_app,
)
from karria.shared.http.ratelimit import client_ip_behind
from karria.shared.llm import build_language_model
from karria.shared.logging import get_logger
from karria.shared.mail import Mailer, build_mailer
from karria.shared.queue import App as QueueApp
from karria.shared.storage import ObjectStorage

logger = get_logger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


class NoCvSource:
    """Le refus par défaut : sans branchement, personne n'a de CV à rendre."""

    async def snapshot(self, user_id: UUID) -> CvSnapshot | None:
        del user_id
        return None


class NoAccountDirectory:
    """Le refus par défaut : sans branchement, aucune adresse de compte (`account_email_unknown`)."""

    async def email_of(self, user_id: UUID) -> str | None:
        del user_id
        logger.warning("documents.account_directory_unwired")
        return None


# --- Ports : ce que les tests remplacent ----------------------------------


def get_clock() -> Clock:
    return SystemClock()


#: Le compteur des lectures anonymes de la galerie : un par processus.
_guest_limiter = SlidingWindowLimiter(
    max_hits=GUEST_RATE_LIMIT, window_seconds=GUEST_RATE_WINDOW_SECONDS
)

#: Une personne connectée, ou un lecteur anonyme admis sous quota (ADR-0025).
GuestPrincipalDep = Annotated[
    Principal | None, Depends(allow_anonymous(_guest_limiter, scope="documents"))
]

#: Le compteur des rendus de l'atelier, par compte (ADR-0029 § 5).
_render_limiter = SlidingWindowLimiter(
    max_hits=RENDER_RATE_LIMIT, window_seconds=RENDER_RATE_WINDOW_SECONDS
)


def throttle_render(principal: CurrentPrincipalDep) -> Principal:
    """Un rendu de plus pour ce compte, ou `429 render_rate_limited`."""
    retry_after = _render_limiter.hit(f"render|{principal.subject_id}")
    if retry_after is not None:
        raise RateLimitedError(
            "Trop de rendus d'affilée. Réessaie dans un instant.",
            retry_after=retry_after,
            details={"reason": "render_rate_limited"},
        )
    return principal


RenderPrincipalDep = Annotated[Principal, Depends(throttle_render)]

#: Envois vers une adresse tierce par origine (adresse IP) et par heure
#: (ADR-0031, revue de sécurité) : dix couvrent une personne qui envoie son
#: dossier à plusieurs entreprises ; un relais en voudrait des centaines.
SEND_ORIGIN_LIMIT = 10
SEND_ORIGIN_WINDOW_SECONDS = 3_600
_send_origin_limiter = SlidingWindowLimiter(
    max_hits=SEND_ORIGIN_LIMIT, window_seconds=SEND_ORIGIN_WINDOW_SECONDS
)


def get_origin_throttle() -> OriginThrottle:
    return _send_origin_limiter


def send_origin(request: Request, settings: SettingsDep) -> str:
    """L'origine d'une demande d'envoi, lue derrière les mandataires de confiance.

    Vide quand rien n'est lisible : le cas d'usage la range alors dans un seau
    partagé (`unknown`), jamais hors plafond.
    """
    return client_ip_behind(request, proxy_hops=settings.security.trusted_proxy_hops)


SendOriginDep = Annotated[str, Depends(send_origin)]


def get_template_repository() -> TemplateRepository:
    return TortoiseTemplateRepository()


def get_user_template_repository() -> UserTemplateRepository:
    return TortoiseUserTemplateRepository()


def get_generated_cv_repository() -> GeneratedCvRepository:
    return TortoiseGeneratedCvRepository()


def get_cv_source() -> CvSource:
    return NoCvSource()


def get_account_directory() -> AccountDirectory:
    return NoAccountDirectory()


def get_send_repository() -> SendRepository:
    return TortoiseSendRepository()


def get_logo_fetcher(settings: SettingsDep) -> LogoFetcher:
    """Les logos ne se lisent que sur l'hôte public du stockage (ADR-0030 § 3)."""
    return StorageLogoFetcher(settings.storage.public_endpoint_url)


@lru_cache(maxsize=1)
def _mailer_for(provider: str, host: str, sender: str) -> Mailer:
    """Mémorise l'adaptateur d'envoi ; les arguments ne servent qu'à la clé."""
    _ = (provider, host, sender)
    return build_mailer(get_settings().mail)


def get_document_mailer(settings: SettingsDep) -> DocumentMailer:
    return MailDocumentMailer(
        _mailer_for(settings.mail.provider.value, settings.mail.host, settings.mail.sender)
    )


def build_engine(settings: Settings, storage: ObjectStorage) -> DocumentEngine:
    """Le moteur de documents (Gotenberg, polices, modèle de vision pour l'analyse)."""
    gotenberg = GotenbergClient(
        settings.documents.gotenberg_url,
        timeout_seconds=settings.documents.gotenberg_timeout_seconds,
    )
    llm = build_language_model(settings)
    return KarriaDocumentEngine(
        FontLibrary(storage),
        VisionLayoutPlanner(llm),
        gotenberg,
        letter_labeler=LetterLineLabeler(llm),
    )


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
SendsDep = Annotated[SendRepository, Depends(get_send_repository)]
AccountsDep = Annotated[AccountDirectory, Depends(get_account_directory)]
LogosDep = Annotated[LogoFetcher, Depends(get_logo_fetcher)]


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


def get_template_shop(templates: TemplatesDep, owned: OwnedDep, clock: ClockDep) -> TemplateShop:
    """La vitrine des modèles Premium, pour le module `payments` (branché par `bootstrap`)."""
    return TemplateShop(templates, owned, clock)


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


def get_render_document(
    *,
    templates: TemplatesDep,
    owned: OwnedDep,
    cvs: CvSourceDep,
    engine: EngineDep,
    storage: ObjectStorageDep,
    logos: LogosDep,
    clock: ClockDep,
) -> RenderDocument:
    """Le rendu HTML de l'atelier (ADR-0029) — aussi la façade d'un autre module."""
    return RenderDocument(
        templates=templates,
        owned=owned,
        cvs=cvs,
        engine=engine,
        storage=storage,
        logos=logos,
        clock=clock,
    )


def get_export_letter(
    *,
    templates: TemplatesDep,
    owned: OwnedDep,
    generated: GeneratedDep,
    engine: EngineDep,
    storage: ObjectStorageDep,
    logos: LogosDep,
    clock: ClockDep,
) -> ExportLetter:
    return ExportLetter(
        templates=templates,
        owned=owned,
        generated=generated,
        engine=engine,
        storage=storage,
        logos=logos,
        clock=clock,
    )


def get_send_documents(
    *,
    generated: GeneratedDep,
    sends: SendsDep,
    accounts: AccountsDep,
    jobs: JobsDep,
    clock: ClockDep,
    origins: Annotated[OriginThrottle, Depends(get_origin_throttle)],
    uow: UnitOfWorkDep,
) -> SendDocuments:
    return SendDocuments(
        generated=generated,
        sends=sends,
        accounts=accounts,
        jobs=jobs,
        clock=clock,
        origins=origins,
        uow=uow,
    )


def get_get_send(sends: SendsDep) -> GetSend:
    return GetSend(sends)


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


def build_refresh_template_colors() -> RefreshTemplateColors:
    """La remesure de la couleur de galerie, pour `scripts/templates.py refresh-colors`."""
    return RefreshTemplateColors(get_template_repository(), get_clock())


def build_publish_plain_letter(
    settings: Settings, storage: ObjectStorage
) -> PublishPlainLetterPreview:
    """Les aperçus de la lettre sobre pour la galerie, pour la commande `scripts.templates plain-letter`."""
    return PublishPlainLetterPreview(build_engine(settings, storage), storage, get_clock())


def build_explain_ats(settings: Settings) -> ExplainAts:
    """L'explication d'un score, pour le travail `documents.explain_ats`."""
    return ExplainAts(
        get_generated_cv_repository(), LlmAtsExplainer(build_language_model(settings))
    )


def build_forget_user(storage: ObjectStorage) -> ForgetDocuments:
    """L'oubli d'un compte supprimé, branché sur `AccountDeleted` par `bootstrap/wiring.py`."""
    return ForgetDocuments(
        get_generated_cv_repository(),
        get_user_template_repository(),
        storage,
        get_send_repository(),
    )


def build_deliver_documents(settings: Settings, storage: ObjectStorage) -> DeliverDocuments:
    """La remise d'un envoi, pour le travail `documents.send_documents` (ADR-0031)."""
    return DeliverDocuments(
        sends=get_send_repository(),
        generated=get_generated_cv_repository(),
        storage=storage,
        mailer=MailDocumentMailer(build_mailer(settings.mail)),
        clock=get_clock(),
    )


def build_render_document(
    settings: Settings, storage: ObjectStorage, *, cvs: CvSource | None = None
) -> RenderDocument:
    """Le rendu HTML de l'atelier, hors requête — la façade pour un autre module (règle 4).

    `cvs` est la source de CV que `bootstrap` fournit (celle de `cv`) ; sans
    elle, la photo de la personne n'est jamais posée (`photo_missing`), rien
    d'autre ne change : le CV à rendre arrive **en données**.
    """
    return RenderDocument(
        templates=get_template_repository(),
        owned=get_user_template_repository(),
        cvs=cvs or NoCvSource(),
        engine=build_engine(settings, storage),
        storage=storage,
        logos=StorageLogoFetcher(settings.storage.public_endpoint_url),
        clock=get_clock(),
    )
