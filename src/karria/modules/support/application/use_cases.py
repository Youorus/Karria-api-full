"""Écrire à l'équipe, lire le fil, répondre — et prévenir au bon moment.

Ce que la V1 faisait, et ce qu'on garde
---------------------------------------
La V1 (`app/support/`) ouvrait une conversation par personne, acceptait un
texte (ou un vocal **transcrit** en texte), envoyait un courriel à l'admin
à chaque message et un push à la personne à chaque réponse. L'app relisait
toutes les 60 s. On garde : un fil par personne, le courriel, le push. On
change : le vocal et l'image sont des **fichiers** du fil (on écoute la
voix, on voit la capture), le fil se met à jour **en direct** (ADR-0026),
et l'on se tait quand prévenir ferait doublon (règles du domaine).

Les décisions de prévenir sont prises **avant** d'appliquer le message au
fil, puis exécutées **après** la transaction : un push ne part jamais pour
un message qui n'aurait pas été écrit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from uuid import UUID, uuid4

from karria.modules.support.application.ports import (
    AccountDirectory,
    ConversationFeed,
    ConversationListing,
    ConversationRepository,
    MessageRepository,
    TeamAlerter,
    UserNotifier,
)
from karria.modules.support.domain import (
    Attachment,
    Conversation,
    Message,
    MessageAuthor,
    MessageKind,
    kind_for_content_type,
    preview_of,
)
from karria.shared.clock import Clock
from karria.shared.database import UnitOfWork
from karria.shared.errors import DependencyUnavailableError, EntityNotFoundError
from karria.shared.logging import get_logger
from karria.shared.push import PushKind, PushMessage
from karria.shared.storage import REGISTRY, Bucket, ObjectStorage, build_key, prepare_upload

logger = get_logger(__name__)

#: Ce que l'app charge à l'ouverture du fil. Au-delà, il faudrait paginer —
#: aucune conversation de support n'en approche.
HISTORY_LIMIT: Final = 200

#: Le titre et le corps de la notification quand l'équipe répond. Le corps ne
#: cite pas la réponse : un aperçu sur l'écran verrouillé d'un téléphone prêté
#: n'est pas à nous.
PUSH_TITLE: Final = "L'équipe Karria t'a répondu"
PUSH_BODY: Final = "Ouvre « Parler à l'équipe » pour lire la réponse."


@dataclass(frozen=True, slots=True)
class ConversationView:
    """Le fil tel que l'app le lit : la conversation et ses derniers messages."""

    conversation: Conversation
    messages: Sequence[Message]


class GetMyConversation:
    """`GET /support/me` : le fil de la personne, ouvert s'il n'existe pas encore."""

    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        messages: MessageRepository,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._conversations = conversations
        self._messages = messages
        self._clock = clock
        self._uow = uow

    async def execute(self, user_id: UUID) -> ConversationView:
        """Rend le fil et ses `HISTORY_LIMIT` derniers messages."""
        conversation = await self.ensure(user_id)
        messages = await self._messages.list_recent(conversation.id, limit=HISTORY_LIMIT)
        return ConversationView(conversation=conversation, messages=messages)

    async def ensure(self, user_id: UUID) -> Conversation:
        """Le fil de la personne, créé à la première visite."""
        existing = await self._conversations.find_by_user(user_id)
        if existing is not None:
            return existing
        conversation = Conversation.opened(uuid4(), user_id, at=self._clock.now())
        async with self._uow.atomic():
            await self._conversations.add(conversation)
        logger.info("support.conversation_opened", user_id=str(user_id))
        return conversation


class MarkReadByUser:
    """`POST /support/me/read` (et le `read` du flux) : la personne a le fil sous les yeux."""

    def __init__(
        self, *, conversations: ConversationRepository, clock: Clock, uow: UnitOfWork
    ) -> None:
        self._conversations = conversations
        self._clock = clock
        self._uow = uow

    async def execute(self, user_id: UUID) -> None:
        """Pose la lecture. Silencieux sans fil : il n'y a rien à lire."""
        conversation = await self._conversations.find_by_user(user_id)
        if conversation is None:
            return
        async with self._uow.atomic():
            await self._conversations.save(conversation.read_by_user(at=self._clock.now()))


