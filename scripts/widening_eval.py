"""Évalue les élargissements sur de vraies demandes, contre l'index courant — en lecture seule.

Pour chaque demande : le nombre d'offres retenues, les élargissements que
Karria proposerait (avec leurs gains comptés), et la latence de la recherche
puis du comptage. Rien n'est écrit : ni critères, ni fil, ni cibles.

    uv run python -m scripts.widening_eval                 # les demandes du jeu
    uv run python -m scripts.widening_eval --out /tmp/w.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.offers import build_search_offers
from karria.modules.offers.application import SuggestWidenings
from karria.modules.offers.domain import (
    ContractType,
    LocationCriteria,
    Schedule,
    SearchCriteria,
)
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging


def _place(label: str, lat: float, lng: float, dept: str, radius: int = 30) -> LocationCriteria:
    return LocationCriteria(
        label=label, latitude=lat, longitude=lng, department_code=dept, radius_km=radius
    )


QUERIES: list[SearchCriteria] = [
    SearchCriteria(
        query="cuisinier en CDI à Lyon",
        job_titles=["cuisinier", "cuisinière"],
        keywords=["cuisine", "restauration"],
        location=_place("Lyon", 45.758, 4.835, "69"),
        contract_types=[ContractType.CDI],
    ),
    SearchCriteria(
        query="serveur à Paris",
        job_titles=["serveur", "serveuse"],
        location=_place("Paris", 48.857, 2.352, "75"),
    ),
    SearchCriteria(
        query="data analyst à Lille",
        job_titles=["data analyst", "analyste de données"],
        keywords=["SQL", "Power BI"],
        location=_place("Lille", 50.633, 3.066, "59"),
    ),
    SearchCriteria(
        query="caissière à temps partiel à Marseille",
        job_titles=["caissière", "hôte de caisse"],
        location=_place("Marseille", 43.296, 5.370, "13"),
        schedule=Schedule.PART_TIME,
    ),
    SearchCriteria(
        query="développeur Python à Toulouse",
        job_titles=["développeur python", "développeur backend"],
        keywords=["Python"],
        location=_place("Toulouse", 43.604, 1.444, "31"),
    ),
    SearchCriteria(
        query="infirmier à Bordeaux en CDI",
        job_titles=["infirmier", "infirmière"],
        location=_place("Bordeaux", 44.838, -0.579, "33"),
        contract_types=[ContractType.CDI],
    ),
    SearchCriteria(
        query="chauffeur poids lourd à Rennes",
        job_titles=["chauffeur poids lourd", "conducteur routier"],
        location=_place("Rennes", 48.117, -1.678, "35"),
    ),
    SearchCriteria(
        query="comptable en alternance à Nantes",
        job_titles=["comptable", "assistant comptable"],
        location=_place("Nantes", 47.218, -1.554, "44"),
        contract_types=[ContractType.ALTERNANCE],
    ),
    SearchCriteria(
        query="boulanger à Strasbourg",
        job_titles=["boulanger", "boulangère"],
        location=_place("Strasbourg", 48.573, 7.752, "67"),
    ),
    SearchCriteria(
        query="commercial en intérim à Nice",
        job_titles=["commercial", "attaché commercial"],
        location=_place("Nice", 43.710, 7.262, "06"),
        contract_types=[ContractType.INTERIM],
    ),
    SearchCriteria(
        query="maçon dans le Gard",
        job_titles=["maçon"],
        location=LocationCriteria(label="Gard", department_codes=["30"]),
    ),
    SearchCriteria(
        query="aide-soignant à Dijon",
        job_titles=["aide-soignant", "aide-soignante"],
        location=_place("Dijon", 47.322, 5.041, "21"),
    ),
    SearchCriteria(
        query="ingénieur aéronautique à Brest",
        job_titles=["ingénieur aéronautique"],
        location=_place("Brest", 48.390, -4.486, "29"),
    ),
]


async def main() -> list[dict[str, Any]]:
    """Joue chaque demande, imprime le tableau et rend les lignes."""
    settings = get_settings()
    configure_logging(settings.observability)
    await connect(TORTOISE_ORM)
    try:
        search = build_search_offers(settings)
        widen = SuggestWidenings(search=search)
        rows: list[dict[str, Any]] = []
        for criteria in QUERIES:
            started = time.perf_counter()
            result = await search.execute(criteria)
            searched = time.perf_counter()
            widenings = await widen.execute(
                criteria, current=len(result.ranked), rejected=result.rejected
            )
            counted = time.perf_counter()
            row = {
                "query": criteria.query,
                "results": len(result.ranked),
                "candidates": result.candidates,
                "rejected": len(result.rejected),
                "search_ms": round((searched - started) * 1000),
                "widen_ms": round((counted - searched) * 1000),
                "widenings": [{"id": w.id, "gain": w.gain, "label": w.label} for w in widenings],
            }
            rows.append(row)
            proposals = " ; ".join(f"{w['id']} +{w['gain']}" for w in row["widenings"]) or "—"
            print(
                f"{criteria.query:<42} {row['results']:>5} offres  "
                f"({row['search_ms']:>5} ms + {row['widen_ms']:>5} ms)  {proposals}"
            )
        return rows
    finally:
        await disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    lines = asyncio.run(main())
    if args.out is not None:
        args.out.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
