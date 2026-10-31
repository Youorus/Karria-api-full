"""La boucle du flux WebSocket d'un fil — voir l'en-tête de `routers.py`.

Séparée de la route pour tenir en un objet lisible : un curseur, une
livraison, une écoute, et la boucle qui les relie au bus de réveils.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from karria.modules.support.api.schemas import MessageRead, StreamEvent
from karria.modules.support.application import MarkReadByUser, MessageRepository
from karria.modules.support.infrastructure import topic_of
from karria.shared.logging import get_logger
from karria.shared.realtime import Broker
from karria.shared.storage import ObjectStorage

logger = get_logger(__name__)

#: Le délai d'attente du flux : au-delà, on relit la base et on dit « ping ».
STREAM_IDLE_SECONDS: Final = 20.0


class ConversationStream:
    """Un flux ouvert sur un fil, pour une personne."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        conversation_id: UUID,
        user_id: UUID,
        cursor: datetime,
        messages: MessageRepository,
        mark_read: MarkReadByUser,
        storage: ObjectStorage,
        broker: Broker,
    ) -> None:
        self._ws = websocket
        self._conversation_id = conversation_id
        self._user_id = user_id
        self._cursor = cursor
        self._messages = messages
        self._mark_read = mark_read
        self._storage = storage
        self._broker = broker

    @staticmethod
    async def initial_cursor(
        messages: MessageRepository, conversation_id: UUID, since: datetime | None
    ) -> datetime:
        """Ce que l'app dit avoir vu, sinon le dernier message en base.

        Pas l'horloge murale : un message écrit « à l'instant » par un autre
        processus, ou une horloge décalée, ne doit pas passer entre les mailles.
        """
        if since is not None:
            return since.astimezone(UTC)
        last = await messages.list_recent(conversation_id, limit=1)
        return last[0].created_at if last else datetime.min.replace(tzinfo=UTC)

    async def run(self) -> None:
        """Livre, puis attend les réveils jusqu'à la déconnexion."""
        listener = asyncio.create_task(self._listen())
        try:
            await self._deliver()
            async with self._broker.subscribe(topic_of(self._conversation_id)) as subscription:
                while not listener.done():
                    waiter = asyncio.create_task(subscription.wait(STREAM_IDLE_SECONDS))
                    done, _ = await asyncio.wait(
                        {waiter, listener}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if listener in done:
                        waiter.cancel()
                        break
                    await self._deliver()
                    if not waiter.result():
                        await self._ws.send_text(StreamEvent(type="ping").model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            listener.cancel()
            if self._ws.client_state is WebSocketState.CONNECTED:
                await self._ws.close()
            logger.info("support.stream_closed", conversation_id=str(self._conversation_id))

    async def _deliver(self) -> None:
        """Envoie ce qui est arrivé depuis le curseur, et l'avance."""
        for message in await self._messages.list_after(self._conversation_id, after=self._cursor):
            event = StreamEvent(
                type="message", message=await MessageRead.present(message, self._storage)
            )
            await self._ws.send_text(event.model_dump_json())
            self._cursor = max(self._cursor, message.created_at)

    async def _listen(self) -> None:
        """Lit ce que l'app envoie ; une déconnexion termine la boucle."""
        while True:
            raw = await self._ws.receive_text()
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("type") == "read":
                await self._mark_read.execute(self._user_id)
