"""Commandes d'exploitation des entreprises et de leurs logos (module `offers`).

    uv run python -m scripts.companies import-v1 FICHIER.jsonl[.gz]   # entreprises de la V1
    uv run python -m scripts.companies backfill [--all]                # rattache les offres en base
    uv run python -m scripts.companies fetch-logos [--budget S] [--concurrency N]
    uv run python -m scripts.companies coverage                        # couverture des logos par source
    uv run python -m scripts.companies shared-logos [--limit N]        # logos partagés (images génériques ?)
    uv run python -m scripts.companies reset-untrusted-v1              # retire les logos V1 douteux

`import-v1` lit l'export de la table `companies` de la V1, une ligne JSON
par entreprise (`id`, `name`, `website`, `logo`), produit **depuis une base
de travail** où le dump a été restauré — jamais depuis la production V1 :

    \\copy (select json_build_object('id',id,'name',name,'website',website,'logo',logo)
            from public.companies) to 'companies.jsonl'

Le worker cherche les logos tout seul (`offers.fetch_logos`, toutes les cinq
minutes) ; `fetch-logos` sert au rattrapage initial, plus large. Rien de tout
cela ne tourne au démarrage de l'API (AGENTS.md §6.4).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tortoise import connections

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.offers import (
    CompanySighting,
    build_attach_companies,
    build_backfill_companies,
    company_domain,
    company_key,
    fetch_logos_within,
    plausible_domain,
    trusted_v1_logo,
)
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging

IMPORT_BATCH = 2_000

COVERAGE_SQL = """
SELECT source,
       count(*) AS offers,
       count(*) FILTER (WHERE company_name IS NOT NULL) AS named,
       count(*) FILTER (WHERE company_logo_url IS NOT NULL) AS source_logo,
       count(*) FILTER (WHERE company_id IS NOT NULL) AS linked,
       count(*) FILTER (WHERE company_logo_hash IS NOT NULL) AS logo
  FROM offers_offers
 WHERE is_active AND duplicate_of IS NULL
 GROUP BY source ORDER BY offers DESC
"""
STATUS_SQL = """
SELECT logo_status, coalesce(logo_origin, '-') AS origin, count(*) AS n,
       count(*) FILTER (WHERE v1_id IS NOT NULL) AS v1
  FROM offers_companies GROUP BY 1, 2 ORDER BY 1, 2
"""
SHARED_SQL = """
SELECT logo_hash, count(*) AS companies, min(name) AS example, max(name) AS other
  FROM offers_companies WHERE logo_hash IS NOT NULL
 GROUP BY logo_hash HAVING count(*) > 1 ORDER BY 2 DESC LIMIT $1
"""


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def v1_sighting(row: dict[str, Any]) -> CompanySighting | None:
    """Une ligne de `companies` (V1) → ce qu'on en retient."""
    name = str(row.get("name") or "").strip()
    key = company_key(name)
    if key is None or not isinstance(row.get("id"), int):
        return None
    website = row.get("website") if isinstance(row.get("website"), str) else None
    logo = row.get("logo") if isinstance(row.get("logo"), str) else None
    domain = company_domain(website)
    if not plausible_domain(domain, key):
        domain, website = None, None
    return CompanySighting(
        key=key,
        name=name[:255],
        domain=domain,
        website=website[:2048] if website and domain else None,
        logo_url=logo[:2048] if logo and logo.startswith(("http://", "https://")) else None,
        v1_id=int(row["id"]),
    )


async def import_v1(path: Path) -> None:
    """Entreprises V1 → `offers_companies`, par lots ; idempotent (clé de nom + domaine)."""
    attach = build_attach_companies()
    batch: list[CompanySighting] = []
    created = updated = seen = 0
    for row in _rows(path):
        sighting = v1_sighting(row)
        if sighting is None:
            continue
        batch.append(sighting)
        seen += 1
        if len(batch) >= IMPORT_BATCH:
            report = await attach.execute(batch)
            created, updated, batch = created + report.created, updated + report.updated, []
    if batch:
        report = await attach.execute(batch)
        created, updated = created + report.created, updated + report.updated
    print(json.dumps({"v1_rows": seen, "created": created, "updated": updated}))


