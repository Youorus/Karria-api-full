"""Câblage du module `support`."""

from __future__ import annotations

import hmac
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header

from karria.modules.support.application import (
    AccountDirectory,
    ConversationFeed,
    ConversationRepository,
    CountUnreadForUser,
    ForgetUserSupport,
    GetConversationForTeam,
    GetMyConversation,
    ListConversationsForTeam,
    MarkReadByUser,
    MessageRepository,
    ReplyAsTeam,
    SendUserMessage,
    TeamAlerter,
    UserNotifier,
)
from karria.modules.support.infrastructure import (
    BrokerConversationFeed,
    MailTeamAlerter,
    SilentTeamAlerter,
    TortoiseConversationRepository,
    TortoiseMessageRepository,
)
from karria.shared.clock import Clock, SystemClock
from karria.shared.config import Settings, get_settings
from karria.shared.errors import AuthenticationError
from karria.shared.http.dependencies import (
    BrokerDep,
    ObjectStorageDep,
    SettingsDep,
    UnitOfWorkDep,
    get_unit_of_work,
)
from karria.shared.logging import get_logger
from karria.shared.mail import Mailer, build_mailer
from karria.shared.push import PushMessage
from karria.shared.storage import ObjectStorage

logger = get_logger(__name__)

#: Le chemin de la console de l'équipe, sous le préfixe de l'API.
CONSOLE_PATH = "/internal/support/console"


def get_clock() -> Clock:
    """L'horloge réelle."""
    return SystemClock()


def get_conversation_repository() -> ConversationRepository:
    """Le dépôt des conversations."""
    return TortoiseConversationRepository()


def get_message_repository() -> MessageRepository:
    """Le dépôt des messages."""
    return TortoiseMessageRepository()


def get_conversation_feed(broker: BrokerDep) -> ConversationFeed:
    """Le réveil des écrans ouverts, par le bus du socle."""
    return BrokerConversationFeed(broker)


def console_url(settings: Settings) -> str:
    """L'adresse publique de la console, pour le courriel de l'équipe."""
    return f"{settings.app.public_url.rstrip('/')}{settings.app.api_prefix}{CONSOLE_PATH}"


@lru_cache(maxsize=1)
def _mailer_for(provider: str, host: str, sender: str) -> Mailer:
    """Mémorise l'adaptateur d'envoi ; les arguments ne servent qu'à la clé."""
    _ = (provider, host, sender)
    return build_mailer(get_settings().mail)


def get_team_alerter(settings: SettingsDep) -> TeamAlerter:
    """Le courriel à l'adresse de garde — ou rien, si elle est vide."""
    if not settings.support.team_email.strip():
        return SilentTeamAlerter()
    mailer = _mailer_for(settings.mail.provider.value, settings.mail.host, settings.mail.sender)
    return MailTeamAlerter(
        mailer, team_email=settings.support.team_email, console_url=console_url(settings)
    )


class UnwiredUserNotifier:
    """Le défaut : personne n'est prévenu, et le journal le dit."""

    async def notify(self, user_id: UUID, message: PushMessage) -> int:
        """Rend 0 sans envoyer."""
        _ = message
        logger.warning("support.notifier_unwired", user_id=str(user_id))
        return 0


class UnwiredAccountDirectory:
    """Le défaut : aucune adresse connue — la liste de l'équipe le montre."""

    async def email_of(self, user_id: UUID) -> str | None:
        """Rend `None`."""
        _ = user_id
        return None


def get_user_notifier() -> UserNotifier:
    """Le port « prévenir cette personne ». `bootstrap/wiring.py` le branche sur `devices`."""
    return UnwiredUserNotifier()


def get_account_directory() -> AccountDirectory:
    """Le port « l'adresse de ce compte ». `bootstrap/wiring.py` le branche sur `identity`."""
    return UnwiredAccountDirectory()


