"""Dépendances FastAPI transverses.

Ces alias sont la manière **unique** d'obtenir la configuration, le bus
d'événements, le stockage ou une unité de travail dans une route. La V1
redéclarait dans chaque router un `def get_x_service(): return XService()`
sans valeur ajoutée ; on centralise ici ce qui est réellement transverse,
et chaque module ne déclare que ses propres dépendances métier.

Ces objets sont exposés en dépendances plutôt qu'importés en tant que
variables globales pour une raison précise : un test peut les remplacer via
`app.dependency_overrides` sans monkeypatch, donc sans fuite d'un test à
l'autre.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from karria.shared.config import Settings, get_settings
from karria.shared.database import TortoiseUnitOfWork, UnitOfWork
from karria.shared.events import EventBus
from karria.shared.llm import LanguageModel, build_language_model
from karria.shared.queue import App as QueueApp
from karria.shared.realtime import Broker
from karria.shared.storage import ObjectStorage


def get_event_bus(request: Request) -> EventBus:
    """Retourne le bus d'événements attaché à l'application."""
    return cast(EventBus, request.app.state.event_bus)


def get_object_storage(request: Request) -> ObjectStorage:
    """Retourne le stockage objet attaché à l'application.

    Lu depuis l'état de l'application et non depuis une variable de module :
    un test le remplace par `dependency_overrides`, sans monkeypatch et sans
    fuite d'un test à l'autre. La V1 conservait son client S3 dans deux
    attributs de classe pour le même objet — un état global impossible à
    substituer proprement.
    """
    return cast(ObjectStorage, request.app.state.object_storage)


def get_queue_app(request: Request) -> QueueApp:
    """Retourne la file de travaux ouverte par le `lifespan` (pour différer, pas exécuter)."""
    return cast(QueueApp, request.app.state.queue_app)


def get_broker(request: Request) -> Broker:
    """Retourne le bus de réveils temps réel ouvert par le `lifespan` (ADR-0026)."""
    return cast(Broker, request.app.state.broker)


def get_language_model(settings: Annotated[Settings, Depends(get_settings)]) -> LanguageModel:
    """Le routeur de modèles de langage, assemblé depuis la configuration (ADR-0018).

    Construit à chaque requête : les adaptateurs sont des clients sortants
    immuables, sans connexion ouverte ; un test le remplace par
    `dependency_overrides` avec une doublure.
    """
    return build_language_model(settings)


def get_unit_of_work() -> UnitOfWork:
    """Retourne une unité de travail adossée à Tortoise."""
    return TortoiseUnitOfWork()


SettingsDep = Annotated[Settings, Depends(get_settings)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
UnitOfWorkDep = Annotated[UnitOfWork, Depends(get_unit_of_work)]
ObjectStorageDep = Annotated[ObjectStorage, Depends(get_object_storage)]
QueueAppDep = Annotated[QueueApp, Depends(get_queue_app)]
BrokerDep = Annotated[Broker, Depends(get_broker)]
LanguageModelDep = Annotated[LanguageModel, Depends(get_language_model)]
