"""Câblage des dépendances du module `cv`.

Chaque **port** a sa fonction fournisseur : c'est elle qu'un test remplace
par `app.dependency_overrides` pour exercer les routes avec un dépôt en
mémoire et un Gemini factice, sans base ni réseau. Les cas d'usage sont
assemblés à partir de ces ports — un test ne les remplace jamais, c'est
le code réel qu'il traverse.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from karria.modules.cv.api.schemas import DraftPresenter
from karria.modules.cv.application import (
    AiUsageRepository,
    AudioTranscriber,
    ChapterInterpreter,
    CvDocumentExtractor,
    CvRepository,
    DailyAiGuard,
    DraftRepository,
    EnhanceCurrentPhoto,
    FinalizeDraft,
    ForgetUser,
    GetDraft,
    GetMyCv,
    GetUsageQuota,
    ImportCv,
    InterpretAnswer,
    OfferVocabulary,
    OpenDraft,
    PhotoRepository,
    PortraitEnhancer,
    RefreshSuggestions,
    SubscriptionReader,
    SuggestCompletions,
    SuggestionIndex,
    TranscribeVoice,
    UpdateCvChapter,
    UploadInspector,
    UploadPhoto,
    ValidateChapter,
)
from karria.modules.cv.infrastructure import (
    FileInspector,
    LlmChapterInterpreter,
    LlmCvExtractor,
    LlmPortraitEnhancer,
    LlmTranscriber,
    TortoiseAiUsageRepository,
    TortoiseCvRepository,
    TortoiseDraftRepository,
    TortoisePhotoRepository,
    TortoiseSuggestionIndex,
)
from karria.shared.clock import Clock, SystemClock
from karria.shared.http.dependencies import (
    EventBusDep,
    LanguageModelDep,
    ObjectStorageDep,
    UnitOfWorkDep,
    get_unit_of_work,
)
from karria.shared.storage import Bucket, ObjectStorage

# --- Ports : ce que les tests remplacent ----------------------------------


def get_clock() -> Clock:
    return SystemClock()


def get_draft_repository() -> DraftRepository:
    return TortoiseDraftRepository()


def get_cv_repository() -> CvRepository:
    return TortoiseCvRepository()


def get_photo_repository() -> PhotoRepository:
    return TortoisePhotoRepository()


def get_ai_usage_repository() -> AiUsageRepository:
    return TortoiseAiUsageRepository()


class _NoSubscription:
    """Le défaut tant que `bootstrap` n'a rien branché : personne n'est abonné."""

    async def is_subscriber(self, user_id: UUID) -> bool:
        _ = user_id
        return False


def get_subscription_reader() -> SubscriptionReader:
    """Remplacé par `bootstrap/wiring.py` (module `payments`) ; sinon, plafonds du gratuit."""
    return _NoSubscription()


def get_upload_inspector() -> UploadInspector:
    return FileInspector()


def get_chapter_interpreter(llm: LanguageModelDep) -> ChapterInterpreter:
    """L'interprète, par le port LLM routé (tâche `cv_interpret`)."""
    return LlmChapterInterpreter(llm)


def get_audio_transcriber(llm: LanguageModelDep) -> AudioTranscriber:
    """La transcription (tâche `transcribe`)."""
    return LlmTranscriber(llm)


def get_portrait_enhancer(llm: LanguageModelDep) -> PortraitEnhancer:
    """Le portrait (tâche `portrait`, un modèle d'image)."""
    return LlmPortraitEnhancer(llm)


def get_cv_extractor(llm: LanguageModelDep) -> CvDocumentExtractor:
    """L'extraction d'un CV existant (tâche `cv_extract`)."""
    return LlmCvExtractor(llm)


