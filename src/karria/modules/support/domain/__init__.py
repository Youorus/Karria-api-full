"""Règles pures de « Parler à l'équipe »."""

from karria.modules.support.domain.conversations import (
    ALERT_COOLDOWN,
    AUDIO_CONTENT_TYPES,
    IMAGE_CONTENT_TYPES,
    MAX_TEXT_LENGTH,
    MAX_VOICE_DURATION,
    PRESENCE_WINDOW,
    PREVIEW_LENGTH,
    Attachment,
    Conversation,
    Message,
    MessageAuthor,
    MessageKind,
    kind_for_content_type,
    preview_of,
)

__all__ = [
    "ALERT_COOLDOWN",
    "AUDIO_CONTENT_TYPES",
    "IMAGE_CONTENT_TYPES",
    "MAX_TEXT_LENGTH",
    "MAX_VOICE_DURATION",
    "PRESENCE_WINDOW",
    "PREVIEW_LENGTH",
    "Attachment",
    "Conversation",
    "Message",
    "MessageAuthor",
    "MessageKind",
    "kind_for_content_type",
    "preview_of",
]
