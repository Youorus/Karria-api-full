"""La file durable vue d'`applications` : implémente `DossierJobs` sur procrastinate.

Un seul travail, `applications.adapt_cv`, avec un verrou de file par
dossier : une adaptation qui dépasse le temps de la requête ne part qu'une
fois, même si l'app redemande.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from karria.shared.logging import get_logger
from karria.shared.queue import AlreadyEnqueued, App

logger = get_logger(__name__)

TASK_ADAPT_CV: Final = "applications.adapt_cv"


class ProcrastinateDossierJobs:
    """Implémente `DossierJobs`."""

    def __init__(self, app: App) -> None:
        self._app = app

    async def defer_adaptation(self, dossier_id: UUID) -> None:
        """Une adaptation par dossier à la fois."""
        try:
            await self._app.configure_task(
                TASK_ADAPT_CV, queueing_lock=f"{TASK_ADAPT_CV}:{dossier_id}"
            ).defer_async(dossier_id=str(dossier_id))
        except AlreadyEnqueued:
            logger.debug("applications.job_already_queued", dossier_id=str(dossier_id))
