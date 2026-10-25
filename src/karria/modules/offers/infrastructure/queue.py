"""La file durable vue d'`offers` : implémente `JobQueue` sur procrastinate.

Les noms de travaux sont ici et nulle part ailleurs ; `api/tasks.py` les
inscrit, cet adaptateur les diffère. Chaque différé porte un **verrou de
file** (`queueing_lock`) : un deuxième « resynchronise l'index » pendant
qu'un premier attend n'en crée pas un second — exactement ce qui manquait
à la V1 (80 000 travaux ARQ empilés, la même offre en file 670 à 3 921
fois).
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from karria.shared.logging import get_logger
from karria.shared.queue import AlreadyEnqueued, App

logger = get_logger(__name__)

TASK_INGEST_BATCH: Final = "offers.ingest_batch"
TASK_SYNC_INDEX: Final = "offers.sync_index"
TASK_REFRESH_TARGETS: Final = "offers.refresh_targets"
TASK_EXPIRE: Final = "offers.expire"
TASK_RECONCILE: Final = "offers.reconcile"
TASK_UPDATE_MEMORY: Final = "offers.update_memory"
TASK_FETCH_LOGOS: Final = "offers.fetch_logos"


class ProcrastinateJobQueue:
    """Implémente `JobQueue`."""

    def __init__(self, app: App) -> None:
        self._app = app

    async def defer_index_sync(self) -> None:
        """Un seul en attente à la fois."""
        await self._defer(TASK_SYNC_INDEX, TASK_SYNC_INDEX)

    async def defer_ingest_batch(self, key: str, target_key: str | None) -> None:
        """Un travail par lot, dédoublonné par sa clé."""
        kwargs = {"key": key, **({"target_key": target_key} if target_key else {})}
        await self._defer(TASK_INGEST_BATCH, f"{TASK_INGEST_BATCH}:{key}", **kwargs)

    async def defer_memory_update(self, user_id: UUID, *, user_message: str, reply: str) -> None:
        """Un travail par tour ; pas de verrou : deux tours successifs se fusionnent par la règle."""
        await self._app.configure_task(TASK_UPDATE_MEMORY).defer_async(
            user_id=str(user_id), user_message=user_message, reply=reply
        )

    async def defer_logo_fetch(self) -> None:
        """Un seul passage en attente à la fois ; il se re-diffère tant qu'il reste du travail."""
        await self._defer(TASK_FETCH_LOGOS, TASK_FETCH_LOGOS)

    async def _defer(self, task: str, lock: str, **kwargs: str) -> None:
        try:
            await self._app.configure_task(task, queueing_lock=lock).defer_async(**kwargs)
        except AlreadyEnqueued:
            logger.debug("offers.job_already_queued", task=task)
