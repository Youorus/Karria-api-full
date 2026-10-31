"""Choix du bus de réveils d'après la configuration.

Le seul endroit qui décide : une base PostgreSQL configurée → `LISTEN`/
`NOTIFY` ; sinon (les tests, un outil sans base) → la mémoire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from karria.shared.realtime.memory import InMemoryBroker
from karria.shared.realtime.ports import Broker
from karria.shared.realtime.postgres import PostgresBroker

if TYPE_CHECKING:
    from karria.shared.config import DatabaseSettings


def build_broker(settings: DatabaseSettings, *, in_memory: bool = False) -> Broker:
    """Construit le bus : PostgreSQL par défaut, la mémoire si on le demande."""
    if in_memory:
        return InMemoryBroker()
    return PostgresBroker(settings.url)
