"""Ports du module `support`.

Chacun décrit un besoin. La base, le bucket, le push, le courriel, le réveil
en direct : derrière. Deux ports sont branchés par `bootstrap/wiring.py` sur
d'autres modules (règle 4) : `UserNotifier` (→ `devices`) et
`AccountDirectory` (→ `identity`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from karria.modules.support.domain import Conversation, Message, MessageKind
from karria.shared.push import PushMessage


@dataclass(frozen=True, slots=True)
class ConversationListing:
    """Une ligne de la liste de l'équipe : le fil, son dernier message, son volume."""

    conversation: Conversation
    last_message: Message | None
    message_count: int


class ConversationRepository(Protocol):
    """Port : accès persistant aux conversations."""

    async def find_by_user(self, user_id: UUID) -> Conversation | None:
        """Le fil de cette personne, s'il existe."""
        ...

    async def find(self, conversation_id: UUID) -> Conversation | None:
        """Un fil par son identifiant."""
        ...

    async def add(self, conversation: Conversation) -> None:
        """Ouvre un fil."""
        ...

    async def save(self, conversation: Conversation) -> None:
        """Écrit l'état complet d'un fil existant."""
        ...

    async def list_for_team(
        self, *, limit: int, offset: int, awaiting_only: bool
    ) -> Sequence[ConversationListing]:
        """Les fils, les plus récemment actifs d'abord ; `awaiting_only` : le dernier mot est à la personne."""
        ...

    async def count_for_team(self, *, awaiting_only: bool) -> int:
        """Combien de fils, avec le même filtre."""
        ...

    async def delete_by_user(self, user_id: UUID) -> int:
        """Efface le fil d'une personne (ses messages suivent). Rend 1 ou 0."""
        ...


class MessageRepository(Protocol):
    """Port : accès persistant aux messages."""

    async def add(self, message: Message) -> None:
        """Enregistre un message."""
        ...

    async def list_recent(self, conversation_id: UUID, *, limit: int) -> Sequence[Message]:
        """Les `limit` derniers messages, du plus ancien au plus récent."""
        ...

    async def list_after(self, conversation_id: UUID, *, after: datetime) -> Sequence[Message]:
        """Les messages créés strictement après cet instant, du plus ancien au plus récent."""
        ...

    async def attachment_keys(self, conversation_id: UUID) -> Sequence[str]:
        """Les clés des fichiers du fil, pour les effacer avec lui."""
        ...


class UserNotifier(Protocol):
    """Port : prévenir une personne. Branché sur `devices` par `bootstrap/wiring.py`."""

    async def notify(self, user_id: UUID, message: PushMessage) -> int:
        """Envoie à tous les appareils joignables. Rend le nombre accepté."""
        ...


class AccountDirectory(Protocol):
    """Port : l'adresse d'un compte, pour la liste de l'équipe. Branché sur `identity`."""

    async def email_of(self, user_id: UUID) -> str | None:
        """L'adresse, ou `None` si le compte n'existe plus (ou si rien n'est branché)."""
        ...


class TeamAlerter(Protocol):
    """Port : prévenir l'équipe qu'une personne a écrit (un courriel, aujourd'hui)."""

    async def alert(
        self, *, conversation_id: UUID, user_email: str | None, preview: str, kind: MessageKind
    ) -> None:
        """Envoie l'alerte. Ne lève pas : un courriel en échec ne bloque pas le message."""
        ...


class ConversationFeed(Protocol):
    """Port : réveiller les écrans ouverts sur un fil, dans tous les processus (ADR-0026)."""

    async def wake(self, conversation_id: UUID) -> None:
        """Annonce que le fil a bougé."""
        ...
