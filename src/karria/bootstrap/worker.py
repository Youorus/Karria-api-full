"""Le worker : exécute les travaux de la file durable (procrastinate, sur PostgreSQL).

    uv run python -m scripts.worker

Contrat
-------
- Un processus à part de l'API, qui partage sa base, sa configuration et
  ses modules. Il ne sert aucune route.
- Les travaux d'un module s'inscrivent **ici**, en recevant les ports
  qu'un autre module remplit — exactement comme `wiring.py` le fait pour
  HTTP : `offers` reçoit le notificateur de `devices`, sans l'importer.
- Rien de lourd au démarrage : les travaux planifiés tournent à leur
  heure, pas au boot (AGENTS.md §6.4).
"""

from __future__ import annotations

from typing import Final

from karria.bootstrap.orm import TORTOISE_ORM
from karria.bootstrap.wiring import build_user_notifier
from karria.modules.offers import register_tasks as register_offers_tasks
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging, get_logger
from karria.shared.queue import App, Status, UniqueViolation, build_queue_app

logger = get_logger(__name__)

#: Travaux exécutés en même temps par un worker. Deux : une collecte (qui
#: attend surtout le réseau) et une synchronisation de l'index (qui occupe
#: le serveur d'inférence) peuvent avancer ensemble ; davantage se battrait
#: pour les mêmes huit cœurs.
WORKER_CONCURRENCY: Final = 2
#: Un travail « en cours » dont le worker ne donne plus signe de vie depuis
#: ce délai est relancé. Sans cela, un worker tué en plein travail laisse
#: son verrou d'exécution pris, et tous les travaux du même verrou
#: attendent pour toujours — constaté le 2026-10-22 en redémarrant le
#: worker pendant une synchronisation de l'index.
STALLED_AFTER_SECONDS: Final = 120.0
TASK_RETRY_STALLED: Final = "queue.retry_stalled"


def register_queue_maintenance(app: App) -> None:
    """Le travail du socle : relancer les travaux orphelins d'un worker mort."""

    @app.periodic(cron="*/2 * * * *", periodic_id=TASK_RETRY_STALLED)
    @app.task(name=TASK_RETRY_STALLED, queueing_lock=TASK_RETRY_STALLED)
    async def retry_stalled(timestamp: int) -> None:  # noqa: ARG001 - imposé par les travaux périodiques
        await retry_stalled_jobs(app)


async def retry_stalled_jobs(app: App) -> int:
    """Relance chaque travail bloqué ; rend leur nombre."""
    stalled = list(
        await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=STALLED_AFTER_SECONDS)
    )
    for job in stalled:
        try:
            await app.job_manager.retry_job(job)
        except UniqueViolation:
            # Un travail identique attend déjà (même verrou de file) : le
            # relancer ferait doublon. On clôt l'orphelin en échec.
            await app.job_manager.finish_job_by_id_async(
                job_id=int(job.id or 0), status=Status.FAILED, delete_job=False
            )
            logger.warning("worker.job_stalled_closed", task=job.task_name, job_id=job.id)
            continue
        logger.warning("worker.job_stalled_retried", task=job.task_name, job_id=job.id)
    await app.job_manager.prune_stalled_workers(seconds_since_heartbeat=STALLED_AFTER_SECONDS)
    return len(stalled)


def build_worker_app() -> App:
    """L'application de file avec tous les travaux inscrits."""
    app = build_queue_app(get_settings().db)
    register_queue_maintenance(app)
    register_offers_tasks(app, notifier=build_user_notifier)
    return app


async def run_worker() -> None:
    """Ouvre la base et la file, exécute jusqu'à l'arrêt."""
    settings = get_settings()
    configure_logging(settings.observability)
    await connect(TORTOISE_ORM)
    try:
        app = build_worker_app()
        async with app.open_async():
            logger.info("worker.started", concurrency=WORKER_CONCURRENCY)
            await app.run_worker_async(concurrency=WORKER_CONCURRENCY, install_signal_handlers=True)
    finally:
        await disconnect()
        logger.info("worker.stopped")
