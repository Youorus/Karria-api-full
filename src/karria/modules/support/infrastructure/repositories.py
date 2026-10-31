"""Dépôts Tortoise du module `support`. Ne rendent jamais un modèle."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from tortoise.expressions import Q

from karria.modules.support.application import ConversationListing
from karria.modules.support.domain import Attachment, Conversation, Message
from karria.modules.support.infrastructure.models import (
    SupportConversationModel,
    SupportMessageModel,
)


def _to_conversation(row: SupportConversationModel) -> Conversation:
    return Conversation(
        id=row.id,
        user_id=row.user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_user_message_at=row.last_user_message_at,
        last_team_message_at=row.last_team_message_at,
        user_read_at=row.user_read_at,
        team_read_at=row.team_read_at,
        team_alerted_at=row.team_alerted_at,
    )


def _conversation_columns(conversation: Conversation) -> dict[str, Any]:
    return {
        "user_id": conversation.user_id,
        "last_user_message_at": conversation.last_user_message_at,
        "last_team_message_at": conversation.last_team_message_at,
        "user_read_at": conversation.user_read_at,
        "team_read_at": conversation.team_read_at,
        "team_alerted_at": conversation.team_alerted_at,
        "updated_at": conversation.updated_at,
    }


def _to_message(row: SupportMessageModel) -> Message:
    attachment = None
    if row.attachment_key is not None:
        attachment = Attachment(
            key=row.attachment_key,
            content_type=row.attachment_content_type or "application/octet-stream",
            size_bytes=row.attachment_size_bytes or 0,
            duration=(
                None
                if row.attachment_duration_ms is None
                else timedelta(milliseconds=row.attachment_duration_ms)
            ),
        )
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        author=row.author,
        kind=row.kind,
        created_at=row.created_at,
        body=row.body,
        attachment=attachment,
    )


class TortoiseConversationRepository:
    """Accès persistant aux conversations."""

    async def find_by_user(self, user_id: UUID) -> Conversation | None:
        """Le fil de cette personne."""
        row = await SupportConversationModel.get_or_none(user_id=user_id)
        return None if row is None else _to_conversation(row)

    async def find(self, conversation_id: UUID) -> Conversation | None:
        """Un fil par identifiant."""
        row = await SupportConversationModel.get_or_none(id=conversation_id)
        return None if row is None else _to_conversation(row)

    async def add(self, conversation: Conversation) -> None:
        """Ouvre un fil."""
        await SupportConversationModel.create(
            id=conversation.id, **_conversation_columns(conversation)
        )

    async def save(self, conversation: Conversation) -> None:
        """Écrit l'état complet d'un fil."""
        await SupportConversationModel.filter(id=conversation.id).update(
            **_conversation_columns(conversation)
        )

    async def list_for_team(
        self, *, limit: int, offset: int, awaiting_only: bool
    ) -> Sequence[ConversationListing]:
        """Les fils les plus récemment actifs d'abord, avec dernier message et volume."""
        rows = (
            await self._team_query(awaiting_only)
            .order_by("-updated_at")
            .offset(offset)
            .limit(limit)
        )
        listings: list[ConversationListing] = []
        for row in rows:
            last = (
                await SupportMessageModel.filter(conversation_id=row.id)
                .order_by("-created_at")
                .first()
            )
            count = await SupportMessageModel.filter(conversation_id=row.id).count()
            listings.append(
                ConversationListing(
                    conversation=_to_conversation(row),
                    last_message=None if last is None else _to_message(last),
                    message_count=count,
                )
            )
        return listings

    async def count_for_team(self, *, awaiting_only: bool) -> int:
        """Combien de fils, avec le même filtre que la liste."""
        total: int = await self._team_query(awaiting_only).count()
        return total

    @staticmethod
    def _team_query(awaiting_only: bool) -> Any:
        query = SupportConversationModel.all()
        if awaiting_only:
            # Le dernier mot est à la personne : elle a écrit et l'équipe n'a
            # pas (encore) répondu après.
            query = query.filter(last_user_message_at__isnull=False).filter(
                Q(last_team_message_at__isnull=True)
                | Q(last_team_message_at__lt=models_field("last_user_message_at"))
            )
        return query

    async def delete_by_user(self, user_id: UUID) -> int:
        """Efface le fil d'une personne ; les messages suivent (cascade)."""
        deleted: int = await SupportConversationModel.filter(user_id=user_id).delete()
        return deleted


def models_field(name: str) -> Any:
    """Référence une colonne dans une comparaison (`F`), sans l'importer partout."""
    from tortoise.expressions import F  # noqa: PLC0415 - détail Tortoise

    return F(name)


class TortoiseMessageRepository:
    """Accès persistant aux messages."""

    async def add(self, message: Message) -> None:
        """Enregistre un message."""
        attachment = message.attachment
        await SupportMessageModel.create(
            id=message.id,
            conversation_id=message.conversation_id,
            author=message.author,
            kind=message.kind,
            body=message.body,
            attachment_key=None if attachment is None else attachment.key,
            attachment_content_type=None if attachment is None else attachment.content_type,
            attachment_size_bytes=None if attachment is None else attachment.size_bytes,
            attachment_duration_ms=(
                None
                if attachment is None or attachment.duration is None
                else int(attachment.duration.total_seconds() * 1000)
            ),
            created_at=message.created_at,
        )

    async def list_recent(self, conversation_id: UUID, *, limit: int) -> Sequence[Message]:
        """Les derniers messages, rendus du plus ancien au plus récent."""
        rows = (
            await SupportMessageModel.filter(conversation_id=conversation_id)
            .order_by("-created_at")
            .limit(limit)
        )
        return [_to_message(row) for row in reversed(rows)]

    async def list_after(self, conversation_id: UUID, *, after: datetime) -> Sequence[Message]:
        """Les messages strictement postérieurs à cet instant."""
        rows = await SupportMessageModel.filter(
            conversation_id=conversation_id, created_at__gt=after
        ).order_by("created_at")
        return [_to_message(row) for row in rows]

    async def attachment_keys(self, conversation_id: UUID) -> Sequence[str]:
        """Les clés des fichiers du fil."""
        rows = await SupportMessageModel.filter(
            conversation_id=conversation_id, attachment_key__isnull=False
        ).values_list("attachment_key", flat=True)
        return [str(key) for key in rows]
