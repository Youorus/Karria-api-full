"""Câblage des dépendances du module `applications`.

Chaque **port** a sa fonction fournisseur : c'est elle qu'un test remplace
par `app.dependency_overrides`, et c'est elle que `bootstrap/wiring.py`
branche sur un autre module (règle 4). Les ports vers les autres modules
gardent une implémentation par défaut qui **refuse** : sans branchement,
« pas de CV », « offre introuvable », « modèle verrouillé » — un oubli se
voit, il ne laisse rien passer. Les cas d'usage sont assemblés à partir
de ces ports ; un test ne les remplace jamais.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request

from karria.modules.applications.application import (
    AdaptCv,
    BaseCv,
    BaseCvReader,
    CreateDossierFromOffer,
    CvAdapter,
    CvValidator,
    DecideProposition,
    DeleteDossier,
    DossierJobs,
    DossierRepository,
    DraftLetter,
    ForgetUser,
    GetDossier,
    LetterWriter,
    ListDossiers,
    LogoUrl,
    MarkApplied,
    OfferCard,
    OfferReader,
    RegenerateParagraph,
    RetryAdaptation,
    TemplateChecker,
    UnmarkApplied,
    UpdateDossierCv,
    UpdateLetter,
)
from karria.modules.applications.domain import (
    AI_RATE_LIMIT,
    AI_RATE_WINDOW_SECONDS,
    CV_BODY_MAX_BYTES,
    CvTooLargeError,
)
from karria.modules.applications.infrastructure import (
    LlmCvAdapter,
    LlmLetterWriter,
    ProcrastinateDossierJobs,
    TortoiseDossierRepository,
)
from karria.shared.auth import AiConsentedPrincipalDep, Principal
from karria.shared.clock import Clock, SystemClock
from karria.shared.config import Settings
from karria.shared.errors import DependencyUnavailableError, RateLimitedError
from karria.shared.http import SlidingWindowLimiter
from karria.shared.http.dependencies import EventBusDep, LanguageModelDep, QueueAppDep
from karria.shared.llm import build_language_model
from karria.shared.logging import get_logger

logger = get_logger(__name__)


# --- Garde-fous des routes ------------------------------------------------

#: Le compteur des appels au modèle (créer, relancer, ébaucher, réécrire), par compte.
_ai_limiter = SlidingWindowLimiter(max_hits=AI_RATE_LIMIT, window_seconds=AI_RATE_WINDOW_SECONDS)


def throttle_ai(principal: AiConsentedPrincipalDep) -> Principal:
    """Un appel au modèle de plus pour ce compte, ou `429 ai_rate_limited`.

    Après la porte du consentement : on ne compte pas un appel qui n'aurait
    pas eu lieu.
    """
    retry_after = _ai_limiter.hit(f"ai|{principal.subject_id}")
    if retry_after is not None:
        raise RateLimitedError(
            "Karria a déjà beaucoup travaillé pour toi ces dernières minutes. Réessaie dans un instant.",
            retry_after=retry_after,
            details={"reason": "ai_rate_limited"},
        )
    return principal


AiPrincipalDep = Annotated[Principal, Depends(throttle_ai)]


async def limit_cv_body(request: Request) -> None:
    """Refuse un corps plus lourd qu'un CV (`422 cv_too_large`), avant toute lecture JSON."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > CV_BODY_MAX_BYTES:
        raise CvTooLargeError(int(declared), CV_BODY_MAX_BYTES)
    body = await request.body()
    if len(body) > CV_BODY_MAX_BYTES:
        raise CvTooLargeError(len(body), CV_BODY_MAX_BYTES)


# --- Ports : ce que les tests remplacent, ce que bootstrap branche --------


def get_clock() -> Clock:
    """L'horloge réelle."""
    return SystemClock()


def get_dossier_repository() -> DossierRepository:
    """Le dépôt des dossiers."""
    return TortoiseDossierRepository()


class UnwiredOfferReader:
    """Le défaut : aucune offre connue — `offer_not_found`, et le journal le dit."""

    async def read(self, offer_id: UUID) -> OfferCard | None:
        """Rend `None`."""
        logger.warning("applications.offer_reader_unwired", offer_id=str(offer_id))
        return None


class UnwiredBaseCvReader:
    """Le défaut : aucun CV — `cv_missing`."""

    async def read(self, user_id: UUID) -> BaseCv | None:
        """Rend `None`."""
        logger.warning("applications.base_cv_reader_unwired", user_id=str(user_id))
        return None


