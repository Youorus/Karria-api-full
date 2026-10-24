"""La file durable vue de `documents` : implémente `TemplateJobs` sur procrastinate.

Chaque différé porte un verrou de file (`queueing_lock`) : une version ne
s'analyse pas deux fois en même temps, une explication ne se rédige qu'une fois.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from karria.shared.logging import get_logger
from karria.shared.queue import AlreadyEnqueued, App

logger = get_logger(__name__)

TASK_INGEST_TEMPLATE: Final = "documents.ingest_template"
TASK_EXPLAIN_ATS: Final = "documents.explain_ats"


class ProcrastinateTemplateJobs:
    """Implémente `TemplateJobs`."""

    def __init__(self, app: App) -> None:
        self._app = app

    async def defer_ingestion(self, version_id: UUID) -> None:
        """Analyse d'une version, une seule à la fois par version."""
        await self._defer(
            TASK_INGEST_TEMPLATE, f"{TASK_INGEST_TEMPLATE}:{version_id}", version_id=str(version_id)
        )

    async def defer_explanation(self, generated_id: UUID) -> None:
        """Explication du score d'un export."""
        await self._defer(
            TASK_EXPLAIN_ATS, f"{TASK_EXPLAIN_ATS}:{generated_id}", generated_id=str(generated_id)
        )

    async def _defer(self, task: str, lock: str, **kwargs: str) -> None:
        try:
            await self._app.configure_task(task, queueing_lock=lock).defer_async(**kwargs)
        except AlreadyEnqueued:
            logger.debug("documents.job_already_queued", task=task)
