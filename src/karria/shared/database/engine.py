"""Cycle de vie de la connexion à la base."""

from __future__ import annotations

from typing import Any

from tortoise import Tortoise, connections

from karria.shared.errors import DependencyUnavailableError
from karria.shared.logging import get_logger

logger = get_logger(__name__)


async def connect(config: dict[str, Any]) -> None:
    """Ouvre le pool de connexions au démarrage de l'application.

    `_enable_global_fallback=True` n'est pas optionnel ici, malgré son
    préfixe. Depuis Tortoise 1.1, l'état de l'ORM vit dans un
    `TortoiseContext` porté par un `contextvar`, et ce drapeau vaut `False`
    par défaut. Sans lui, le contexte n'existe que dans la tâche qui a
    appelé `init` — celle du `lifespan` — et **chaque requête HTTP échoue**
    avec « No TortoiseContext is currently active », puisqu'une requête
    s'exécute dans sa propre tâche.

    La panne est d'autant plus traître qu'elle ne se voit pas au démarrage :
    l'application se lance normalement et n'échoue qu'au premier accès en
    base.
    """
    await Tortoise.init(config=config, _enable_global_fallback=True)
    logger.info("database.connected")


async def disconnect() -> None:
    """Ferme proprement le pool à l'arrêt.

    Sans fermeture explicite, un arrêt en douceur laisse des connexions
    ouvertes côté PostgreSQL jusqu'à leur expiration — et un redéploiement
    répété finit par épuiser `max_connections`.
    """
    await connections.close_all()
    logger.info("database.disconnected")


async def check_health() -> None:
    """Vérifie que la base répond réellement.

    Une sonde qui se contente de constater l'existence d'un pool renvoie
    « en bonne santé » alors que PostgreSQL est tombé. On exécute donc une
    requête, si triviale soit-elle.
    """
    try:
        await connections.get("default").execute_query("SELECT 1")
    except Exception as exc:
        raise DependencyUnavailableError("postgresql", str(exc)) from exc