class UnwiredCvValidator:
    """Le défaut : personne ne sait juger un CV — 503, jamais une écriture non jugée."""

    def validate(self, cv: Mapping[str, Any]) -> dict[str, Any]:
        """Refuse."""
        _ = cv
        raise DependencyUnavailableError("validation du CV", "cv_validator_unwired")


class UnwiredTemplateChecker:
    """Le défaut : aucun modèle n'est utilisable — `template_locked`."""

    async def usable(self, user_id: UUID, template_id: UUID) -> bool:
        """Rend faux."""
        logger.warning(
            "applications.template_checker_unwired",
            user_id=str(user_id),
            template_id=str(template_id),
        )
        return False


def get_offer_reader() -> OfferReader:
    """Le port « la fiche de cette offre ». `bootstrap/wiring.py` le branche sur `offers`."""
    return UnwiredOfferReader()


def get_base_cv_reader() -> BaseCvReader:
    """Le port « le CV de base ». `bootstrap/wiring.py` le branche sur `cv`."""
    return UnwiredBaseCvReader()


def get_cv_validator() -> CvValidator:
    """Le port « la forme d'un CV ». `bootstrap/wiring.py` le branche sur `cv`."""
    return UnwiredCvValidator()


def get_template_checker() -> TemplateChecker:
    """Le port « ce modèle est-il utilisable ». `bootstrap/wiring.py` le branche sur `documents`."""
    return UnwiredTemplateChecker()


def get_logo_url() -> LogoUrl | None:
    """Le port « l'adresse d'un logo ». `bootstrap/wiring.py` le branche sur `offers` ; sinon, initiales."""
    return None


def get_cv_adapter(llm: LanguageModelDep) -> CvAdapter:
    """L'adaptation du CV, par le port LLM routé (tâche `cv_adapt`)."""
    return LlmCvAdapter(llm)


def get_letter_writer(llm: LanguageModelDep) -> LetterWriter:
    """La lettre et ses paragraphes (tâche `letter_draft`)."""
    return LlmLetterWriter(llm)


def get_dossier_jobs(queue_app: QueueAppDep) -> DossierJobs:
    """La file durable, pour l'adaptation qui dépasse le temps d'une requête."""
    return ProcrastinateDossierJobs(queue_app)


ClockDep = Annotated[Clock, Depends(get_clock)]
DossiersDep = Annotated[DossierRepository, Depends(get_dossier_repository)]
OfferReaderDep = Annotated[OfferReader, Depends(get_offer_reader)]
BaseCvReaderDep = Annotated[BaseCvReader, Depends(get_base_cv_reader)]
CvValidatorDep = Annotated[CvValidator, Depends(get_cv_validator)]
TemplateCheckerDep = Annotated[TemplateChecker, Depends(get_template_checker)]
LogoUrlDep = Annotated[LogoUrl | None, Depends(get_logo_url)]
CvAdapterDep = Annotated[CvAdapter, Depends(get_cv_adapter)]
LetterWriterDep = Annotated[LetterWriter, Depends(get_letter_writer)]
DossierJobsDep = Annotated[DossierJobs, Depends(get_dossier_jobs)]


# --- Cas d'usage : assemblés, jamais remplacés ----------------------------


def get_adapt_cv(
    dossiers: DossiersDep,
    adapter: CvAdapterDep,
    jobs: DossierJobsDep,
    validator: CvValidatorDep,
    clock: ClockDep,
) -> AdaptCv:
    """L'adaptation, bornée dans la requête puis différée."""
    return AdaptCv(dossiers=dossiers, adapter=adapter, jobs=jobs, validator=validator, clock=clock)


AdaptCvDep = Annotated[AdaptCv, Depends(get_adapt_cv)]


def get_create_dossier(  # noqa: PLR0917 - FastAPI injecte par position
    dossiers: DossiersDep,
    offers: OfferReaderDep,
    base_cvs: BaseCvReaderDep,
    templates: TemplateCheckerDep,
    adapt: AdaptCvDep,
    clock: ClockDep,
) -> CreateDossierFromOffer:
    """« Adapter mon CV à cette offre »."""
    return CreateDossierFromOffer(
        dossiers=dossiers,
        offers=offers,
        base_cvs=base_cvs,
        templates=templates,
        adapt=adapt,
        clock=clock,
    )


