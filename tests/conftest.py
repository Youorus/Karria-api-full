"""Fixtures partagées par toute la suite de tests.

Principe directeur
------------------
Chaque test reçoit sa **propre** application, construite par la fabrique
avec sa propre configuration. Aucun état global, donc aucun ordre
d'exécution implicite entre tests — la cause la plus fréquente des suites
qui « passent en local mais échouent en CI ».

Les doublures elles-mêmes vivent dans `tests/doubles.py`, pour rester
importables depuis n'importe quel test et pas seulement injectables.
Chaque module ajoute les siennes et ses surcharges de dépendances ici, dans
le commit qui le crée.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from doubles import CollectingBus
from karria.bootstrap.app import create_app
from karria.shared.auth import Principal, resolve_principal
from karria.shared.config import (
    AppSettings,
    Environment,
    ObservabilitySettings,
    Settings,
    StorageSettings,
)
from karria.shared.storage import InMemoryObjectStorage


@pytest.fixture(scope="session", autouse=True)
def _isolate_from_dotenv() -> None:
    """Coupe la suite du `.env` du poste de développement.

    Sans cela, les tests héritent de la configuration locale : un
    développeur qui bascule son `STORAGE__PROVIDER` fait échouer des tests
    qui n'ont rien à voir, et la CI — qui n'a pas de `.env` — ne se comporte
    pas comme le poste. C'est la promesse que fait déjà la fixture
    `settings` ; encore faut-il la tenir pour **toutes** les constructions
    de `Settings`, y compris celles faites à la main dans un test.
    """
    Settings.model_config["env_file"] = None


@pytest.fixture
def settings() -> Settings:
    """Configuration de test, indépendante du `.env` du développeur."""
    return Settings(
        app=AppSettings(environment=Environment.TEST, debug=True),
        observability=ObservabilitySettings(log_level="WARNING", log_format="console"),
    )


@pytest.fixture
def events() -> CollectingBus:
    """Bus d'événements neuf pour chaque test, qui garde ce qu'il a vu."""
    return CollectingBus()


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    """Stockage objet en mémoire, neuf pour chaque test.

    Il applique **la même politique** que l'adaptateur S3 : un test qui
    écrit un type refusé échoue ici comme il échouerait en production.
    """
    return InMemoryObjectStorage(StorageSettings(), Environment.TEST.value)


@pytest.fixture
def app(settings: Settings, events: CollectingBus, storage: InMemoryObjectStorage) -> FastAPI:
    """Application neuve, sans `lifespan`.

    Le `lifespan` n'est volontairement pas exécuté : la grande majorité des
    tests n'a pas besoin d'une connexion à la base, et l'exiger rendrait la
    suite lente et fragile. Les tests qui en ont besoin sont marqués
    `integration` et montent explicitement leurs ressources.

    Seuls les **adaptateurs** sont substitués. Les use cases, les routes,
    l'autorisation, la validation et le format d'erreur sont exercés pour de
    vrai — c'est tout l'intérêt de la couture d'`api/dependencies.py`.
    """
    application = create_app(settings)
    # Le bus et le stockage sont normalement posés par le `lifespan`, qui
    # ne tourne pas ici.
    application.state.event_bus = events
    application.state.object_storage = storage
    return application


@pytest.fixture
def authenticate(app: FastAPI) -> Iterator[Callable[..., Principal]]:
    """Installe un appelant authentifié pour la durée du test.

    Substitue le seul point de résolution du `Principal` (ADR-0007). Les
    routes exercent ensuite leurs contrôles d'autorisation réels : un test
    qui oublie une permission obtient un `403`, exactement comme en
    production.
    """

    def _authenticate(subject_id: UUID | None = None, *permissions: str) -> Principal:
        principal = Principal(subject_id=subject_id or uuid4(), permissions=frozenset(permissions))
        app.dependency_overrides[resolve_principal] = lambda: principal
        return principal

    yield _authenticate
    app.dependency_overrides.pop(resolve_principal, None)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP parlant directement à l'application ASGI, sans socket."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
