"""Routes HTTP du module `cv` — `docs/contrat-api-cv.md`, en exécutable.

Toutes exigent la session : le CV est *le sien*, et rien ici ne concerne
quelqu'un d'autre. Une route reçoit, délègue, projette. Les fichiers reçus
sont lus **bornés** (`read(limite + 1)`) : un envoi de deux gigaoctets ne
doit pas pouvoir remplir la mémoire avant qu'une règle le refuse.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from karria.modules.cv.api.dependencies import (
    DraftPresenterDep,
    FinalizeDraftDep,
    GetDraftDep,
    GetMyCvDep,
    ImportCvDep,
    InterpretAnswerDep,
    OpenDraftDep,
    TranscribeVoiceDep,
    UploadPhotoDep,
    ValidateChapterDep,
)
from karria.modules.cv.api.schemas import (
    ChapterSubmissionRequest,
    CvRead,
    DraftRead,
    InterpretationRead,
    InterpretRequest,
    PhotoRead,
    TranscriptionRead,
)
from karria.modules.cv.domain import (
    MAX_AUDIO_BYTES,
    MAX_DOCUMENT_BYTES,
    Chapter,
    ChapterSubmission,
)
from karria.shared.auth import CurrentPrincipalDep, require_authentication
from karria.shared.errors import ErrorCode, error_responses
from karria.shared.http.dependencies import ObjectStorageDep
from karria.shared.storage import REGISTRY, Bucket

router = APIRouter(dependencies=[Depends(require_authentication)])
cv_router = APIRouter(prefix="/cv", tags=["CV"])
voice_router = APIRouter(prefix="/voice", tags=["Voix"])

#: Taille lue au plus pour une photo : le plafond du bucket, plus un octet
#: pour que la politique puisse constater le dépassement.
_PHOTO_READ_LIMIT = (REGISTRY[Bucket.MEDIA].max_bytes or 5 * 1024 * 1024) * 4


@cv_router.get(
    "/me",
    summary="Mon CV",
    response_model=CvRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def read_my_cv(
    principal: CurrentPrincipalDep, get_my_cv: GetMyCvDep, storage: ObjectStorageDep
) -> CvRead:
    """Le CV au format V1, plus `identity` et `contact`. Sections vides s'il n'existe pas encore."""
    cv = await get_my_cv.execute(principal.subject_id)
    return await CvRead.present(cv, storage)


@cv_router.get(
    "/draft",
    summary="Mon brouillon en cours",
    response_model=DraftRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def read_draft(
    principal: CurrentPrincipalDep, get_draft: GetDraftDep, presenter: DraftPresenterDep
) -> DraftRead:
    """`404` avec `details.reason = draft_not_found` s'il n'y en a pas."""
    return await presenter.present(await get_draft.execute(principal.subject_id))


@cv_router.post(
    "/draft",
    summary="Ouvrir le brouillon",
    response_model=DraftRead,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def open_draft(
    principal: CurrentPrincipalDep, open_draft: OpenDraftDep, presenter: DraftPresenterDep
) -> DraftRead:
    """Crée le brouillon, ou rend celui qui existe. Idempotent."""
    return await presenter.present(await open_draft.execute(principal.subject_id))


@cv_router.post(
    "/draft/interpret",
    summary="Interpréter une réponse",
    response_model=InterpretationRead,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def interpret_answer(
    body: InterpretRequest, interpret: InterpretAnswerDep, _principal: CurrentPrincipalDep
) -> InterpretationRead:
    """Un premier jet au format du chapitre, ou une relance (`off_topic`).

    La réponse est une donnée : aucune instruction qu'elle contient n'est
    suivie. `503` avec `details.reason = ai_unavailable` si le modèle ne
    répond pas.
    """
    result = await interpret.execute(body.chapter, body.answer, body.correction_of)
    return InterpretationRead(
        proposal=result.proposal,
        missing=list(result.missing),
        follow_up=result.follow_up,
        off_topic=result.off_topic,
    )


@cv_router.put(
    "/draft/chapters/{chapter}",
    summary="Valider ou passer un chapitre",
    response_model=DraftRead,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.INVALID_INPUT
    ),
)
async def submit_chapter(
    chapter: Chapter,
    body: ChapterSubmissionRequest,
    principal: CurrentPrincipalDep,
    validate_chapter: ValidateChapterDep,
    presenter: DraftPresenterDep,
) -> DraftRead:
    """Revalide le contenu avec le schéma V1 du chapitre (`422 chapter_invalid` sinon)."""
    draft = await validate_chapter.execute(
        principal.subject_id,
        chapter,
        ChapterSubmission(data=body.data, items=body.items, skipped=body.skipped),
    )
    return await presenter.present(draft)


@cv_router.post(
    "/draft/finalize",
    summary="Finaliser le brouillon",
    response_model=CvRead,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.RULE_VIOLATION
    ),
)
async def finalize_draft(
    principal: CurrentPrincipalDep, finalize: FinalizeDraftDep, storage: ObjectStorageDep
) -> CvRead:
    """Le brouillon devient le CV, dans une transaction. `409 draft_incomplete` sinon."""
    cv = await finalize.execute(principal.subject_id)
    return await CvRead.present(cv, storage)


@cv_router.post(
    "/photo",
    summary="Envoyer une photo, améliorée ou non",
    response_model=PhotoRead,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def upload_photo(
    principal: CurrentPrincipalDep,
    upload: UploadPhotoDep,
    storage: ObjectStorageDep,
    image: Annotated[UploadFile, File(description="La photo (JPEG, PNG, WebP, HEIC).")],
    enhance: Annotated[
        bool, Form(description="Vrai si la personne a demandé l'amélioration.")
    ] = False,
) -> PhotoRead:
    """`422 photo_enhance_refused` si le générateur refuse ; `503 photo_enhance_unavailable` s'il est en panne."""
    payload = await image.read(_PHOTO_READ_LIMIT)
    photo = await upload.execute(principal.subject_id, payload, image.content_type, enhance=enhance)
    return await PhotoRead.present(photo, storage)


@cv_router.post(
    "/import",
    summary="Importer un CV existant dans le brouillon",
    response_model=DraftRead,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def import_cv(
    principal: CurrentPrincipalDep,
    import_cv: ImportCvDep,
    presenter: DraftPresenterDep,
    file: Annotated[
        UploadFile, File(description="PDF, DOCX ou image du CV, 10 Mo et 10 pages au plus.")
    ],
) -> DraftRead:
    """Les chapitres extraits sont posés en **propositions** (`in_progress`), à valider un à un."""
    payload = await file.read(MAX_DOCUMENT_BYTES + 1)
    draft = await import_cv.execute(principal.subject_id, payload, file.content_type)
    return await presenter.present(draft)


@voice_router.post(
    "/transcribe",
    summary="Transcrire un enregistrement",
    response_model=TranscriptionRead,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def transcribe(
    transcribe_voice: TranscribeVoiceDep,
    _principal: CurrentPrincipalDep,
    audio_file: Annotated[
        UploadFile, File(description="M4A, MP3, WAV, WebM ou OGG, 5 minutes au plus.")
    ],
) -> TranscriptionRead:
    """Le texte entendu, en français. Même contrat que la V1."""
    payload = await audio_file.read(MAX_AUDIO_BYTES + 1)
    text = await transcribe_voice.execute(payload, audio_file.content_type)
    return TranscriptionRead(text=text)


router.include_router(cv_router)
router.include_router(voice_router)
