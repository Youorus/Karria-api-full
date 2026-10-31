"""« Parler à l'équipe » : les règles pures.

Ce qu'est une conversation
--------------------------
**Une par personne**, ouverte à la première visite, jamais close : la même
personne revient toujours au même fil, comme dans la V1 (`app/support/`).
Deux auteurs seulement — la personne et « l'équipe » (des humains, jamais
Karria l'IA) — et trois sortes de messages : un texte, un vocal, une image.

Ce que ce fichier décide, et que rien d'autre ne décide
-------------------------------------------------------
- la validité d'un message (texte non vide et borné ; vocal et image avec
  pièce jointe, vocal borné en durée) ;
- ce qui est **non lu**, de chaque côté, à partir des instants de dernière
  lecture et de dernier message ;
- **quand on prévient**, et quand on se tait : la notification push à la
  personne et le courriel à l'équipe suivent des règles d'ici, pour ne pas
  harceler — voir `push_decision` et `team_alert_decision`.

Aucun appel : pas de stockage, pas de push, pas de courriel. Ce sont des
décisions ; l'application les exécute.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

# Le sous-module, et non le paquet `karria.shared.errors` : son `__init__`
# tire les gestionnaires HTTP, donc Starlette, que le domaine ignore.
from karria.shared.errors.exceptions import InvalidInputError

#: Un message écrit ne dépasse pas ceci — la V1 avait la même borne.
MAX_TEXT_LENGTH: Final = 4_000
#: Un vocal au-delà de cinq minutes ne s'écoute pas : on demande d'écrire.
MAX_VOICE_DURATION: Final = timedelta(minutes=5)
#: Ce qu'on montre d'un message dans une liste ou un courriel.
PREVIEW_LENGTH: Final = 140

#: Si la personne a lu la conversation il y a moins que ceci, elle a l'écran
#: sous les yeux : le message lui arrive en direct, une notification ferait
#: doublon.
PRESENCE_WINDOW: Final = timedelta(seconds=45)
#: Un courriel à l'équipe par rafale : pas de nouveau courriel tant que
#: l'équipe n'a pas relu la conversation **et** que le dernier date de moins
#: que ceci.
ALERT_COOLDOWN: Final = timedelta(minutes=10)

IMAGE_CONTENT_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp"})
AUDIO_CONTENT_TYPES: Final = frozenset(
    {
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/aac",
        "audio/mpeg",
        "audio/wav",
        "audio/webm",
        "audio/ogg",
    }
)


class MessageAuthor(StrEnum):
    """Qui parle : la personne, ou l'équipe Karria (des humains)."""

    USER = "user"
    TEAM = "team"


class MessageKind(StrEnum):
    """La forme du message."""

    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"


def kind_for_content_type(content_type: str) -> MessageKind:
    """La sorte de message qu'un fichier de ce type porte.

    Raises:
        InvalidInputError: Ni une image ni un son acceptés
            (`support_media_unsupported`).
    """
    if content_type in IMAGE_CONTENT_TYPES:
        return MessageKind.IMAGE
    if content_type in AUDIO_CONTENT_TYPES:
        return MessageKind.VOICE
    raise InvalidInputError(
        "Seules les images (JPEG, PNG, WebP) et les messages vocaux sont acceptés.",
        details={"reason": "support_media_unsupported", "field": "file"},
    )


@dataclass(frozen=True, slots=True)
class Attachment:
    """Le fichier d'un vocal ou d'une image : sa clé, jamais une URL (AGENTS §6.12)."""

    key: str
    content_type: str
    size_bytes: int
    #: Durée d'un vocal ; `None` pour une image.
    duration: timedelta | None = None


@dataclass(frozen=True, slots=True)
class Message:
    """Un message du fil, validé à la construction.

    Un texte porte `body` ; un vocal ou une image porte `attachment`. Un
    message qui prétend l'un sans l'avoir n'existe pas : l'erreur remonte
    chez l'appelant, pas dans l'écran de la personne.
    """

    id: UUID
    conversation_id: UUID
    author: MessageAuthor
    kind: MessageKind
    created_at: datetime
    body: str | None = None
    attachment: Attachment | None = None

    def __post_init__(self) -> None:
        if self.kind is MessageKind.TEXT:
            text = (self.body or "").strip()
            if not text:
                raise InvalidInputError(
                    "Écris quelque chose avant d'envoyer.",
                    details={"reason": "support_message_empty", "field": "body"},
                )
            if len(text) > MAX_TEXT_LENGTH:
                raise InvalidInputError(
                    f"Un message fait au plus {MAX_TEXT_LENGTH} caractères.",
                    details={"reason": "support_message_too_long", "field": "body"},
                )
            object.__setattr__(self, "body", text)
            object.__setattr__(self, "attachment", None)
            return
        if self.attachment is None:
            raise InvalidInputError(
                "Ce message a besoin d'un fichier.",
                details={"reason": "support_attachment_required", "field": "file"},
            )
        if self.kind is MessageKind.VOICE:
            duration = self.attachment.duration
            if duration is not None and duration > MAX_VOICE_DURATION:
                raise InvalidInputError(
                    "Un message vocal dure au plus cinq minutes.",
                    details={"reason": "support_voice_too_long", "field": "duration_ms"},
                )
        object.__setattr__(self, "body", None)


