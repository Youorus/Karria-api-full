"""Le bus de réveils en mémoire : un seul processus, aucune I/O.

Celui des tests, et de tout processus sans base. Il applique exactement la
même sémantique que `PostgresBroker` — plusieurs réveils fondus en un, délai
rendu `False` — pour qu'un test décrive le même système que la production.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class _EventSubscription:
    """Un abonnement adossé à un `asyncio.Event` (fondu : plusieurs réveils = un)."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def wake(self) -> None:
        self._event.set()

    async def wait(self, at_most: float) -> bool:
        """Voir `Subscription.wait`."""
        try:
            await asyncio.wait_for(self._event.wait(), at_most)
        except TimeoutError:
            return False
        self._event.clear()
        return True


class InMemoryBroker:
    """Implémentation du port `Broker` sans réseau."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, set[_EventSubscription]] = defaultdict(set)
        self.published: list[str] = []

    async def publish(self, topic: str) -> None:
        """Réveille les abonnés de ce sujet, dans ce processus."""
        self.published.append(topic)
        for subscription in list(self._subscriptions.get(topic, ())):
            subscription.wake()

    @asynccontextmanager
    async def subscribe(self, topic: str) -> AsyncIterator[_EventSubscription]:
        """Abonne, puis désabonne à la sortie du bloc."""
        subscription = _EventSubscription()
        self._subscriptions[topic].add(subscription)
        try:
            yield subscription
        finally:
            self._subscriptions[topic].discard(subscription)
            if not self._subscriptions[topic]:
                del self._subscriptions[topic]

    def subscribers(self, topic: str) -> int:
        """Combien d'abonnements sont ouverts sur ce sujet (diagnostic, tests)."""
        return len(self._subscriptions.get(topic, ()))

    async def close(self) -> None:
        """Rien à libérer."""
        self._subscriptions.clear()
