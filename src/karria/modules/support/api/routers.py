"""Routes du module `support` — `docs/contrat-api-equipe.md` de l'app.

Trois surfaces :

- **la personne** (`/support/me…`, session requise) : lire son fil, écrire un
  texte ou déposer un vocal ou une image, dire qu'elle a lu, et le **flux**
  WebSocket qui lui apporte les réponses en direct ;
- **l'équipe** (`/internal/support/…`, jeton `SUPPORT__ADMIN_TOKEN`) : la
  liste des fils, un fil (l'ouvrir, c'est le lire), répondre ;
- **la console** (`/internal/support/console`) : la page HTML de l'équipe.

Le flux
-------
`GET /support/me/stream` est un WebSocket. Il exige l'en-tête
`Authorization: Bearer` (React Native sait le poser) et accepte `?since=`
(ISO 8601) pour rattraper ce qui est arrivé depuis la dernière lecture. Il
envoie `{"type": "message", "message": …}` pour chaque message nouveau —
l'équipe comme la personne elle-même depuis un autre appareil — et
`{"type": "ping"}` toutes les vingt secondes. Il lit `{"type": "read"}` pour
poser la lecture sans aller-retour HTTP. Refusé : fermeture `4401`.

Le serveur **relit la base** à chaque réveil du bus et à chaque délai :
un réveil manqué retarde de vingt secondes, il ne perd rien (ADR-0026).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, WebSocket, status
from fastapi.responses import HTMLResponse

from karria.modules.support.api.console import CONSOLE_PAGE
from karria.modules.support.api.dependencies import (
    CONSOLE_PATH,
    CountUnreadDep,
    GetConversationForTeamDep,
    GetMyConversationDep,
    ListForTeamDep,
    MarkReadByUserDep,
    MessagesDep,
    ReplyAsTeamDep,
    SendUserMessageDep,
    TeamDep,
)
from karria.modules.support.api.schemas import (
    ConversationRead,
    MessageRead,
    TeamConversationListRead,
    TeamConversationRead,
    TextMessageRequest,
    UnreadRead,
)
from karria.modules.support.api.stream import ConversationStream
from karria.shared.auth import CurrentPrincipalDep, TokenResolverDep, bearer_token
from karria.shared.errors import ErrorCode, error_responses
from karria.shared.http.dependencies import ObjectStorageDep as _StorageDep
from karria.shared.logging import get_logger
from karria.shared.storage import REGISTRY, Bucket

logger = get_logger(__name__)

router = APIRouter(tags=["Parler à l'équipe"])

#: Lu au plus pour un fichier : le plafond du bucket, plus un octet pour que
#: le dépassement soit refusé par la politique et non tronqué en silence.
_MEDIA_READ_LIMIT: Final = (REGISTRY[Bucket.SUPPORT].max_bytes or 0) + 1
#: Fermeture du flux quand le jeton n'ouvre aucune session.
WS_UNAUTHENTICATED: Final = 4401

_MEDIA_RESPONSES = error_responses(
    ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE
)


# ---------------------------------------------------------------------
# La personne
# ---------------------------------------------------------------------


@router.get(
    "/support/me",
    response_model=ConversationRead,
    summary="Mon fil avec l'équipe",
    description=(
        "Le fil de la personne connectée, ouvert à la première visite, avec ses 200 "
        "derniers messages (du plus ancien au plus récent) et `has_unread`."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def read_my_conversation(
    principal: CurrentPrincipalDep, get: GetMyConversationDep, storage: _StorageDep
) -> ConversationRead:
    """Lit le fil."""
    return await ConversationRead.present(await get.execute(principal.subject_id), storage)


@router.get(
    "/support/me/unread",
    response_model=UnreadRead,
    summary="L'équipe a-t-elle répondu depuis ma dernière lecture ?",
    description="Léger : ne crée rien, ne lit pas les messages. Pour le bouton flottant du profil.",
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def read_unread(principal: CurrentPrincipalDep, count: CountUnreadDep) -> UnreadRead:
    """L'indicateur."""
    return UnreadRead(has_unread=await count.execute(principal.subject_id))


@router.post(
    "/support/me/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Écrire à l'équipe",
    description=(
        "Un message écrit (1 à 4 000 caractères). L'équipe est prévenue par courriel — "
        "une fois par rafale — et répondra dans ce fil.\n\n"
        "`422 support_message_empty` si le texte est vide une fois nettoyé."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT),
)
async def post_text(
    principal: CurrentPrincipalDep,
    body: TextMessageRequest,
    send: SendUserMessageDep,
    storage: _StorageDep,
) -> MessageRead:
    """Écrit un texte."""
    message = await send.text(principal.subject_id, body.body)
    return await MessageRead.present(message, storage)


