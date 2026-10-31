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

Les ponts entre modules
-----------------------
Un module ne connaît jamais les événements d'un autre (règle 4), et cette
couche est la seule à voir les deux côtés. Les abonnements croisés vivent
dans `wiring.subscribe_bridges`, appelé ici **une fois le stockage ouvert** :
un abonné qui efface des objets a besoin du client ouvert pour la durée du
processus.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from karria.bootstrap.modules import ENABLED_MODULES, register_event_subscriptions
from karria.bootstrap.orm import TORTOISE_ORM
from karria.bootstrap.wiring import subscribe_bridges
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.events import EventBus
from karria.shared.logging import get_logger
from karria.shared.queue import build_queue_app
from karria.shared.realtime import build_broker
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

        # La file de travaux : l'API diffère (la suite d'un fil, une
        # resynchronisation), le worker exécute. Ouverte pour la durée du
        # processus, comme le stockage.
        queue_app = build_queue_app(settings.db)
        await resources.enter_async_context(queue_app.open_async())
        app.state.queue_app = queue_app

        # Le bus de réveils temps réel (ADR-0026) : `NOTIFY` par la connexion
        # de l'application, `LISTEN` sur une connexion dédiée ouverte à la
        # première écoute, jamais au démarrage.
        broker = build_broker(settings.db)
        app.state.broker = broker

        # Les réactions croisées (un compte supprimé → `devices` et `cv`
        # oublient la personne) : ici et pas plus haut, parce que l'oubli du
        # CV efface des objets et a besoin du stockage ouvert.
        subscribe_bridges(bus, storage=storage)

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
            await broker.close()
            await disconnect()
            logger.info("application.stopped")
