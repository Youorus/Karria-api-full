"""Le bus de réveils sur PostgreSQL : `NOTIFY` pour publier, `LISTEN` pour attendre.

Comment ça marche
-----------------
- **Publier** : `SELECT pg_notify('karria_wakeups', <sujet>)` sur la
  connexion Tortoise courante. Émis dans une transaction, le réveil ne part
  qu'à la validation : un abonné réveillé trouve toujours la ligne en base.
- **Écouter** : une connexion asyncpg **dédiée**, ouverte à la première
  souscription et gardée pour la durée du processus (une connexion `LISTEN`
  ne peut pas retourner dans un pool). Elle reçoit tous les sujets du canal
  et réveille les abonnements du processus qui portent ce sujet.
- **Panne de l'écoute** : la connexion est rouverte à la souscription
  suivante ; entre-temps, les abonnés relisent au délai (`wait` rend
  `False`). Rien n'est perdu, tout est en retard de quelques secondes.

Pourquoi un seul canal
----------------------
`LISTEN` se fait par canal, et un canal par conversation obligerait à
`LISTEN`/`UNLISTEN` à chaque ouverture d'écran. Un canal unique et le sujet
dans la charge (≤ 8 000 octets, nous en utilisons une centaine) suffisent :
le filtrage se fait en mémoire, là où c'est gratuit.

`asyncpg` est le pilote que Tortoise utilise déjà : aucune dépendance de plus.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import asyncpg
from tortoise import connections

from karria.shared.database import DEFAULT_CONNECTION
from karria.shared.logging import get_logger
from karria.shared.realtime.memory import _EventSubscription

logger = get_logger(__name__)

#: Le canal unique. Le sujet voyage dans la charge.
CHANNEL: Final = "karria_wakeups"


class PostgresBroker:
    """Implémentation du port `Broker` sur `LISTEN`/`NOTIFY`."""

    def __init__(self, dsn: str, *, connection_name: str = DEFAULT_CONNECTION) -> None:
        self._dsn = dsn
        self._connection_name = connection_name
        self._subscriptions: dict[str, set[_EventSubscription]] = defaultdict(set)
        self._listener: Any | None = None
        self._lock = asyncio.Lock()

    async def publish(self, topic: str) -> None:
        """Émet le réveil par la connexion de l'application (donc dans sa transaction)."""
        await connections.get(self._connection_name).execute_query(
            "SELECT pg_notify($1, $2)", [CHANNEL, topic]
        )

    @asynccontextmanager
    async def subscribe(self, topic: str) -> AsyncIterator[_EventSubscription]:
        """Abonne au sujet, en s'assurant qu'une écoute est ouverte."""
        await self._ensure_listening()
        subscription = _EventSubscription()
        self._subscriptions[topic].add(subscription)
        try:
            yield subscription
        finally:
            self._subscriptions[topic].discard(subscription)
            if not self._subscriptions[topic]:
                del self._subscriptions[topic]

    async def close(self) -> None:
        """Ferme la connexion d'écoute, s'il y en a une."""
        listener, self._listener = self._listener, None
        if listener is not None and not listener.is_closed():
            await listener.close()
        self._subscriptions.clear()

    # --- écoute -----------------------------------------------------

    async def _ensure_listening(self) -> None:
        """Ouvre la connexion `LISTEN` si elle manque ou si elle est tombée.

        Une panne ici n'empêche pas de s'abonner : l'abonné relira au délai.
        On journalise, et on réessaiera à la souscription suivante.
        """
        async with self._lock:
            if self._listener is not None and not self._listener.is_closed():
                return
            try:
                listener = await asyncpg.connect(self._dsn)
                await listener.add_listener(CHANNEL, self._on_notification)
                listener.add_termination_listener(self._on_termination)
            except (OSError, asyncpg.PostgresError) as exc:
                logger.warning("realtime.listen_failed", error=type(exc).__name__)
                return
            self._listener = listener
            logger.info("realtime.listening", channel=CHANNEL)

    def _on_notification(self, connection: Any, pid: int, channel: str, payload: str) -> None:
        """Réveille les abonnements du sujet reçu (appelé par asyncpg, sans `await`)."""
        _ = (connection, pid, channel)
        for subscription in list(self._subscriptions.get(payload, ())):
            subscription.wake()

    def _on_termination(self, connection: Any) -> None:
        """La connexion d'écoute est tombée : la prochaine souscription la rouvrira."""
        if connection is self._listener:
            self._listener = None
        logger.warning("realtime.listener_terminated")
