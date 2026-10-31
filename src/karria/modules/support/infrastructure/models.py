"""Modèles de persistance du module `support`.

Une conversation par compte (`user_id` unique, sans clé étrangère : `support`
ne lit pas la table d'`identity`, règle 4). Les messages portent la **clé**
du fichier, jamais une URL (AGENTS §6.12).
"""

from __future__ import annotations

from uuid import UUID

from tortoise import fields

from karria.modules.support.domain import MAX_TEXT_LENGTH, MessageAuthor, MessageKind
from karria.shared.database import BaseDBModel
from karria.shared.storage import KEY_MAX_LENGTH


class SupportConversationModel(BaseDBModel):
    """Le fil d'une personne avec l'équipe."""

    user_id = fields.UUIDField(
        unique=True,
        description="Le compte. Pas de clé étrangère : `support` ne lit pas `identity` (règle 4).",
    )
    last_user_message_at = fields.DatetimeField(null=True, db_index=True)
    last_team_message_at = fields.DatetimeField(null=True)
    user_read_at = fields.DatetimeField(null=True)
    team_read_at = fields.DatetimeField(null=True)
    team_alerted_at = fields.DatetimeField(
        null=True, description="Dernier courriel envoyé à l'équipe pour ce fil."
    )

    class Meta:
        table = "support_conversations"
        table_description = "« Parler à l'équipe » : un fil par compte, et ses lectures."


class SupportMessageModel(BaseDBModel):
    """Un message du fil : un texte, ou la clé d'un vocal ou d'une image."""

    conversation: fields.ForeignKeyRelation[SupportConversationModel] = fields.ForeignKeyField(
        "models.SupportConversationModel", related_name="messages", on_delete=fields.CASCADE
    )
    conversation_id: UUID
    author = fields.CharEnumField(MessageAuthor, max_length=8)
    kind = fields.CharEnumField(MessageKind, max_length=8)
    body = fields.TextField(
        null=True, description=f"Le texte, au plus {MAX_TEXT_LENGTH} caractères."
    )
    attachment_key = fields.CharField(
        max_length=KEY_MAX_LENGTH, null=True, description="Clé dans le bucket `support`."
    )
    attachment_content_type = fields.CharField(max_length=64, null=True)
    attachment_size_bytes = fields.IntField(null=True)
    attachment_duration_ms = fields.IntField(null=True, description="Durée d'un vocal.")

    class Meta:
        table = "support_messages"
        table_description = "Les messages des fils « Parler à l'équipe »."
        indexes = (("conversation_id", "created_at"),)
