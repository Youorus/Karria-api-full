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
    AudioTranscriber,
    ChapterInterpreter,
    CvDocumentExtractor,
    CvRepository,
    DraftRepository,
    FinalizeDraft,
    ForgetUser,
    GetDraft,
    GetMyCv,
    ImportCv,
    InterpretAnswer,
    OpenDraft,
    PhotoRepository,
    PortraitEnhancer,
    TranscribeVoice,
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
    TortoiseCvRepository,
    TortoiseDraftRepository,
    TortoisePhotoRepository,
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


def get_transcribe_voice(
    transcriber: AudioTranscriberDep, inspector: UploadInspectorDep
) -> TranscribeVoice:
    return TranscribeVoice(transcriber, inspector)


def get_upload_photo(
    enhancer: PortraitEnhancerDep,
    storage: ObjectStorageDep,
    photos: PhotoRepositoryDep,
    clock: ClockDep,
) -> UploadPhoto:
    return UploadPhoto(enhancer, storage, photos, clock)


def get_import_cv(
    extractor: CvExtractorDep,
    inspector: UploadInspectorDep,
    drafts: DraftRepositoryDep,
    clock: ClockDep,
) -> ImportCv:
    return ImportCv(extractor, inspector, drafts, clock)


def build_forget_user(storage: ObjectStorage) -> ForgetUser:
    """Assemble l'oubli d'une personne **hors requête**, pour le pont du bus.

    Le stockage est celui ouvert par le `lifespan` — un abonné n'a pas de
    requête d'où le lire ; `bootstrap` le lui passe (ADR-0015).
    """
    return ForgetUser(
        drafts=get_draft_repository(),
        cvs=get_cv_repository(),
        photos=get_photo_repository(),
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
ImportCvDep = Annotated[ImportCv, Depends(get_import_cv)]
DraftPresenterDep = Annotated[DraftPresenter, Depends(get_draft_presenter)]
