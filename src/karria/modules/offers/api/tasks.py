"""Les travaux du module `offers`, inscrits sur la file durable — l'autre porte d'entrée.

Une route HTTP et un travail de file sont deux adaptateurs **entrants** :
ils vivent dans la même couche, assemblent les mêmes cas d'usage avec les
mêmes fabriques (`api/dependencies.py`), et ne contiennent aucune règle.

Le calendrier
-------------
| Travail | Quand | Pourquoi ce rythme |
|---|---|---|
| `offers.ingest_batch` | à chaque lot remis par le collecteur | l'ingestion n'attend pas : le lot est déjà atterri |
| `offers.sync_index` | chaque minute, et dès qu'une collecte a inséré | l'outbox se vide en continu ; quatre minutes de travail au plus par passage |
| `offers.refresh_targets` | chaque heure | la demande évolue à l'échelle des conversations, pas des secondes |
| `offers.expire` | chaque nuit, 3 h 15 | une offre expire au jour près |
| `offers.reconcile` | chaque nuit, 3 h 45 | filet de sécurité : ce que l'outbox aurait manqué |
| `offers.update_memory` | après chaque tour de conversation | la mémoire de Karria se met à jour hors du chemin de la réponse (ADR-0018) |
| `offers.fetch_logos` | toutes les 5 min, et dès qu'un lot a montré une entreprise sans logo | deux minutes de travail au plus par passage, puis il se re-diffère : la file ne porte jamais un travail par entreprise |

Les travaux périodiques portent un **verrou d'exécution** (`lock`) : deux
synchronisations ne tournent jamais ensemble. La collecte elle-même n'est
pas ici : c'est un processus à part (`karria.collector`), qui remet ses
lots par `POST /internal/offers/batches`.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Final
from uuid import UUID

from karria.modules.offers.api.dependencies import (
    build_expire_offers,
    build_fetch_logos,
    build_ingest_batch,
    build_reconcile_index,
    build_refresh_targets,
    build_sync_index,
    build_update_memory,
)
from karria.modules.offers.application import LOGO_CONCURRENCY, UserNotifier
from karria.modules.offers.infrastructure import HttpLogoFetcher, ProcrastinateJobQueue
from karria.modules.offers.infrastructure.queue import (
    TASK_EXPIRE,
    TASK_FETCH_LOGOS,
    TASK_INGEST_BATCH,
    TASK_RECONCILE,
    TASK_REFRESH_TARGETS,
    TASK_SYNC_INDEX,
    TASK_UPDATE_MEMORY,
)
from karria.shared.config import get_settings
from karria.shared.logging import get_logger
from karria.shared.queue import App
from karria.shared.storage import build_object_storage

logger = get_logger(__name__)

#: Durée de travail maximale d'un passage de synchronisation : au-delà, il
#: se re-diffère, pour laisser respirer la file et le serveur d'inférence.
SYNC_BUDGET_SECONDS: Final = 240.0
#: Durée de travail maximale d'un passage de logos, pour la même raison : un
#: worker n'a que trois places, l'ingestion ne doit pas attendre.
LOGO_BUDGET_SECONDS: Final = 120.0


def register_tasks(app: App, *, notifier: Callable[[], UserNotifier] | None) -> None:
    """Inscrit les travaux d'`offers`. `notifier` vient de `bootstrap` (règle 4)."""

    @app.task(name=TASK_INGEST_BATCH, retry=3)
    async def ingest_batch(key: str, target_key: str | None = None) -> None:
        settings = get_settings()
        async with AsyncExitStack() as resources:
            storage = await build_object_storage(settings, resources)
            await build_ingest_batch(settings, storage, app).execute(key, target_key=target_key)

    @app.task(name=TASK_UPDATE_MEMORY, retry=1)
    async def update_memory(user_id: str, user_message: str, reply: str) -> None:
        await build_update_memory(get_settings()).execute(
            UUID(user_id), user_message=user_message, reply=reply
        )

    @app.periodic(cron="* * * * *", periodic_id=TASK_SYNC_INDEX)
    @app.task(name=TASK_SYNC_INDEX, lock=TASK_SYNC_INDEX, queueing_lock=TASK_SYNC_INDEX)
    async def sync_index(timestamp: int | None = None) -> None:  # noqa: ARG001 - périodique ou différé
        await run_sync(app, notifier)

    # Pas de `retry` : le passage se re-diffère lui-même sous le même verrou de
    # file, et une reprise automatique du travail en échec remettrait un second
    # `todo` sous ce verrou — `UniqueViolation (queueing_lock)`, le travail
    # restait « en cours » jusqu'à `retry_stalled` (vu le 2026-10-27). Le
    # périodique de cinq minutes est la reprise.
    @app.periodic(cron="*/5 * * * *", periodic_id=TASK_FETCH_LOGOS)
    @app.task(name=TASK_FETCH_LOGOS, lock=TASK_FETCH_LOGOS, queueing_lock=TASK_FETCH_LOGOS)
    async def fetch_logos(timestamp: int | None = None) -> None:  # noqa: ARG001 - périodique ou différé
        await run_logos(app)

    @app.periodic(cron="7 * * * *", periodic_id=TASK_REFRESH_TARGETS)
    @app.task(name=TASK_REFRESH_TARGETS, lock=TASK_REFRESH_TARGETS)
    async def refresh_targets(timestamp: int) -> None:  # noqa: ARG001 - imposé par les travaux périodiques
        await build_refresh_targets().execute()

    @app.periodic(cron="15 3 * * *", periodic_id=TASK_EXPIRE)
    @app.task(name=TASK_EXPIRE, lock=TASK_EXPIRE)
    async def expire(timestamp: int) -> None:  # noqa: ARG001 - imposé par les travaux périodiques
        await build_expire_offers().execute()
        await ProcrastinateJobQueue(app).defer_index_sync()

    @app.periodic(cron="45 3 * * *", periodic_id=TASK_RECONCILE)
    @app.task(name=TASK_RECONCILE, lock=TASK_RECONCILE)
    async def reconcile(timestamp: int) -> None:  # noqa: ARG001 - imposé par les travaux périodiques
        await build_reconcile_index(get_settings()).execute()
        await ProcrastinateJobQueue(app).defer_index_sync()


