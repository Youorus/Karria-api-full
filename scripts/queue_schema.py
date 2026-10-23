"""Applique le schéma de la file durable (tables `procrastinate_*`).

    uv run python -m scripts.queue_schema

Un acte de déploiement, comme `aerich upgrade`, et jamais au démarrage. Le
schéma appartient à procrastinate : à chaque montée de version de la
bibliothèque, lire ses notes de migration avant de relancer.
"""

from __future__ import annotations

import asyncio
import sys

from karria.shared.config import get_settings
from karria.shared.queue import build_queue_app


async def main() -> int:
    """Applique le schéma ; échoue s'il existe déjà (rien n'est écrasé)."""
    app = build_queue_app(get_settings().db)
    async with app.open_async():
        await app.schema_manager.apply_schema_async()
    print("Schéma de la file appliqué.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
