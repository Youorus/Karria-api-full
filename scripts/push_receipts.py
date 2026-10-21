"""Lit les reçus des notifications push et détache les jetons morts.

    uv run python -m scripts.push_receipts

À planifier toutes les quinze minutes environ (cron, tâche d'orchestrateur).
Jamais au démarrage de l'API (AGENTS.md §6.4).

Pourquoi ce script existe
-------------------------
Expo accepte un message et rend un ticket ; la livraison réelle se lit dans
un **reçu**, disponible quelques minutes plus tard. Un appareil désinstallé
après avoir été accepté n'est signalé que là. Sans cette lecture, on
continuerait à lui envoyer — et Expo finit par bannir les expéditeurs qui
persistent.
"""

from __future__ import annotations

import asyncio
import sys

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.devices import CheckPushReceipts
from karria.modules.devices.api.dependencies import (
    get_check_push_receipts,
    get_clock,
    get_device_repository,
    get_push_gateway,
    get_ticket_repository,
)
from karria.shared.config import get_settings
from karria.shared.database import TortoiseUnitOfWork, connect, disconnect


def _assemble() -> CheckPushReceipts:
    """Assemble le use case avec les mêmes fournisseurs que l'API."""
    return get_check_push_receipts(
        devices=get_device_repository(),
        tickets=get_ticket_repository(),
        gateway=get_push_gateway(get_settings()),
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


async def main() -> int:
    """Traite les tickets en attente, par lots, jusqu'à épuisement."""
    await connect(TORTOISE_ORM)
    try:
        use_case = _assemble()
        total_revoked = 0
        while True:
            revoked = await use_case.execute()
            total_revoked += revoked
            if revoked == 0:
                break
        print(f"Reçus traités. Jetons détachés : {total_revoked}.")
    finally:
        await disconnect()
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    sys.exit(asyncio.run(main()))
