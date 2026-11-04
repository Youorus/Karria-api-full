"""Matérialise le vocabulaire des suggestions à la saisie du CV (ADR-0028).

    uv run python -m scripts.suggestions refresh

Ce que le worker fait chaque nuit (`cv.refresh_suggestions`), à la demande :
après la migration 15, ou après une collecte massive. Lit les intitulés,
entreprises et villes distincts des offres actives, les nettoie, et remplace
la table `cv_suggestions` famille par famille. Idempotent : relancer
produit le même résultat. Durable, pas jetable.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.cv import run_refresh
from karria.modules.offers import build_offer_vocabulary
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def refresh() -> dict[str, int]:
    """Une matérialisation complète ; rend le nombre d'entrées écrites par famille."""
    configure_logging(get_settings().observability)
    await connect(TORTOISE_ORM)
    try:
        report = await run_refresh(build_offer_vocabulary)
    finally:
        await disconnect()
    return {kind.value: count for kind, count in report.written.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else "")
    parser.add_argument("command", choices=["refresh"])
    parser.parse_args(argv)
    written = asyncio.run(refresh())
    for kind, count in written.items():
        sys.stdout.write(f"{kind}: {count}\n")
    return 0 if any(written.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