def preview_of(message: Message) -> str:
    """Ce qu'on montre d'un message dans une liste : le texte, ou sa nature."""
    if message.kind is MessageKind.VOICE:
        return "Message vocal"
    if message.kind is MessageKind.IMAGE:
        return "Image"
    text = message.body or ""
    if len(text) <= PREVIEW_LENGTH:
        return text
    return text[: PREVIEW_LENGTH - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class Conversation:
    """Le fil d'une personne avec l'équipe.

    Attributes:
        last_user_message_at: Dernier mot de la personne.
        last_team_message_at: Dernière réponse de l'équipe.
        user_read_at: Dernière fois que la personne a ouvert le fil (ou
            reçu un message en direct, écran ouvert).
        team_read_at: Dernière fois que l'équipe a ouvert le fil.
        team_alerted_at: Dernier courriel envoyé à l'équipe.
    """

    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    last_user_message_at: datetime | None = None
    last_team_message_at: datetime | None = None
    user_read_at: datetime | None = None
    team_read_at: datetime | None = None
    team_alerted_at: datetime | None = None

    @classmethod
    def opened(cls, id: UUID, user_id: UUID, *, at: datetime) -> Conversation:  # noqa: A002
        """La conversation d'une personne, à sa première visite."""
        return cls(id=id, user_id=user_id, created_at=at, updated_at=at)

    @property
    def has_unread_for_user(self) -> bool:
        """L'équipe a-t-elle répondu depuis la dernière lecture de la personne ?"""
        return _after(self.last_team_message_at, self.user_read_at)

    @property
    def has_unread_for_team(self) -> bool:
        """La personne a-t-elle écrit depuis la dernière lecture de l'équipe ?"""
        return _after(self.last_user_message_at, self.team_read_at)

    @property
    def awaits_team(self) -> bool:
        """Le dernier mot est-il à la personne ? (la liste de l'équipe trie dessus)."""
        return _after(self.last_user_message_at, self.last_team_message_at)

    # --- ce que les messages font au fil -----------------------------

    def user_posted(self, *, at: datetime) -> Conversation:
        """La personne a écrit : elle a forcément le fil sous les yeux."""
        return replace(self, last_user_message_at=at, user_read_at=at, updated_at=at)

    def team_replied(self, *, at: datetime) -> Conversation:
        """L'équipe a répondu : elle a forcément lu."""
        return replace(self, last_team_message_at=at, team_read_at=at, updated_at=at)

    def read_by_user(self, *, at: datetime) -> Conversation:
        """La personne a ouvert le fil."""
        return replace(self, user_read_at=at)

    def read_by_team(self, *, at: datetime) -> Conversation:
        """L'équipe a ouvert le fil."""
        return replace(self, team_read_at=at)

    def team_alerted(self, *, at: datetime) -> Conversation:
        """Un courriel vient de partir vers l'équipe."""
        return replace(self, team_alerted_at=at)

    # --- quand prévenir --------------------------------------------

    def push_decision(self, *, at: datetime) -> bool:
        """Faut-il notifier la personne de la réponse qui arrive **maintenant** ?

        À évaluer **avant** `team_replied`. On se tait dans deux cas :

        - elle a l'écran ouvert — lecture il y a moins de `PRESENCE_WINDOW`
          (l'app marque lu dès qu'un message lui arrive en direct) : le
          message est déjà sous ses yeux ;
        - elle a déjà une réponse non lue : la notification est déjà partie,
          une rafale de l'équipe ne doit faire qu'**une** notification.
        """
        if self.user_read_at is not None and at - self.user_read_at < PRESENCE_WINDOW:
            return False
        return not self.has_unread_for_user

    def team_alert_decision(self, *, at: datetime) -> bool:
        """Faut-il envoyer un courriel à l'équipe pour le message qui arrive ?

        À évaluer **avant** `user_posted`. Toujours, sauf si un courriel est
        déjà parti depuis moins d'`ALERT_COOLDOWN` **et** que l'équipe n'a
        pas rouvert le fil depuis : elle est déjà prévenue, la rafale ne fait
        qu'un courriel.
        """
        if self.team_alerted_at is None:
            return True
        if _after(self.team_read_at, self.team_alerted_at) or self.team_read_at == (
            self.team_alerted_at
        ):
            return True
        return at - self.team_alerted_at >= ALERT_COOLDOWN


def _after(moment: datetime | None, reference: datetime | None) -> bool:
    """`moment` existe et est strictement après `reference` (absent = jamais)."""
    if moment is None:
        return False
    return reference is None or moment > reference
