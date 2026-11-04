"""Routes HTTP du module `cv` — `docs/contrat-api-cv.md`, en exécutable.

Toutes exigent la session : le CV est *le sien*, et rien ici ne concerne
quelqu'un d'autre — les suggestions aussi, qui n'ont de sens qu'en
éditant son CV. Une route reçoit, délègue, projette. Les fichiers reçus
sont lus **bornés** (`read(limite + 1)`) : un envoi de deux gigaoctets ne
doit pas pouvoir remplir la mémoire avant qu'une règle le refuse.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from karria.modules.cv.api.dependencies import (
    DraftPresenterDep,
    FinalizeDraftDep,
    GetDraftDep,
    GetMyCvDep,
    ImportCvDep,
    InterpretAnswerDep,
    OpenDraftDep,
    SuggestCompletionsDep,
    TranscribeVoiceDep,
    UpdateCvChapterDep,
    UploadPhotoDep,
    ValidateChapterDep,
)
from karria.modules.cv.api.schemas import (
    ChapterSubmissionRequest,
    CvRead,
    DraftRead,
    ImportRead,
    InterpretationRead,
    InterpretRequest,
    PhotoRead,
    SuggestionsRead,
    TranscriptionRead,
)
from karria.modules.cv.domain import (
    MAX_AUDIO_BYTES,
    MAX_DOCUMENT_BYTES,
    QUERY_MAX_LENGTH,
    Chapter,
    ChapterSubmission,
    ReceivedFile,
    SuggestionKind,
)
from karria.shared.auth import (
    AiConsentedPrincipalDep,
    AiConsentGateDep,
    CurrentPrincipalDep,
    require_authentication,
)
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


@cv_router.put(
    "/me/chapters/{chapter}",
    summary="Modifier une section de mon CV",
    response_model=CvRead,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.INVALID_INPUT
    ),
)
async def update_cv_chapter(
    chapter: Chapter,
    body: ChapterSubmissionRequest,
    principal: CurrentPrincipalDep,
    update: UpdateCvChapterDep,
    storage: ObjectStorageDep,
) -> CvRead:
    """L'édition manuelle : la section est remplacée d'un bloc, revalidée par le schéma du chapitre.

    `data` pour `identity`, `contact`, `summary`, `skills` (compétences et
    langues ensemble), `interests` et `photo` (`{ image_id }` rendu par
    `POST /cv/photo`, ou `{ image_id: null }` pour la retirer) ; `items`
    (éventuellement vide, dans l'ordre voulu) pour `experiences` et
    `educations` ; `skipped: true` vide une section facultative.
    `404 cv_not_found` sans CV ; `422 chapter_invalid` avec `fields`.
    """
    cv = await update.execute(
        principal.subject_id,
        chapter,
        ChapterSubmission(data=body.data, items=body.items, skipped=body.skipped),
    )
    return await CvRead.present(cv, storage)


@cv_router.get(
    "/suggestions",
    summary="Suggestions à la saisie",
    response_model=SuggestionsRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT),
)
async def suggest(
    suggest_completions: SuggestCompletionsDep,
    kind: Annotated[SuggestionKind, Query(description="Ce qu'on complète.")],
    q: Annotated[
        str,
        Query(
            max_length=QUERY_MAX_LENGTH,
            description="Ce que la personne a tapé. Casse et accents indifférents.",
        ),
    ] = "",
) -> SuggestionsRead:
    """Des valeurs connues qui commencent par la saisie (ou dont un mot commence par elle).

    `title`, `company` et `city` viennent de l'index d'offres (matérialisé
    chaque nuit) ; `school`, `degree`, `skill` et `language` de listes de
    référence embarquées. Huit au plus ; vide pour une saisie vide.
    """
    return SuggestionsRead(items=await suggest_completions.execute(kind, q))


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
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.INVALID_INPUT,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def interpret_answer(
    body: InterpretRequest, interpret: InterpretAnswerDep, _principal: AiConsentedPrincipalDep
) -> InterpretationRead:
    """Un premier jet au format du chapitre, ou une relance (`off_topic`).

    La réponse est une donnée : aucune instruction qu'elle contient n'est
    suivie. `503` avec `details.reason = ai_unavailable` si le modèle ne
    répond pas. `403 ai_consent_required` sans consentement à l'IA (ADR-0025).
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
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.INVALID_INPUT,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def upload_photo(  # noqa: PLR0917 - FastAPI injecte par position
    principal: CurrentPrincipalDep,
    gate: AiConsentGateDep,
    upload: UploadPhotoDep,
    storage: ObjectStorageDep,
    image: Annotated[UploadFile, File(description="La photo (JPEG, PNG, WebP, HEIC).")],
    enhance: Annotated[
        bool, Form(description="Vrai si la personne a demandé l'amélioration.")
    ] = False,
) -> PhotoRead:
    """`422 photo_enhance_refused` si le générateur refuse ; `503 photo_enhance_unavailable` s'il est en panne.

    Sans amélioration, la photo ne quitte pas Karria : la porte d'IA ne joue
    que si `enhance` est vrai (`403 ai_consent_required` sinon, ADR-0025).
    """
    if enhance:
        await gate.ensure(principal)
    payload = await image.read(_PHOTO_READ_LIMIT)
    photo = await upload.execute(principal.subject_id, payload, image.content_type, enhance=enhance)
    return await PhotoRead.present(photo, storage)


@cv_router.post(
    "/import",
    summary="Importer un CV existant : il devient mon CV",
    response_model=ImportRead,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.INVALID_INPUT,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def import_cv(
    principal: AiConsentedPrincipalDep,
    import_cv: ImportCvDep,
    storage: ObjectStorageDep,
    file: Annotated[
        list[UploadFile],
        File(
            description=(
                "Le CV : un PDF, un DOCX ou une image (10 Mo, 10 pages au plus) — ou **plusieurs "
                "images**, les photos des pages d'un même CV, dans l'ordre (même champ répété)."
            )
        ),
    ],
) -> ImportRead:
    """Le CV lu remplace les sections du CV de la personne (la photo reste) ; le brouillon éventuel est supprimé.

    La réponse dit ce qui a **vraiment** été retenu (`kept`), écarté
    (`discarded`) et ce qu'il reste à vérifier (`missing`). Rien n'est écrit
    si la lecture échoue. `422 document_not_a_cv` si rien d'un CV n'y est
    lu ; `403 ai_consent_required` sans consentement à l'IA (ADR-0025).
    """
    received = [
        ReceivedFile(
            payload=await part.read(MAX_DOCUMENT_BYTES + 1), content_type=part.content_type or ""
        )
        for part in file
    ]
    result = await import_cv.execute(principal.subject_id, received)
    return await ImportRead.present(result, storage)


@voice_router.post(
    "/transcribe",
    summary="Transcrire un enregistrement",
    response_model=TranscriptionRead,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.INVALID_INPUT,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def transcribe(
    transcribe_voice: TranscribeVoiceDep,
    _principal: AiConsentedPrincipalDep,
    audio_file: Annotated[
        UploadFile, File(description="M4A, MP3, WAV, WebM ou OGG, 5 minutes au plus.")
    ],
) -> TranscriptionRead:
    """Le texte entendu, en français. Même contrat que la V1.

    `403 ai_consent_required` sans consentement à l'IA (ADR-0025).
    """
    payload = await audio_file.read(MAX_AUDIO_BYTES + 1)
    text = await transcribe_voice.execute(payload, audio_file.content_type)
    return TranscriptionRead(text=text)


router.include_router(cv_router)
router.include_router(voice_router)
