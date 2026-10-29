"""Amorçage des tests d'intégration : une vraie base, de vraies migrations.

Ce que ces fixtures garantissent
--------------------------------
Le schéma sous lequel les tests s'exécutent est celui produit par les
**migrations**, jamais celui que `Tortoise.generate_schemas()` déduirait
des modèles. La différence est décisive : les index partiels uniques et
les contraintes `CHECK` n'existent que dans la migration, parce que
Tortoise ne sait pas les déclarer. Tester sur un schéma généré validerait
un système dépourvu de ses garde-fous — c'est-à-dire pas celui qui part en
production.

Pourquoi la migration passe par `asyncio.run`
---------------------------------------------
Les tests s'exécutent chacun dans leur propre boucle d'événements
(`asyncio_default_fixture_loop_scope = "function"`). Un pool asyncpg ouvert
dans une boucle de session serait inutilisable depuis celles des tests. La
migration ouvre donc sa propre boucle, applique, et referme — les tests
ouvrent ensuite la leur.

Sont ignorés proprement, avec un motif lisible, les cas où PostgreSQL est
injoignable ou aucune migration n'a encore été générée. **En CI, ils
échouent** : voir `unavailable`.

Ce qui manque encore
--------------------
La fixture `clean_database` tronque les tables des modules ; aucun module
n'en déclare aujourd'hui, la liste est donc vide. Chaque module qui
ajoute des tables les inscrit ici, dans le commit qui les crée.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from tortoise import Tortoise, connections

from karria.bootstrap.modules import all_model_modules
from karria.shared.config import DatabaseSettings
from karria.shared.database import build_tortoise_config

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "models"

#: Base dédiée aux tests. Distincte de la base de développement : une suite
#: qui tronque des tables ne doit jamais pouvoir viser les données locales.
DEFAULT_TEST_URL = "postgres://karria:karria@localhost:5432/karria_test"

#: Les tables à vider entre deux tests. Chaque module y inscrit les siennes
#: dans le commit qui les crée.
TABLES_TO_TRUNCATE: Final[tuple[str, ...]] = (
    "identity_users",
    "identity_provider_identities",
    "identity_sessions",
    "identity_login_challenges",
    "identity_departure_feedback",
    "devices_installations",
    "devices_push_tickets",
    "cv_drafts",
    "cv_curricula",
    "cv_photos",
    "offers_offers",
    "offers_companies",
    "offers_index_outbox",
    "offers_target_demands",
    "offers_scrape_targets",
    "offers_ingestion_runs",
    "offers_geocode_cache",
    "offers_criteria",
    "offers_conversations",
    "offers_messages",
    "offers_feed_entries",
    "offers_offer_states",
    "documents_templates",
    "documents_template_versions",
    "documents_user_templates",
    "documents_selections",
    "documents_generated_cvs",
    "payments_accounts",
    "payments_purchases",
    "payments_webhook_events",
)


def unavailable(reason: str) -> None:
    """Ignore le test en local, mais **échoue** en intégration continue.

    Un test d'intégration ignoré est un test qui ne vérifie rien. En local
    c'est acceptable — tout le monde n'a pas Docker allumé en permanence.
    En CI, c'est un piège : la suite resterait verte alors qu'elle a cessé
    de vérifier la couche qui parle réellement à PostgreSQL et au stockage.

    GitHub Actions positionne `CI=true` ; la distinction est donc gratuite.
    """
    if os.environ.get("CI"):
        pytest.fail(f"Dépendance d'intégration indisponible en CI : {reason}")
    pytest.skip(reason)


_SKIP_NO_MIGRATION = (
    "Aucune migration générée. Lancer `make db-up`, puis "
    "`uv run aerich init -t karria.bootstrap.orm.TORTOISE_ORM` et `uv run aerich init-db`."
)


def _tortoise_config() -> dict[str, Any]:
    settings = DatabaseSettings(url=os.environ.get("DB__URL", DEFAULT_TEST_URL))
    return build_tortoise_config(settings, all_model_modules())


def _version_of(path: Path) -> int:
    """Numéro de version d'une migration Aerich, depuis son nom de fichier.

    Trié **numériquement**, jamais lexicographiquement : `sorted()` place
    sinon `10_` et `11_` avant `2_`, et une migration qui modifie une
    table s'exécute avant celle qui la crée.
    """
    return int(path.stem.split("_", 1)[0])


def _migration_modules() -> list[ModuleType]:
    """Charge les migrations Aerich, dans l'ordre de leur numéro de version."""
    modules: list[ModuleType] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py"), key=_version_of):
        spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
        if spec is None or spec.loader is None:  # pragma: no cover - défensif
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


