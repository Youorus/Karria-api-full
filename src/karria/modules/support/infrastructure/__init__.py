"""Adaptateurs du module `support` : base, courriel, réveils."""

from karria.modules.support.infrastructure.alerts import MailTeamAlerter, SilentTeamAlerter
from karria.modules.support.infrastructure.feed import BrokerConversationFeed, topic_of
from karria.modules.support.infrastructure.repositories import (
    TortoiseConversationRepository,
    TortoiseMessageRepository,
)

__all__ = [
    "BrokerConversationFeed",
    "MailTeamAlerter",
    "SilentTeamAlerter",
    "TortoiseConversationRepository",
    "TortoiseMessageRepository",
    "topic_of",
]
