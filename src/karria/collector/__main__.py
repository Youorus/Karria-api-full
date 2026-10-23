"""Lance le collecteur : `python -m karria.collector [--once] [--ft-sweep JOURS]`.

Sans option, deux boucles tournent ensemble dans le même processus :

- **France Travail**, toutes les `COLLECTOR__FRANCE_TRAVAIL_POLL_SECONDS`
  (3 min) : les annonces créées depuis le dernier passage ; plus un
  balayage quotidien par département ;
- **JobSpy**, toutes les `COLLECTOR__INTERVAL_SECONDS` (10 min) : les
  cibles louées à l'API.

C'est le mode d'une application Dokploy toujours active. `--once` fait un
passage de chaque et s'arrête (le mode d'une tâche planifiée) ;
`--ft-sweep N` ne fait qu'un balayage France Travail des N derniers jours
(l'amorçage d'un environnement). Une boucle ne doit pas coexister avec
une tâche planifiée.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from karria.collector.api_client import ApiError, CollectorApi
from karria.collector.france_travail import FranceTravailClient
from karria.collector.run import Collector, FranceTravailCollector
from karria.collector.settings import CollectorSettings
from karria.shared.config import ObservabilitySettings
from karria.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


def build(settings: CollectorSettings) -> tuple[Collector, FranceTravailCollector | None]:
    """Les deux collecteurs ; France Travail seulement s'il est configuré."""
    api = CollectorApi(
        settings.api_url,
        settings.token.get_secret_value(),
        timeout_seconds=settings.request_timeout_seconds,
    )
    credentials = settings.france_travail
    if credentials is None:
        logger.warning("france_travail.disabled")
        return Collector(settings, api), None
    client = FranceTravailClient(credentials, timeout_seconds=settings.request_timeout_seconds)
    return Collector(settings, api), FranceTravailCollector(settings, api, client)


async def jobspy_loop(collector: Collector, settings: CollectorSettings, *, once: bool) -> int:
    """Les passages JobSpy ; en boucle, une API muette n'arrête rien."""
    while True:
        try:
            await collector.run_once()
        except ApiError as exc:
            logger.error("collector.api_unavailable", status=exc.status, error=str(exc)[:200])
            if once:
                return 1
        if once:
            return 0
        await asyncio.sleep(settings.interval_seconds)


async def france_travail_loop(
    collector: FranceTravailCollector, settings: CollectorSettings, *, once: bool
) -> int:
    """Les passages incrémentaux France Travail, et le balayage quotidien."""
    while True:
        try:
            if collector.sweep_due(datetime.now(UTC)):
                await collector.sweep()
            await collector.poll_once()
        except ApiError as exc:
            logger.error("collector.api_unavailable", status=exc.status, error=str(exc)[:200])
            if once:
                return 1
        if once:
            return 0
        await asyncio.sleep(settings.france_travail_poll_seconds)


async def main(argv: list[str]) -> int:
    """Un passage, un balayage, ou les boucles."""
    parser = argparse.ArgumentParser(prog="karria.collector")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--ft-sweep", type=int, metavar="JOURS")
    args = parser.parse_args(argv)
    settings = CollectorSettings()
    configure_logging(
        ObservabilitySettings(log_level=settings.log_level, log_format=settings.log_format)
    )
    jobspy, france_travail = build(settings)
    if args.ft_sweep is not None:
        if france_travail is None:
            return 2
        try:
            await france_travail.sweep(args.ft_sweep)
        except ApiError as exc:
            logger.error("collector.api_unavailable", status=exc.status, error=str(exc)[:200])
            return 1
        return 0
    loops = [jobspy_loop(jobspy, settings, once=args.once)]
    if france_travail is not None:
        loops.append(france_travail_loop(france_travail, settings, once=args.once))
    codes = await asyncio.gather(*loops)
    return max(codes)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