class CountUnreadForUser:
    """`GET /support/me/unread` : l'indicateur discret du bouton flottant."""

    def __init__(self, *, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(self, user_id: UUID) -> bool:
        """Vrai si l'équipe a répondu depuis la dernière lecture. Ne crée rien."""
        conversation = await self._conversations.find_by_user(user_id)
        return conversation is not None and conversation.has_unread_for_user


class SendUserMessage:
    """La personne écrit — un texte, un vocal ou une image.

    Le fichier est normalisé par `prepare_upload` (ADR-0009), rangé dans le
    bucket `support`, et le message ne porte que sa clé. Puis, hors
    transaction : les écrans ouverts sont réveillés, et l'équipe est prévenue
    si la règle du domaine le dit.
    """

    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        messages: MessageRepository,
        storage: ObjectStorage,
        feed: ConversationFeed,
        alerter: TeamAlerter,
        directory: AccountDirectory,
        get_conversation: GetMyConversation,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._conversations = conversations
        self._messages = messages
        self._storage = storage
        self._feed = feed
        self._alerter = alerter
        self._directory = directory
        self._get_conversation = get_conversation
        self._clock = clock
        self._uow = uow

    async def text(self, user_id: UUID, body: str) -> Message:
        """Un message écrit."""
        conversation = await self._get_conversation.ensure(user_id)
        message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            author=MessageAuthor.USER,
            kind=MessageKind.TEXT,
            created_at=self._clock.now(),
            body=body,
        )
        return await self._post(conversation, message)

    async def media(
        self,
        user_id: UUID,
        payload: bytes,
        declared_content_type: str | None,
        *,
        duration: timedelta | None,
    ) -> Message:
        """Un vocal ou une image : la sorte se déduit du type réel du fichier."""
        conversation = await self._get_conversation.ensure(user_id)
        attachment, kind = await store_attachment(
            self._storage,
            conversation.id,
            payload,
            declared_content_type,
            duration=duration,
            clock=self._clock,
        )
        message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            author=MessageAuthor.USER,
            kind=kind,
            created_at=self._clock.now(),
            attachment=attachment,
        )
        return await self._post(conversation, message)

    async def _post(self, conversation: Conversation, message: Message) -> Message:
        now = message.created_at
        alert = conversation.team_alert_decision(at=now)
        updated = conversation.user_posted(at=now)
        if alert:
            updated = updated.team_alerted(at=now)
        async with self._uow.atomic():
            await self._messages.add(message)
            await self._conversations.save(updated)
        logger.info(
            "support.user_message_posted",
            conversation_id=str(conversation.id),
            kind=message.kind.value,
            team_alerted=alert,
        )
        await self._feed.wake(conversation.id)
        if alert:
            await self._alerter.alert(
                conversation_id=conversation.id,
                user_email=await self._directory.email_of(conversation.user_id),
                preview=preview_of(message),
                kind=message.kind,
            )
        return message


class ReplyAsTeam:
    """L'équipe répond — un texte ou une image — et la personne est prévenue.

    Le push suit `Conversation.push_decision` : rien si elle a l'écran ouvert
    (le direct suffit) ni si une réponse non lue l'attend déjà (elle a déjà
    été prévenue). Expo en panne n'annule pas la réponse : on journalise.
    """

    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        messages: MessageRepository,
        storage: ObjectStorage,
        feed: ConversationFeed,
        notifier: UserNotifier,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._conversations = conversations
        self._messages = messages
        self._storage = storage
        self._feed = feed
        self._notifier = notifier
        self._clock = clock
        self._uow = uow

    async def text(self, conversation_id: UUID, body: str) -> Message:
        """Une réponse écrite."""
        conversation = await self._require(conversation_id)
        message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            author=MessageAuthor.TEAM,
            kind=MessageKind.TEXT,
            created_at=self._clock.now(),
            body=body,
        )
        return await self._post(conversation, message)

    async def media(
        self, conversation_id: UUID, payload: bytes, declared_content_type: str | None
    ) -> Message:
        """Une image (ou un son) envoyé par l'équipe."""
        conversation = await self._require(conversation_id)
        attachment, kind = await store_attachment(
            self._storage,
            conversation.id,
            payload,
            declared_content_type,
            duration=None,
            clock=self._clock,
        )
        message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            author=MessageAuthor.TEAM,
            kind=kind,
            created_at=self._clock.now(),
            attachment=attachment,
        )
        return await self._post(conversation, message)

    async def _require(self, conversation_id: UUID) -> Conversation:
        conversation = await self._conversations.find(conversation_id)
        if conversation is None:
            raise EntityNotFoundError("Conversation", conversation_id)
        return conversation

    async def _post(self, conversation: Conversation, message: Message) -> Message:
        now = message.created_at
        push = conversation.push_decision(at=now)
        async with self._uow.atomic():
            await self._messages.add(message)
            await self._conversations.save(conversation.team_replied(at=now))
        await self._feed.wake(conversation.id)
        accepted = 0
        if push:
            try:
                accepted = await self._notifier.notify(
                    conversation.user_id,
                    PushMessage(kind=PushKind.SUPPORT, title=PUSH_TITLE, body=PUSH_BODY),
                )
            except DependencyUnavailableError as exc:
                logger.warning("support.push_failed", error=type(exc).__name__)
        logger.info(
            "support.team_reply_posted",
            conversation_id=str(conversation.id),
            kind=message.kind.value,
            pushed=push,
            devices_accepted=accepted,
        )
        return message


