"""File de travaux durable : procrastinate, sur la base PostgreSQL du projet.

Contrat
-------
- **Un seul magasin.** Les travaux vivent dans des tables `procrastinate_*`
  de la même base que les données : un travail différé dans la
  transaction d'une écriture n'existe que si l'écriture est validée, et il
  n'y a ni Redis à opérer ni éviction silencieuse (la V1 a perdu des
  travaux ARQ sous `allkeys-lru`, et en a empilé 80 000 faute
  d'identifiant de dédoublonnage).
- **Le schéma de la file n'est pas celui d'aerich.** Il appartient à
  procrastinate et s'applique par `scripts/queue_schema.py` — un acte de
  déploiement, comme `aerich upgrade`, jamais au démarrage.
- **Le socle ne connaît aucune tâche.** Chaque module déclare les siennes ;
  l'assemblage les inscrit sur l'application de file
  (`bootstrap/worker.py`), en leur passant les ports qu'un autre module
  remplit (règle 4) — comme `bootstrap/wiring.py` le fait pour HTTP.

L'API ouvre la file pour **différer** des travaux, sans en exécuter ; le
worker (`scripts/worker.py`) les exécute.
"""

from __future__ import annotations

from typing import Final

from procrastinate import App, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued, UniqueViolation
from procrastinate.jobs import Status

from karria.shared.config import DatabaseSettings

#: Connexions de la file par processus : l'API ne fait que différer, le
#: worker exécute peu de travaux à la fois (voir `bootstrap/worker.py`).
POOL_MIN_SIZE: Final = 1
POOL_MAX_SIZE: Final = 4

__all__ = [
    "AlreadyEnqueued",
    "App",
    "Status",
    "UniqueViolation",
    "build_queue_app",
    "conninfo_from",
]


def conninfo_from(settings: DatabaseSettings) -> str:
    """L'URL de la base au format libpq (`postgresql://`), depuis celle de Tortoise."""
    url = settings.url
    for prefix in ("postgres://", "asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url.removeprefix(prefix)
    return url


def build_queue_app(settings: DatabaseSettings) -> App:
    """L'application de file, non ouverte : l'appelant l'ouvre pour la durée du processus."""
    connector = PsycopgConnector(
        conninfo=conninfo_from(settings), min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE
    )
    return App(connector=connector)
