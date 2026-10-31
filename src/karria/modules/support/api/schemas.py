"""Contrat HTTP du module `support` — `docs/contrat-api-equipe.md` de `karria_app`.

Les URL des fichiers sont **signées à la lecture** depuis la clé (AGENTS
§6.12) : l'app ne les garde pas, elle relit le fil.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from karria.modules.support.application import (
    ConversationView,
    TeamConversationView,
    TeamListing,
    TeamListPage,
)
from karria.modules.support.domain import MAX_TEXT_LENGTH, Message, MessageAuthor, MessageKind
from karria.shared.storage import Bucket, ObjectStorage


class TextMessageRequest(BaseModel):
    """`POST …/messages` : un message écrit."""

    body: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH, description="Le texte.")


class AttachmentRead(BaseModel):
    """Le fichier d'un vocal ou d'une image."""

    url: str = Field(description="URL signée. Expire ; ne pas persister.")
    content_type: str
    size_bytes: int
    duration_ms: int | None = Field(description="Durée d'un vocal, `null` pour une image.")


class MessageRead(BaseModel):
    """Un message du fil."""

    id: UUID
    author: MessageAuthor = Field(description="`user` : la personne ; `team` : l'équipe Karria.")
    kind: MessageKind
    body: str | None = Field(description="Le texte d'un message `text`, `null` sinon.")
    attachment: AttachmentRead | None
    created_at: datetime

    @classmethod
    async def present(cls, message: Message, storage: ObjectStorage) -> MessageRead:
        """Projette un message, URL signée comprise."""
        attachment = None
        if message.attachment is not None:
            attachment = AttachmentRead(
                url=await storage.presigned_get_url(Bucket.SUPPORT, message.attachment.key),
                content_type=message.attachment.content_type,
                size_bytes=message.attachment.size_bytes,
                duration_ms=(
                    None
                    if message.attachment.duration is None
                    else int(message.attachment.duration.total_seconds() * 1000)
                ),
            )
        return cls(
            id=message.id,
            author=message.author,
            kind=message.kind,
            body=message.body,
            attachment=attachment,
            created_at=message.created_at,
        )


async def present_messages(
    messages: Sequence[Message], storage: ObjectStorage
) -> list[MessageRead]:
    """Projette une suite de messages."""
    return [await MessageRead.present(m, storage) for m in messages]


class ConversationRead(BaseModel):
    """`GET /support/me` : le fil de la personne."""

    id: UUID
    has_unread: bool = Field(description="L'équipe a répondu depuis ta dernière lecture.")
    last_team_message_at: datetime | None
    messages: list[MessageRead] = Field(
        description="Les 200 derniers, du plus ancien au plus récent."
    )

    @classmethod
    async def present(cls, view: ConversationView, storage: ObjectStorage) -> ConversationRead:
        """Projette le fil."""
        return cls(
            id=view.conversation.id,
            has_unread=view.conversation.has_unread_for_user,
            last_team_message_at=view.conversation.last_team_message_at,
            messages=await present_messages(view.messages, storage),
        )


class UnreadRead(BaseModel):
    """`GET /support/me/unread` : l'indicateur du bouton flottant."""

    has_unread: bool


class StreamEvent(BaseModel):
    """Un événement du flux WebSocket `/support/me/stream`."""

    type: str = Field(description="`message` (un nouveau message), `ping` (le serveur veille).")
    message: MessageRead | None = None


# --- côté équipe ----------------------------------------------------------


class TeamConversationSummaryRead(BaseModel):
    """Une ligne de la liste de l'équipe."""

    id: UUID
    user_email: str | None
    awaits_team: bool = Field(description="Le dernier mot est à la personne.")
    has_unread: bool = Field(
        description="La personne a écrit depuis la dernière lecture de l'équipe."
    )
    message_count: int
    last_message_preview: str | None
    last_message_kind: MessageKind | None
    last_user_message_at: datetime | None
    last_team_message_at: datetime | None
    updated_at: datetime

    @classmethod
    def present(cls, item: TeamListing) -> TeamConversationSummaryRead:
        """Projette une ligne."""
        from karria.modules.support.domain import preview_of  # noqa: PLC0415 - évite un cycle de lecture

        conversation = item.listing.conversation
        last = item.listing.last_message
        return cls(
            id=conversation.id,
            user_email=item.user_email,
            awaits_team=conversation.awaits_team,
            has_unread=conversation.has_unread_for_team,
            message_count=item.listing.message_count,
            last_message_preview=None if last is None else preview_of(last),
            last_message_kind=None if last is None else last.kind,
            last_user_message_at=conversation.last_user_message_at,
            last_team_message_at=conversation.last_team_message_at,
            updated_at=conversation.updated_at,
        )


class TeamConversationListRead(BaseModel):
    """`GET /internal/support/conversations`."""

    items: list[TeamConversationSummaryRead]
    total: int

    @classmethod
    def present(cls, page: TeamListPage) -> TeamConversationListRead:
        """Projette une page."""
        return cls(
            items=[TeamConversationSummaryRead.present(i) for i in page.items], total=page.total
        )


class TeamConversationRead(BaseModel):
    """`GET /internal/support/conversations/{id}`."""

    id: UUID
    user_email: str | None
    user_read_at: datetime | None = Field(description="Jusqu'où la personne a lu.")
    messages: list[MessageRead]

    @classmethod
    async def present(
        cls, view: TeamConversationView, storage: ObjectStorage
    ) -> TeamConversationRead:
        """Projette le fil pour l'équipe."""
        return cls(
            id=view.conversation.id,
            user_email=view.user_email,
            user_read_at=view.conversation.user_read_at,
            messages=await present_messages(view.messages, storage),
        )