@router.post(
    "/support/me/messages/media",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Envoyer un message vocal ou une image à l'équipe",
    description=(
        "Multipart : `file` (JPEG, PNG, WebP, ou un son M4A/AAC/MP3/WAV/WebM/OGG, 15 Mo au "
        "plus) et, pour un vocal, `duration_ms`. La sorte du message se déduit du **type réel** "
        "du fichier, pas de son étiquette ; une image est redressée et allégée.\n\n"
        "`422 support_media_unsupported` pour un autre type ; `422 support_voice_too_long` "
        "au-delà de cinq minutes."
    ),
    responses=_MEDIA_RESPONSES,
)
async def post_media(
    principal: CurrentPrincipalDep,
    send: SendUserMessageDep,
    storage: _StorageDep,
    file: Annotated[UploadFile, File(description="Le fichier : image ou son.")],
    duration_ms: Annotated[
        int | None, Form(ge=1, le=600_000, description="Durée d'un vocal, en millisecondes.")
    ] = None,
) -> MessageRead:
    """Dépose un fichier dans le fil."""
    payload = await file.read(_MEDIA_READ_LIMIT)
    message = await send.media(
        principal.subject_id,
        payload,
        file.content_type,
        duration=None if duration_ms is None else timedelta(milliseconds=duration_ms),
    )
    return await MessageRead.present(message, storage)


@router.post(
    "/support/me/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="J'ai lu le fil",
    description="Pose la lecture : plus de non-lu, et pas de notification pour ce qui arrive sous mes yeux.",
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def mark_read(principal: CurrentPrincipalDep, mark: MarkReadByUserDep) -> None:
    """Marque lu."""
    await mark.execute(principal.subject_id)


@router.websocket("/support/me/stream")
async def stream(  # noqa: PLR0917 - FastAPI injecte par position
    websocket: WebSocket,
    resolver: TokenResolverDep,
    get: GetMyConversationDep,
    mark: MarkReadByUserDep,
    messages: MessagesDep,
    since: Annotated[datetime | None, Query()] = None,
) -> None:
    """Le flux en direct du fil — voir l'en-tête du module."""
    token = bearer_token(websocket.headers.get("authorization"))
    principal = None if token is None else await resolver.resolve(token)
    if principal is None:
        await websocket.close(code=WS_UNAUTHENTICATED)
        return
    conversation = await get.ensure(principal.subject_id)
    cursor = await ConversationStream.initial_cursor(messages, conversation.id, since)
    await websocket.accept()
    logger.info("support.stream_opened", conversation_id=str(conversation.id))
    await ConversationStream(
        websocket,
        conversation_id=conversation.id,
        user_id=principal.subject_id,
        cursor=cursor,
        messages=messages,
        mark_read=mark,
        storage=websocket.app.state.object_storage,
        broker=websocket.app.state.broker,
    ).run()


# ---------------------------------------------------------------------
# L'équipe
# ---------------------------------------------------------------------


@router.get(
    "/internal/support/conversations",
    response_model=TeamConversationListRead,
    summary="Les fils, pour l'équipe",
    description=(
        "Jeton d'équipe (`SUPPORT__ADMIN_TOKEN`) en `Authorization: Bearer`. Les plus "
        "récemment actifs d'abord ; `awaiting_only=true` ne garde que ceux dont le dernier "
        "mot est à la personne."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def list_for_team(
    _: TeamDep,
    use_case: ListForTeamDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    awaiting_only: Annotated[bool, Query()] = False,
) -> TeamConversationListRead:
    """La liste."""
    page = await use_case.execute(limit=limit, offset=offset, awaiting_only=awaiting_only)
    return TeamConversationListRead.present(page)


@router.get(
    "/internal/support/conversations/{conversation_id}",
    response_model=TeamConversationRead,
    summary="Un fil, pour l'équipe",
    description="Ouvrir le fil pose la lecture de l'équipe : la personne n'est plus « en attente ».",
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def read_for_team(
    _: TeamDep, conversation_id: UUID, use_case: GetConversationForTeamDep, storage: _StorageDep
) -> TeamConversationRead:
    """Le fil."""
    return await TeamConversationRead.present(await use_case.execute(conversation_id), storage)


@router.post(
    "/internal/support/conversations/{conversation_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Répondre par écrit",
    description=(
        "La réponse apparaît en direct sur le téléphone si le fil est ouvert ; sinon la "
        "personne reçoit une notification (`support`), une seule par rafale."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.INVALID_INPUT
    ),
)
async def reply_text(
    _: TeamDep,
    conversation_id: UUID,
    body: TextMessageRequest,
    reply: ReplyAsTeamDep,
    storage: _StorageDep,
) -> MessageRead:
    """Répond."""
    return await MessageRead.present(await reply.text(conversation_id, body.body), storage)


@router.post(
    "/internal/support/conversations/{conversation_id}/messages/media",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Répondre avec une image (ou un son)",
    description="Multipart `file`, mêmes types et bornes que pour la personne.",
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.NOT_FOUND,
        ErrorCode.INVALID_INPUT,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def reply_media(
    _: TeamDep,
    conversation_id: UUID,
    reply: ReplyAsTeamDep,
    storage: _StorageDep,
    file: Annotated[UploadFile, File(description="Image ou son.")],
) -> MessageRead:
    """Répond avec un fichier."""
    payload = await file.read(_MEDIA_READ_LIMIT)
    return await MessageRead.present(
        await reply.media(conversation_id, payload, file.content_type), storage
    )


@router.get(
    CONSOLE_PATH,
    include_in_schema=False,
    response_class=HTMLResponse,
    summary="La console de l'équipe",
)
async def console() -> HTMLResponse:
    """Sert la page ; elle demande le jeton elle-même et ne sait rien d'autre."""
    return HTMLResponse(CONSOLE_PAGE, headers={"Cache-Control": "no-store"})
