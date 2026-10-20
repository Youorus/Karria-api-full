"""Cycle de vie de l'application.

Contrat
-------
- **Aucune migration automatique au démarrage.** Migrer le schéma est un
  acte de déploiement délibéré ; le faire au boot signifie que N instances
  qui redémarrent ensemble migrent en concurrence.
- **Aucune tâche métier lourde au démarrage.** La V1 a payé cher un
  ré-indexage déclenché au boot : chaque redémarrage de conteneur relançait
  un ré-embedding complet du corpus d'offres, pour plusieurs euros d'API
  et, un jour, un crédit épuisé qui a cassé l'inscription des nouveaux
  utilisateurs. Ce qui est coûteux appartient à une commande explicite ou
  à une tâche planifiée, jamais au chemin de démarrage.
- **Démarrage bruyant, arrêt propre.** Si une dépendance critique manque,
  le processus doit échouer immédiatement et visiblement.

Ce que ce fichier accueillera
-----------------------------
Les **ponts entre modules** qui s'abonnent au bus : un module ne connaît
jamais les événements d'un autre (règle 4), et cette couche est la seule à
voir les deux côtés. Chaque pont arrive avec le module qui le justifie,
sous la forme d'un `subscribe_*` dans `wiring.py`, appelé ici.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from karria.bootstrap.modules import ENABLED_MODULES, register_event_subscriptions
from karria.bootstrap.orm import TORTOISE_ORM
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.events import EventBus
from karria.shared.logging import get_logger
from karria.shared.storage import build_object_storage

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ouvre les ressources au démarrage et les referme à l'arrêt."""
    settings = get_settings()

    bus = EventBus()
    register_event_subscriptions(bus)

    # Attaché à l'application plutôt qu'à une variable de module : chaque
    # instance de test dispose de son propre bus, sans état résiduel.
    app.state.event_bus = bus

    await connect(TORTOISE_ORM)

    # Le client de stockage est un gestionnaire de contexte asynchrone : il
    # doit rester ouvert pour toute la durée de vie du processus. En ouvrir
    # un par requête rétablirait une poignée de main TLS à chaque fichier.
    async with AsyncExitStack() as resources:
        storage = await build_object_storage(settings, resources)
        app.state.object_storage = storage

        # Démarrage bruyant : un bucket manquant doit empêcher le conteneur
        # de booter, pas produire un 500 au premier envoi d'un utilisateur.
        # Une fois démarré, le stockage redevient une dépendance NON
        # critique — l'API sert encore beaucoup de choses sans lui.
        await storage.check_health()

        logger.info(
            "application.started",
            environment=settings.app.environment.value,
            version=settings.app.version,
            modules=[module.name for module in ENABLED_MODULES],
            subscriptions=bus.subscriptions(),
            storage_provider=settings.storage.provider.value,
        )

        try:
            yield
        finally:
            await disconnect()
            logger.info("application.stopped")