async def coverage() -> None:
    """La couverture des logos, par source, sur les offres actives canoniques."""
    connection = connections.get("default")
    for row in await connection.execute_query_dict(COVERAGE_SQL):
        offers = int(row["offers"]) or 1
        print(
            json.dumps(
                {
                    **{k: int(v) if isinstance(v, int) else v for k, v in row.items()},
                    "source_logo_pct": round(100 * int(row["source_logo"]) / offers, 1),
                    "logo_pct": round(100 * int(row["logo"]) / offers, 1),
                    "logo_of_named_pct": round(
                        100 * int(row["logo"]) / (int(row["named"]) or 1), 1
                    ),
                }
            )
        )
    for row in await connection.execute_query_dict(STATUS_SQL):
        print(json.dumps({"companies": dict(row)}))


async def shared_logos(limit: int) -> None:
    """Les logos portés par plusieurs entreprises : une image générique s'y repère."""
    connection = connections.get("default")
    for row in await connection.execute_query_dict(SHARED_SQL, [limit]):
        print(json.dumps(dict(row), ensure_ascii=False))


UNTRUSTED_SQL = """
SELECT id, key, domain, v1_logo_url, logo_origin FROM offers_companies
 WHERE logo_origin IN ('v1', 'favicon')
"""
RESET_SQL = """
WITH reset AS (
    UPDATE offers_companies
       SET logo_hash = NULL, logo_origin = NULL, logo_status = 'pending',
           logo_attempts = 0, logo_next_attempt_at = now()
     WHERE id = ANY($1::uuid[])
    RETURNING id
)
UPDATE offers_offers AS o SET company_logo_hash = NULL FROM reset WHERE o.company_id = reset.id
"""


async def reset_untrusted_v1() -> None:
    """Les logos V1 ou d'icône pris avant `trusted_v1_logo` / `plausible_domain` : remis en recherche."""
    connection = connections.get("default")
    rows = await connection.execute_query_dict(UNTRUSTED_SQL)
    doubtful = [
        r["id"]
        for r in rows
        if (r["logo_origin"] == "favicon" and not plausible_domain(r["domain"], r["key"]))
        or (
            r["logo_origin"] == "v1"
            and not trusted_v1_logo(r["v1_logo_url"] or "", r["domain"], r["key"])
        )
    ]
    if doubtful:
        await connection.execute_query(RESET_SQL, [doubtful])
    print(json.dumps({"v1_logos": len(rows), "reset": len(doubtful)}))


async def main(argv: list[str]) -> int:
    """Point d'entrée."""
    parser = argparse.ArgumentParser(prog="scripts.companies")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-v1")
    importer.add_argument("path", type=Path)
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--all", action="store_true", help="aussi les offres inactives")
    fetch = commands.add_parser("fetch-logos")
    fetch.add_argument("--budget", type=float, default=3_600.0)
    fetch.add_argument("--concurrency", type=int, default=32)
    commands.add_parser("coverage")
    commands.add_parser("reset-untrusted-v1")
    shared = commands.add_parser("shared-logos")
    shared.add_argument("--limit", type=int, default=30)
    args = parser.parse_args(argv)

    configure_logging(get_settings().observability)
    await connect(TORTOISE_ORM)
    try:
        if args.command == "import-v1":
            await import_v1(args.path)
        elif args.command == "backfill":
            report = await build_backfill_companies().execute(active_only=not args.all)
            print(
                json.dumps(
                    {"offers": report.offers, "linked": report.linked, "created": report.created}
                )
            )
        elif args.command == "fetch-logos":
            seen = await fetch_logos_within(budget=args.budget, concurrency=args.concurrency)
            print(json.dumps({"companies": seen}))
        elif args.command == "coverage":
            await coverage()
        elif args.command == "reset-untrusted-v1":
            await reset_untrusted_v1()
        else:
            await shared_logos(args.limit)
    finally:
        await disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