async def run_sync(app: App, notifier: Callable[[], UserNotifier] | None) -> int:
    """Vide l'outbox par lots, dans la limite du budget ; re-diffère s'il en reste."""
    settings = get_settings()
    sync = build_sync_index(settings, notifier() if notifier is not None else None)
    started = time.monotonic()
    total = 0
    while True:
        report = await sync.execute()
        total += report.claimed
        if report.claimed == 0:
            return total
        if time.monotonic() - started > SYNC_BUDGET_SECONDS:
            logger.info("offers.sync_budget_reached", claimed=total)
            await ProcrastinateJobQueue(app).defer_index_sync()
            return total


async def run_logos(app: App, *, budget: float = LOGO_BUDGET_SECONDS) -> int:
    """Le travail de file : des passages dans la limite du budget ; re-diffère s'il en reste."""
    return await fetch_logos_within(
        budget=budget, on_remaining=ProcrastinateJobQueue(app).defer_logo_fetch
    )


async def fetch_logos_within(
    *,
    budget: float,
    concurrency: int = LOGO_CONCURRENCY,
    on_remaining: Callable[[], Awaitable[None]] | None = None,
) -> int:
    """Des passages de logos jusqu'à épuisement ou au budget ; rend le nombre d'entreprises vues.

    Partagé par le worker et par `scripts.companies fetch-logos` (le rattrapage
    initial, plus large) : les deux prennent leurs entreprises sous bail, sans
    jamais la même (`claim_logo_work`).
    """
    settings = get_settings()
    started = time.monotonic()
    total = 0
    async with AsyncExitStack() as resources:
        storage = await build_object_storage(settings, resources)
        client = await resources.enter_async_context(HttpLogoFetcher.build_client())
        fetch = build_fetch_logos(storage, HttpLogoFetcher(client))
        while True:
            report = await fetch.execute(concurrency=concurrency)
            total += report.claimed
            if report.claimed == 0:
                return total
            if time.monotonic() - started > budget:
                if on_remaining is not None:
                    await on_remaining()
                return total