ClockDep = Annotated[Clock, Depends(get_clock)]
DraftRepositoryDep = Annotated[DraftRepository, Depends(get_draft_repository)]
CvRepositoryDep = Annotated[CvRepository, Depends(get_cv_repository)]
PhotoRepositoryDep = Annotated[PhotoRepository, Depends(get_photo_repository)]
AiUsageRepositoryDep = Annotated[AiUsageRepository, Depends(get_ai_usage_repository)]
SubscriptionReaderDep = Annotated[SubscriptionReader, Depends(get_subscription_reader)]
UploadInspectorDep = Annotated[UploadInspector, Depends(get_upload_inspector)]
ChapterInterpreterDep = Annotated[ChapterInterpreter, Depends(get_chapter_interpreter)]
AudioTranscriberDep = Annotated[AudioTranscriber, Depends(get_audio_transcriber)]
PortraitEnhancerDep = Annotated[PortraitEnhancer, Depends(get_portrait_enhancer)]
CvExtractorDep = Annotated[CvDocumentExtractor, Depends(get_cv_extractor)]


# --- Cas d'usage : assemblés, jamais remplacés ----------------------------


def get_get_my_cv(cvs: CvRepositoryDep, clock: ClockDep) -> GetMyCv:
    return GetMyCv(cvs, clock)


def get_open_draft(drafts: DraftRepositoryDep, clock: ClockDep) -> OpenDraft:
    return OpenDraft(drafts, clock)


def get_get_draft(drafts: DraftRepositoryDep) -> GetDraft:
    return GetDraft(drafts)


def get_interpret_answer(interpreter: ChapterInterpreterDep) -> InterpretAnswer:
    return InterpretAnswer(interpreter)


def get_validate_chapter(
    drafts: DraftRepositoryDep, photos: PhotoRepositoryDep, clock: ClockDep
) -> ValidateChapter:
    return ValidateChapter(drafts, photos, clock)


def get_finalize_draft(
    *,
    drafts: DraftRepositoryDep,
    cvs: CvRepositoryDep,
    photos: PhotoRepositoryDep,
    storage: ObjectStorageDep,
    unit_of_work: UnitOfWorkDep,
    events: EventBusDep,
    clock: ClockDep,
) -> FinalizeDraft:
    return FinalizeDraft(
        drafts=drafts,
        cvs=cvs,
        photos=photos,
        storage=storage,
        unit_of_work=unit_of_work,
        events=events,
        clock=clock,
    )


def get_daily_ai_guard(usage: AiUsageRepositoryDep, clock: ClockDep) -> DailyAiGuard:
    """Le garde-fou journalier de l'interprétation et de la dictée."""
    return DailyAiGuard(usage, clock)


DailyAiGuardDep = Annotated[DailyAiGuard, Depends(get_daily_ai_guard)]


def get_transcribe_voice(
    transcriber: AudioTranscriberDep, inspector: UploadInspectorDep
) -> TranscribeVoice:
    return TranscribeVoice(transcriber, inspector)


def get_upload_photo(  # noqa: PLR0917 - FastAPI injecte par position
    enhancer: PortraitEnhancerDep,
    storage: ObjectStorageDep,
    photos: PhotoRepositoryDep,
    clock: ClockDep,
    usage: AiUsageRepositoryDep,
    subscriptions: SubscriptionReaderDep,
) -> UploadPhoto:
    return UploadPhoto(enhancer, storage, photos, clock, usage=usage, subscriptions=subscriptions)


def get_usage_quota(
    usage: AiUsageRepositoryDep, subscriptions: SubscriptionReaderDep, clock: ClockDep
) -> GetUsageQuota:
    return GetUsageQuota(usage, subscriptions, clock)


def get_enhance_current_photo(
    cvs: CvRepositoryDep, storage: ObjectStorageDep, upload: UploadPhotoDep
) -> EnhanceCurrentPhoto:
    return EnhanceCurrentPhoto(cvs, storage, upload)


