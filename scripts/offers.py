"""Commandes d'exploitation du module `offers`.

    uv run python -m scripts.offers ensure-indexes      # crée les collections Qdrant
    uv run python -m scripts.offers sync                # vide la boîte d'envoi vers l'index
    uv run python -m scripts.offers reconcile           # réinscrit l'écart base ↔ index
    uv run python -m scripts.offers expire              # désactive les offres trop anciennes
    uv run python -m scripts.offers refresh-targets     # recalcule les cibles de collecte
    uv run python -m scripts.offers replay FICHIER...   # ré-ingère des annonces brutes (JSONL)
        [--renormalize]                                 # ... et rejoue la normalisation des connues

Le worker (`scripts/worker.py`) fait tout cela à son heure ; ces commandes
servent à amorcer un environnement, à rattraper, et à mesurer. Aucune ne
tourne au démarrage de l'API (AGENTS.md §6.4).

`replay` lit des lots au format de remise (`docs/collecteur.md`), tels que
l'atterrissage `landing` les garde (`.gz`) : c'est le chemin pour rejouer
une normalisation corrigée sans re-collecter.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from karria.bootstrap.orm import TORTOISE_ORM
from karria.bootstrap.wiring import build_user_notifier
from karria.modules.offers import (
    RawPosting,
    build_expire_offers,
    build_ingest_postings,
    build_reconcile_index,
    build_refresh_targets,
    build_sync_index,
    ensure_indexes,
)
from karria.modules.offers.domain import posting_from_record
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging

#: Annonces ingérées par lot au rejeu : la taille d'une page de collecte.
REPLAY_BATCH = 120


def _read_rows(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _postings(paths: list[Path]) -> Iterator[RawPosting]:
    for path in paths:
        for row in _read_rows(path):
            posting = posting_from_record(row)
            if posting is not None:
                yield posting


async def _replay(paths: list[Path], *, renormalize: bool) -> None:
    settings = get_settings()
    ingest = build_ingest_postings(settings)
    totals: dict[str, int] = {}
    batch: list[RawPosting] = []
    started = time.monotonic()

    async def flush() -> None:
        stats = await ingest.execute(batch, renormalize=renormalize)
        for name in (
            "received",
            "inserted",
            "refreshed",
            "updated",
            "duplicates",
            "near_duplicates",
            "errors",
        ):
            totals[name] = totals.get(name, 0) + int(getattr(stats, name))
        batch.clear()

    for posting in _postings(paths):
        batch.append(posting)
        if len(batch) >= REPLAY_BATCH:
            await flush()
    if batch:
        await flush()
    elapsed = time.monotonic() - started
    received = totals.get("received", 0)
    print(
        json.dumps(
            {
                **totals,
                "seconds": round(elapsed, 1),
                "per_second": round(received / elapsed, 1) if elapsed else None,
                "new_share": round(totals.get("inserted", 0) / received, 3) if received else None,
            }
        )
    )


async def _sync() -> None:
    sync = build_sync_index(get_settings(), build_user_notifier())
    started = time.monotonic()
    embedded = claimed = 0
    while True:
        report = await sync.execute()
        if report.claimed == 0:
            break
        claimed += report.claimed
        embedded += report.embedded
    elapsed = time.monotonic() - started
    print(
        json.dumps(
            {
                "claimed": claimed,
                "embedded": embedded,
                "seconds": round(elapsed, 1),
                "embedded_per_second": round(embedded / elapsed, 2) if elapsed else None,
            }
        )
    )


async def main(argv: list[str]) -> int:
    """Analyse la ligne de commande et lance la commande demandée."""
    parser = argparse.ArgumentParser(prog="scripts.offers")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ensure-indexes")
    commands.add_parser("sync")
    commands.add_parser("reconcile")
    commands.add_parser("expire")
    commands.add_parser("refresh-targets")
    replay = commands.add_parser("replay")
    replay.add_argument("paths", nargs="+", type=Path)
    replay.add_argument("--renormalize", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.observability)
    await connect(TORTOISE_ORM)
    try:
        match args.command:
            case "ensure-indexes":
                await ensure_indexes(settings)
            case "sync":
                await _sync()
            case "reconcile":
                print(await build_reconcile_index(settings).execute())
            case "expire":
                print(await build_expire_offers().execute())
            case "refresh-targets":
                print(await build_refresh_targets().execute())
            case "replay":
                await _replay(args.paths, renormalize=args.renormalize)
    finally:
        await disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
