"""Lance le worker de la file durable.

    uv run python -m scripts.worker

Un conteneur à part de l'API, même image, même configuration. Il exécute
la collecte, la synchronisation de l'index, les alertes et la suite des
fils (`src/karria/modules/offers/api/tasks.py`).
"""

from __future__ import annotations

import asyncio

from karria.bootstrap.worker import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