def get_import_cv(
    *,
    extractor: CvExtractorDep,
    inspector: UploadInspectorDep,
    cvs: CvRepositoryDep,
    drafts: DraftRepositoryDep,
    unit_of_work: UnitOfWorkDep,
    events: EventBusDep,
    clock: ClockDep,
    usage: AiUsageRepositoryDep,
    subscriptions: SubscriptionReaderDep,
) -> ImportCv:
    return ImportCv(
        extractor=extractor,
        inspector=inspector,
        cvs=cvs,
        drafts=drafts,
        unit_of_work=unit_of_work,
        events=events,
        clock=clock,
        usage=usage,
        subscriptions=subscriptions,
    )


def get_update_cv_chapter(
    cvs: CvRepositoryDep, photos: PhotoRepositoryDep, storage: ObjectStorageDep, clock: ClockDep
) -> UpdateCvChapter:
    return UpdateCvChapter(cvs=cvs, photos=photos, storage=storage, clock=clock)


def build_forget_user(storage: ObjectStorage) -> ForgetUser:
    """Assemble l'oubli d'une personne **hors requête**, pour le pont du bus.

    Le stockage est celui ouvert par le `lifespan` — un abonné n'a pas de
    requête d'où le lire ; `bootstrap` le lui passe (ADR-0015).
    """
    return ForgetUser(
        drafts=get_draft_repository(),
        cvs=get_cv_repository(),
        photos=get_photo_repository(),
        ai_usage=get_ai_usage_repository(),
        storage=storage,
        unit_of_work=get_unit_of_work(),
    )


def get_draft_presenter(photos: PhotoRepositoryDep, storage: ObjectStorageDep) -> DraftPresenter:
    """Le présentateur de brouillon, capable de signer l'URL de la photo retenue."""

    async def resolve(photo_id: UUID, user_id: UUID) -> str | None:
        found = await photos.find(photo_id, user_id=user_id)
        return await storage.presigned_get_url(Bucket.MEDIA, found.image_key) if found else None

    return DraftPresenter(resolve)


GetMyCvDep = Annotated[GetMyCv, Depends(get_get_my_cv)]
OpenDraftDep = Annotated[OpenDraft, Depends(get_open_draft)]
GetDraftDep = Annotated[GetDraft, Depends(get_get_draft)]
InterpretAnswerDep = Annotated[InterpretAnswer, Depends(get_interpret_answer)]
ValidateChapterDep = Annotated[ValidateChapter, Depends(get_validate_chapter)]
FinalizeDraftDep = Annotated[FinalizeDraft, Depends(get_finalize_draft)]
TranscribeVoiceDep = Annotated[TranscribeVoice, Depends(get_transcribe_voice)]
UploadPhotoDep = Annotated[UploadPhoto, Depends(get_upload_photo)]
EnhanceCurrentPhotoDep = Annotated[EnhanceCurrentPhoto, Depends(get_enhance_current_photo)]
GetUsageQuotaDep = Annotated[GetUsageQuota, Depends(get_usage_quota)]
ImportCvDep = Annotated[ImportCv, Depends(get_import_cv)]
UpdateCvChapterDep = Annotated[UpdateCvChapter, Depends(get_update_cv_chapter)]
DraftPresenterDep = Annotated[DraftPresenter, Depends(get_draft_presenter)]


# --- Suggestions à la saisie ---------------------------------------------


def get_suggestion_index() -> SuggestionIndex:
    """Le vocabulaire matérialisé, dans PostgreSQL."""
    return TortoiseSuggestionIndex()


SuggestionIndexDep = Annotated[SuggestionIndex, Depends(get_suggestion_index)]


def get_suggest_completions(index: SuggestionIndexDep) -> SuggestCompletions:
    return SuggestCompletions(index)


SuggestCompletionsDep = Annotated[SuggestCompletions, Depends(get_suggest_completions)]


def build_refresh_suggestions(vocabulary: OfferVocabulary) -> RefreshSuggestions:
    """Assemble la matérialisation **hors requête**, pour le worker et la commande.

    Le vocabulaire est celui d'un autre module : `bootstrap/worker.py` le
    passe (règle 4), le module ne sait pas d'où il vient.
    """
    return RefreshSuggestions(vocabulary, get_suggestion_index())