def require_team(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    """Les routes `/internal/support/*` n'acceptent que le jeton d'équipe (temps constant)."""
    expected = settings.support.admin_token.get_secret_value()
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise AuthenticationError(
            "Jeton d'équipe invalide.", details={"reason": "admin_token_invalid"}
        )


ClockDep = Annotated[Clock, Depends(get_clock)]
ConversationsDep = Annotated[ConversationRepository, Depends(get_conversation_repository)]
MessagesDep = Annotated[MessageRepository, Depends(get_message_repository)]
FeedDep = Annotated[ConversationFeed, Depends(get_conversation_feed)]
AlerterDep = Annotated[TeamAlerter, Depends(get_team_alerter)]
NotifierDep = Annotated[UserNotifier, Depends(get_user_notifier)]
DirectoryDep = Annotated[AccountDirectory, Depends(get_account_directory)]
TeamDep = Annotated[None, Depends(require_team)]


def get_get_my_conversation(
    conversations: ConversationsDep, messages: MessagesDep, clock: ClockDep, uow: UnitOfWorkDep
) -> GetMyConversation:
    """Assemble la lecture du fil."""
    return GetMyConversation(conversations=conversations, messages=messages, clock=clock, uow=uow)


def get_mark_read_by_user(
    conversations: ConversationsDep, clock: ClockDep, uow: UnitOfWorkDep
) -> MarkReadByUser:
    """Assemble la lecture par la personne."""
    return MarkReadByUser(conversations=conversations, clock=clock, uow=uow)


def get_count_unread(conversations: ConversationsDep) -> CountUnreadForUser:
    """Assemble l'indicateur de non-lus."""
    return CountUnreadForUser(conversations=conversations)


def get_send_user_message(  # noqa: PLR0917 - FastAPI injecte par position
    conversations: ConversationsDep,
    messages: MessagesDep,
    storage: ObjectStorageDep,
    feed: FeedDep,
    alerter: AlerterDep,
    directory: DirectoryDep,
    get_conversation: Annotated[GetMyConversation, Depends(get_get_my_conversation)],
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> SendUserMessage:
    """Assemble l'envoi d'un message par la personne."""
    return SendUserMessage(
        conversations=conversations,
        messages=messages,
        storage=storage,
        feed=feed,
        alerter=alerter,
        directory=directory,
        get_conversation=get_conversation,
        clock=clock,
        uow=uow,
    )


def get_reply_as_team(  # noqa: PLR0917 - FastAPI injecte par position
    conversations: ConversationsDep,
    messages: MessagesDep,
    storage: ObjectStorageDep,
    feed: FeedDep,
    notifier: NotifierDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> ReplyAsTeam:
    """Assemble la réponse de l'équipe."""
    return ReplyAsTeam(
        conversations=conversations,
        messages=messages,
        storage=storage,
        feed=feed,
        notifier=notifier,
        clock=clock,
        uow=uow,
    )


def get_list_for_team(
    conversations: ConversationsDep, directory: DirectoryDep
) -> ListConversationsForTeam:
    """Assemble la liste de l'équipe."""
    return ListConversationsForTeam(conversations=conversations, directory=directory)


def get_conversation_for_team(
    conversations: ConversationsDep,
    messages: MessagesDep,
    directory: DirectoryDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> GetConversationForTeam:
    """Assemble l'ouverture d'un fil par l'équipe."""
    return GetConversationForTeam(
        conversations=conversations, messages=messages, directory=directory, clock=clock, uow=uow
    )


def build_forget_user(storage: ObjectStorage) -> ForgetUserSupport:
    """L'oubli d'un compte supprimé, branché sur `AccountDeleted` par `bootstrap/wiring.py`."""
    return ForgetUserSupport(
        conversations=get_conversation_repository(),
        messages=get_message_repository(),
        storage=storage,
        uow=get_unit_of_work(),
    )


GetMyConversationDep = Annotated[GetMyConversation, Depends(get_get_my_conversation)]
MarkReadByUserDep = Annotated[MarkReadByUser, Depends(get_mark_read_by_user)]
CountUnreadDep = Annotated[CountUnreadForUser, Depends(get_count_unread)]
SendUserMessageDep = Annotated[SendUserMessage, Depends(get_send_user_message)]
ReplyAsTeamDep = Annotated[ReplyAsTeam, Depends(get_reply_as_team)]
ListForTeamDep = Annotated[ListConversationsForTeam, Depends(get_list_for_team)]
GetConversationForTeamDep = Annotated[GetConversationForTeam, Depends(get_conversation_for_team)]
