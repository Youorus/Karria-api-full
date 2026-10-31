"""Le réveil des écrans ouverts, par le bus de réveils du socle (ADR-0026)."""

from __future__ import annotations

from uuid import UUID

from karria.shared.realtime import Broker


def topic_of(conversation_id: UUID) -> str:
    """Le sujet d'un fil sur le bus : `support:<id>`."""
    return f"support:{conversation_id}"


class BrokerConversationFeed:
    """Implémente le port `ConversationFeed` avec le `Broker` du socle."""

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    async def wake(self, conversation_id: UUID) -> None:
        """Réveille tous les abonnés du fil, dans tous les processus."""
        await self._broker.publish(topic_of(conversation_id))
