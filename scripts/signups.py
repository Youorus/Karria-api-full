"""D'où viennent les inscrits : la répartition par pays et par mois. Lecture seule.

    uv run python -m scripts.signups                    # tous les mois
    uv run python -m scripts.signups --since 2026-11    # à partir de novembre 2026

Ce que le script lit
--------------------
`identity_users.signup_region` — le réglage « Région » du téléphone, écrit une
fois sur le compte à l'inscription (ADR-0042) — groupé par mois de création
(en UTC). Il n'écrit rien et n'affiche aucune adresse : des comptes par pays.

Comment lire « pays inconnu »
-----------------------------
Ce sont les comptes dont l'app n'a rien annoncé : ceux créés avant la
migration 29, et ceux d'une version de l'app antérieure tant qu'ils n'ont
pas redéclaré leur appareil. Leur part baisse à mesure que les gens rouvrent
l'app ; elle ne tombe pas à zéro (comptes dormants).

Ce que le pays ne dit pas
-------------------------
C'est un réglage de l'appareil, pas un lieu : une personne installée à Lyon
dont le téléphone est réglé sur « Sénégal » compte pour le Sénégal.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import defaultdict

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.identity import get_signup_directory
from karria.shared.countries import country_name_fr
from karria.shared.database import connect, disconnect

#: Une ligne lue en base : mois « AAAA-MM », code de région ou `None`, inscrits.
type Row = tuple[str, str | None, int]

UNKNOWN_COUNTRY = "pays inconnu"
_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def label(region: str | None) -> str:
    """« Belgique (BE) », « pays inconnu », ou « pays inconnu (QQ) » pour un code hors table."""
    if region is None:
        return UNKNOWN_COUNTRY
    return f"{country_name_fr(region) or UNKNOWN_COUNTRY} ({region})"


def since(rows: list[Row], month: str | None) -> list[Row]:
    """Les lignes à partir de `month` compris (« AAAA-MM » se compare comme du texte)."""
    return rows if month is None else [row for row in rows if row[0] >= month]


def _ranked(counts: dict[str | None, int]) -> list[tuple[str | None, int]]:
    """Du pays le plus représenté au moins représenté ; « inconnu » toujours en dernier."""
    return sorted(counts.items(), key=lambda kv: (kv[0] is None, -kv[1], kv[0] or ""))


def _block(title: str, counts: dict[str | None, int]) -> list[str]:
    total = sum(counts.values())
    lines = [f"{title} — {total} inscrit{'s' if total > 1 else ''}"]
    for region, count in _ranked(counts):
        lines.append(f"  {label(region):<42} {count:>6}  {count * 100 / total:5.1f} %")
    return lines


def report(rows: list[Row]) -> str:
    """Le tableau : l'ensemble de la période, puis chaque mois, du plus récent au plus ancien."""
    if not rows:
        return "Aucun inscrit sur la période."
    overall: dict[str | None, int] = defaultdict(int)
    months: dict[str, dict[str | None, int]] = defaultdict(lambda: defaultdict(int))
    for month, region, count in rows:
        overall[region] += count
        months[month][region] += count
    lines = _block("Toute la période", overall)
    for month in sorted(months, reverse=True):
        lines += ["", *_block(month, months[month])]
    return "\n".join(lines)


def _month(value: str) -> str:
    if not _MONTH.fullmatch(value):
        msg = "attendu : AAAA-MM (par exemple 2026-11)"
        raise argparse.ArgumentTypeError(msg)
    return value


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.signups", description="Inscrits par pays et par mois (lecture seule)."
    )
    parser.add_argument("--since", type=_month, default=None, help="Premier mois lu, AAAA-MM.")
    return parser.parse_args(argv)


async def main(argv: list[str]) -> int:
    """Lit la répartition et l'affiche."""
    args = _parse(argv)
    await connect(TORTOISE_ORM)
    try:
        rows = await get_signup_directory().signups_by_month()
    finally:
        await disconnect()
    print(report(since(rows, args.since)))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