@dataclass(frozen=True, slots=True)
class TeamListing:
    """Une ligne de la liste de l'équipe, l'adresse du compte en plus."""

    listing: ConversationListing
    user_email: str | None


@dataclass(frozen=True, slots=True)
class TeamListPage:
    """Une page de la liste de l'équipe."""

    items: Sequence[TeamListing]
    total: int


class ListConversationsForTeam:
    """`GET /internal/support/conversations` : les fils, les plus actifs d'abord."""

    def __init__(
        self, *, conversations: ConversationRepository, directory: AccountDirectory
    ) -> None:
        self._conversations = conversations
        self._directory = directory

    async def execute(self, *, limit: int, offset: int, awaiting_only: bool) -> TeamListPage:
        """Une page, avec l'adresse de chaque compte (lue chez `identity`)."""
        listings = await self._conversations.list_for_team(
            limit=limit, offset=offset, awaiting_only=awaiting_only
        )
        total = await self._conversations.count_for_team(awaiting_only=awaiting_only)
        items = [
            TeamListing(
                listing=listing,
                user_email=await self._directory.email_of(listing.conversation.user_id),
            )
            for listing in listings
        ]
        return TeamListPage(items=items, total=total)


@dataclass(frozen=True, slots=True)
class TeamConversationView:
    """Un fil ouvert par l'équipe : la conversation, l'adresse, les messages."""

    conversation: Conversation
    user_email: str | None
    messages: Sequence[Message]


class GetConversationForTeam:
    """`GET /internal/support/conversations/{id}` : ouvrir un fil, c'est le lire."""

    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        messages: MessageRepository,
        directory: AccountDirectory,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._conversations = conversations
        self._messages = messages
        self._directory = directory
        self._clock = clock
        self._uow = uow

    async def execute(self, conversation_id: UUID) -> TeamConversationView:
        """Rend le fil et pose la lecture de l'équipe."""
        conversation = await self._conversations.find(conversation_id)
        if conversation is None:
            raise EntityNotFoundError("Conversation", conversation_id)
        read = conversation.read_by_team(at=self._clock.now())
        async with self._uow.atomic():
            await self._conversations.save(read)
        return TeamConversationView(
            conversation=read,
            user_email=await self._directory.email_of(conversation.user_id),
            messages=await self._messages.list_recent(conversation.id, limit=HISTORY_LIMIT),
        )


class ForgetUserSupport:
    """Efface le fil d'un compte supprimé, fichiers compris — réaction à `AccountDeleted`.

    Idempotent : rejouer l'événement n'efface rien et ne lève pas. Les
    fichiers partent **avant** la ligne : un fichier orphelin dans le bucket
    serait invisible ; une ligne sans fichier se voit.
    """

    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        messages: MessageRepository,
        storage: ObjectStorage,
        uow: UnitOfWork,
    ) -> None:
        self._conversations = conversations
        self._messages = messages
        self._storage = storage
        self._uow = uow

    async def execute(self, user_id: UUID) -> int:
        """Efface le fil. Rend le nombre de fichiers effacés."""
        conversation = await self._conversations.find_by_user(user_id)
        if conversation is None:
            return 0
        keys = await self._messages.attachment_keys(conversation.id)
        for key in keys:
            await self._storage.delete(Bucket.SUPPORT, key)
        async with self._uow.atomic():
            await self._conversations.delete_by_user(user_id)
        logger.info("support.user_forgotten", user_id=str(user_id), files=len(keys))
        return len(keys)


async def store_attachment(
    storage: ObjectStorage,
    conversation_id: UUID,
    payload: bytes,
    declared_content_type: str | None,
    *,
    duration: timedelta | None,
    clock: Clock,
) -> tuple[Attachment, MessageKind]:
    """Normalise et range un fichier du fil ; rend sa référence et la sorte de message.

    Une image est redressée, allégée de ses métadonnées et réduite (ADR-0009) ;
    un son passe tel quel, jugé par la politique du bucket. La clé ne porte
    que des identifiants opaques (AGENTS §6.12).
    """
    prepared = prepare_upload(Bucket.SUPPORT, payload, declared_content_type or "")
    kind = kind_for_content_type(prepared.content_type)
    key = build_key(
        "support", str(conversation_id), kind.value, extension=prepared.extension, at=clock.now()
    )
    stored = await storage.put(
        Bucket.SUPPORT,
        key,
        prepared.payload,
        content_type=prepared.content_type,
        cache_seconds=REGISTRY[Bucket.SUPPORT].cache_seconds,
    )
    attachment = Attachment(
        key=stored.key,
        content_type=stored.content_type,
        size_bytes=stored.size,
        duration=duration if kind is MessageKind.VOICE else None,
    )
    return attachment, kind