def get_retry_adaptation(
    dossiers: DossiersDep, adapt: AdaptCvDep, clock: ClockDep
) -> RetryAdaptation:
    return RetryAdaptation(dossiers=dossiers, adapt=adapt, clock=clock)


def get_get_dossier(dossiers: DossiersDep, base_cvs: BaseCvReaderDep) -> GetDossier:
    return GetDossier(dossiers, base_cvs)


def get_list_dossiers(dossiers: DossiersDep) -> ListDossiers:
    return ListDossiers(dossiers)


def get_delete_dossier(dossiers: DossiersDep) -> DeleteDossier:
    return DeleteDossier(dossiers)


def get_decide_proposition(
    dossiers: DossiersDep, validator: CvValidatorDep, clock: ClockDep
) -> DecideProposition:
    return DecideProposition(dossiers, validator, clock)


def get_update_dossier_cv(
    dossiers: DossiersDep, validator: CvValidatorDep, clock: ClockDep
) -> UpdateDossierCv:
    return UpdateDossierCv(dossiers, validator, clock)


def get_draft_letter(
    dossiers: DossiersDep, writer: LetterWriterDep, clock: ClockDep
) -> DraftLetter:
    return DraftLetter(dossiers, writer, clock)


def get_update_letter(dossiers: DossiersDep, clock: ClockDep) -> UpdateLetter:
    return UpdateLetter(dossiers, clock)


def get_regenerate_paragraph(
    dossiers: DossiersDep, writer: LetterWriterDep, clock: ClockDep
) -> RegenerateParagraph:
    return RegenerateParagraph(dossiers, writer, clock)


def get_mark_applied(dossiers: DossiersDep, events: EventBusDep, clock: ClockDep) -> MarkApplied:
    return MarkApplied(dossiers, events, clock)


def get_unmark_applied(
    dossiers: DossiersDep, events: EventBusDep, clock: ClockDep
) -> UnmarkApplied:
    return UnmarkApplied(dossiers, events, clock)


CreateDossierDep = Annotated[CreateDossierFromOffer, Depends(get_create_dossier)]
RetryAdaptationDep = Annotated[RetryAdaptation, Depends(get_retry_adaptation)]
GetDossierDep = Annotated[GetDossier, Depends(get_get_dossier)]
ListDossiersDep = Annotated[ListDossiers, Depends(get_list_dossiers)]
DeleteDossierDep = Annotated[DeleteDossier, Depends(get_delete_dossier)]
DecidePropositionDep = Annotated[DecideProposition, Depends(get_decide_proposition)]
UpdateDossierCvDep = Annotated[UpdateDossierCv, Depends(get_update_dossier_cv)]
DraftLetterDep = Annotated[DraftLetter, Depends(get_draft_letter)]
UpdateLetterDep = Annotated[UpdateLetter, Depends(get_update_letter)]
RegenerateParagraphDep = Annotated[RegenerateParagraph, Depends(get_regenerate_paragraph)]
MarkAppliedDep = Annotated[MarkApplied, Depends(get_mark_applied)]
UnmarkAppliedDep = Annotated[UnmarkApplied, Depends(get_unmark_applied)]


# --- Hors requête : worker et pont du bus ---------------------------------


class _NeverDefers:
    """Le worker exécute : il n'a rien à différer. Appelé, ce serait un défaut de code."""

    async def defer_adaptation(self, dossier_id: UUID) -> None:
        raise RuntimeError(f"adaptation déjà sur la file : {dossier_id}")


def build_adapt_cv(settings: Settings, validator: Callable[[], CvValidator]) -> AdaptCv:
    """L'adaptation pour le travail `applications.adapt_cv`.

    Le validateur de CV est celui d'un autre module : `bootstrap/worker.py`
    le passe (règle 4), le module ne sait pas d'où il vient.
    """
    return AdaptCv(
        dossiers=get_dossier_repository(),
        adapter=LlmCvAdapter(build_language_model(settings)),
        jobs=_NeverDefers(),
        validator=validator(),
        clock=get_clock(),
    )


def build_forget_user() -> ForgetUser:
    """L'oubli d'un compte supprimé, branché sur `AccountDeleted` par `bootstrap/wiring.py`."""
    return ForgetUser(get_dossier_repository())
