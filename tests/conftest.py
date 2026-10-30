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

from doubles import CollectingBus, DevicesEnv, IdentityEnv
from karria.bootstrap.app import create_app
from karria.modules.devices.api import dependencies as devices_deps
from karria.modules.identity.api import dependencies as identity_deps
from karria.shared.auth import AiConsentGate, Principal, get_ai_consent_gate, resolve_principal
from karria.shared.config import (
    AppSettings,
    Environment,
    ObservabilitySettings,
    Settings,
    StorageSettings,
)
from karria.shared.database import NullUnitOfWork
from karria.shared.http.dependencies import get_unit_of_work
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


class _OpenAiConsentGate:
    """Une porte d'IA qui laisse passer : la personne a consenti (ADR-0025)."""

    async def ensure(self, principal: Principal) -> None:
        _ = principal


@pytest.fixture
def ai_consented(app: FastAPI) -> Iterator[None]:
    """Tient le consentement à l'IA pour acquis.

    Les tests de bout en bout du CV, de Karria et des exports exercent ce
    qui se passe **après** la porte ; la porte elle-même a ses tests
    (`tests/unit/test_ai_consent_gate.py`).
    """

    def gate() -> AiConsentGate:
        return _OpenAiConsentGate()

    app.dependency_overrides[get_ai_consent_gate] = gate
    yield
    app.dependency_overrides.pop(get_ai_consent_gate, None)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Client HTTP parlant directement à l'application ASGI, sans socket."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
def identity_env(app: FastAPI) -> Iterator[IdentityEnv]:
    """Installe les doublures d'`identity` sur l'application.

    Les routes, les use cases, la validation **et** la couture
    `resolve_principal` branchée par `bootstrap/wiring.py` sont exercés
    pour de vrai : seuls les dépôts, l'envoi d'e-mail, le limiteur et
    l'horloge sont remplacés.
    """
    env = IdentityEnv.build()
    overrides = {
        identity_deps.get_user_repository: lambda: env.users,
        identity_deps.get_identity_repository: lambda: env.identities,
        identity_deps.get_session_repository: lambda: env.sessions,
        identity_deps.get_challenge_repository: lambda: env.challenges,
        identity_deps.get_departure_repository: lambda: env.departures,
        identity_deps.get_ai_consent_repository: lambda: env.ai_consents,
        identity_deps.get_ai_provider_catalog: lambda: env.ai_catalog,
        identity_deps.get_main_device_lookup: lambda: env.main_device,
        identity_deps.get_login_code_sender: lambda: env.sender,
        identity_deps.get_login_throttle: lambda: env.throttle,
        identity_deps.get_token_factory: lambda: env.tokens,
        identity_deps.get_clock: lambda: env.clock,
        # Aucune base : l'atomicité relève des tests d'intégration.
        get_unit_of_work: NullUnitOfWork,
    }
    app.dependency_overrides.update(overrides)
    yield env
    for key in overrides:
        app.dependency_overrides.pop(key, None)


@pytest.fixture
def devices_env(app: FastAPI) -> Iterator[DevicesEnv]:
    """Installe les doublures de `devices` sur l'application."""
    env = DevicesEnv.build()
    overrides = {
        devices_deps.get_device_repository: lambda: env.devices,
        devices_deps.get_ticket_repository: lambda: env.tickets,
        devices_deps.get_push_gateway: lambda: env.gateway,
        devices_deps.get_clock: lambda: env.clock,
        get_unit_of_work: NullUnitOfWork,
    }
    app.dependency_overrides.update(overrides)
    yield env
    for key in overrides:
        app.dependency_overrides.pop(key, None)