async def _execute(sql: str) -> None:
    """Exécute un script SQL, instruction par instruction.

    Aerich sépare ses instructions par `;` en fin de ligne. Les exécuter
    une à une donne, en cas d'échec, un message qui désigne l'instruction
    fautive plutôt que le bloc entier.
    """
    connection = connections.get("default")
    for statement in filter(None, (part.strip() for part in sql.split(";\n"))):
        await connection.execute_script(statement)


async def _migrate(*, forward: bool) -> None:
    """Applique — ou annule — toutes les migrations, dans une boucle dédiée."""
    await Tortoise.init(config=_tortoise_config())
    try:
        migrations = _migration_modules()
        # À l'annulation, l'ordre s'inverse : une migration ne sait défaire
        # que ce qui a été fait après elle.
        ordered = migrations if forward else list(reversed(migrations))
        connection = connections.get("default")
        for migration in ordered:
            sql = await (
                migration.upgrade(connection) if forward else migration.downgrade(connection)
            )
            await _execute(sql)
    finally:
        await connections.close_all()


async def _reset_schema() -> None:
    """Vide le schéma public de la base de test.

    Sur la base de **test** uniquement : son nom est fixé par
    `DEFAULT_TEST_URL` ou `DB__URL`, et les tests d'intégration ne
    tournent nulle part ailleurs.
    """
    await Tortoise.init(config=_tortoise_config())
    try:
        connection = connections.get("default")
        await connection.execute_script("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await connections.close_all()


async def _reachable() -> None:
    """Vérifie que PostgreSQL répond, et referme aussitôt."""
    await Tortoise.init(config=_tortoise_config())
    try:
        await connections.get("default").execute_query("SELECT 1")
    finally:
        await connections.close_all()


@pytest.fixture(scope="session", autouse=True)
def migrated_schema() -> None:
    """Repart d'un schéma neuf et applique les migrations, une fois par session.

    Sans aucun module à modèles, il n'y a rien à migrer : la fixture ne fait
    rien, et les tests qui n'ont pas besoin de la base — le stockage objet
    sur MinIO — tournent quand même. Dès qu'un module déclare des modèles,
    l'absence de migration devient une anomalie.
    """
    if not all_model_modules():
        return
    if not MIGRATIONS_DIR.exists() or not list(MIGRATIONS_DIR.glob("*.py")):
        unavailable(_SKIP_NO_MIGRATION)

    try:
        asyncio.run(_reachable())
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        unavailable(f"PostgreSQL injoignable ({type(exc).__name__}). Lancer `make db-up`.")

    # Le schéma est **remis à neuf** avant d'appliquer les migrations,
    # plutôt qu'annulé après. Une annulation ne s'exécute pas quand une
    # session est interrompue — un `Ctrl-C`, un test qui plante le
    # processus — et la base reste alors à moitié migrée. Repartir d'un
    # schéma vide rend l'état initial identique à chaque fois.
    asyncio.run(_reset_schema())
    asyncio.run(_migrate(forward=True))


@pytest.fixture
async def clean_database() -> AsyncIterator[None]:
    """Connexion ouverte pour la durée du test, sur des tables vides.

    `TRUNCATE` plutôt que `DELETE` : les statistiques repartent de zéro, et
    un test qui dépendrait de l'ordre d'insertion échoue de façon
    reproductible plutôt qu'aléatoirement.
    """
    await Tortoise.init(config=_tortoise_config())
    try:
        if TABLES_TO_TRUNCATE:
            # `CASCADE` propage aux tables liées par clé étrangère ; les
            # nommer toutes reste plus lisible qu'un effet de bord implicite.
            await connections.get("default").execute_script(
                f"TRUNCATE TABLE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE"
            )
        yield
    finally:
        await connections.close_all()
