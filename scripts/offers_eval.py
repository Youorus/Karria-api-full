"""Évalue la recherche sur le corpus réel : compréhension, pertinence, latence par étage.

    uv run python -m scripts.offers_eval [--runs 3] [--out resultats.jsonl]

Pour chaque demande en français de `QUERIES` (métiers x villes x contrats) :

1. Karria (Gemini) comprend la demande et produit des critères — un seul
   tour, sans historique ;
2. le lieu est géocodé, la recherche tourne (plongement, index hybride,
   base, décision du domaine) ;
3. les dix premières offres sont écrites, une par ligne, pour être
   **jugées** : bon métier, bon lieu, bon contrat, rien de hors sujet.

La latence est mesurée étage par étage sur `--runs` passages (le premier
réchauffe les caches), hors tour de dialogue. C'est l'outil qui recalibre
les seuils de `domain/ranking.py` et les poids de fusion de
`infrastructure/qdrant.py` quand un modèle change.

N'écrit rien en base : ni critères, ni fil.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.offers.api.dependencies import (
    get_embedder,
    get_geocoder,
    get_offer_index,
    get_offer_repository,
    get_search_dialogue,
)
from karria.modules.offers.application import (
    OfferIndex,
    OfferRepository,
    SearchOffers,
    TextEmbedder,
)
from karria.modules.offers.domain import DialogueStatus, SearchCriteria, decide
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.llm import build_language_model
from karria.shared.logging import configure_logging

QUERIES: tuple[str, ...] = (
    "serveur à Paris",
    "Je cherche un poste de serveuse à Lyon en CDI",
    "cuisinier Marseille",
    "commis de cuisine à Bordeaux, je débute",
    "barman Toulouse",
    "vendeuse en boulangerie à Nantes",
    "caissière à temps partiel à Lille",
    "aide-soignante de nuit à Marseille",
    "infirmier en CDI à Toulouse",
    "auxiliaire de vie à Rennes",
    "éducateur spécialisé à Strasbourg",
    "développeur Python à Paris en CDI",
    "alternance développeur web à Lyon",
    "data analyst à Paris",
    "stage en data science à Paris",
    "comptable en CDI à Bordeaux",
    "assistante administrative à Lille",
    "commercial terrain à Nantes",
    "téléconseiller en télétravail complet",
    "chauffeur livreur permis B à Marseille",
    "chauffeur poids lourd à Lyon",
    "préparateur de commandes en intérim à Lille",
    "cariste à Toulouse",
    "agent d'entretien à Paris",
    "électricien à Rennes",
    "plombier à Montpellier",
    "mécanicien automobile à Strasbourg",
    "technicien de maintenance industrielle à Lyon",
    "coiffeuse à Bordeaux",
    "agent de sécurité à Paris",
    "chargé de recrutement en alternance à Paris",
    "juriste en droit social à Paris",
    "chef de projet digital à Lyon",
    "un job étudiant le week-end à Paris",
    "stage en marketing digital à Lyon",
    "cuisinier en temps plein dans toute la France",
)

JUDGE_SYSTEM = """Tu évalues un moteur de recherche d'offres d'emploi français. On te donne la demande d'une personne, les critères que le moteur en a compris (lieu, rayon en km, départements, contrats, temps de travail), et dix offres retournées (titre, employeur, ville, département, contrat, temps de travail). Pour chaque offre, dis si elle est PERTINENTE :
- le bon MÉTIER : celui demandé, pas un métier voisin ni un autre niveau (« chef cuisinier » n'est pas « cuisinier » ; « aide ménagère » n'est pas « auxiliaire de vie ») ;
- le bon LIEU : la ville demandée OU une commune située dans le rayon indiqué autour d'elle (une banlieue ou une commune voisine dans le rayon est un bon lieu), OU l'un des départements indiqués, OU n'importe où si la personne cherche dans toute la France ;
- rien de CONTRAIRE à ce que la personne a précisé (contrat, temps de travail, niveau). Une information absente de l'offre n'est pas contraire ; une précision de la demande que l'offre ne mentionne pas (« de nuit », « permis B ») n'est pas contraire non plus.
Réponds pour chaque indice d'offre, dans l'ordre."""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "relevant": {"type": "boolean"}},
                "required": ["index", "relevant"],
            },
        }
    },
    "required": ["verdicts"],
}


async def _judge(query: str, criteria: SearchCriteria, top: list[dict[str, Any]]) -> list[bool]:
    """Gemini juge la pertinence des dix premières ; une panne vaut « non jugé » (liste vide)."""
    from karria.shared.http.outbound import JsonClient, OutboundError  # noqa: PLC0415 - outil d'évaluation

    settings = get_settings()
    client = JsonClient(
        base_url=settings.gemini.base_url,
        headers={"x-goog-api-key": settings.gemini.api_key.get_secret_value()},
        timeout_seconds=settings.gemini.request_timeout_seconds,
    )
    lines = [
        f"{i}. {o['title']} — {o.get('company') or '?'} — {o.get('city') or '?'} ({o.get('dept') or '?'}) — "
        f"contrat : {o.get('contract') or 'non dit'} — temps : {o.get('schedule') or 'non dit'}"
        for i, o in enumerate(top)
    ]
    understood = {
        "lieu": criteria.location.label if criteria.location else None,
        "rayon_km": criteria.location.radius_km
        if criteria.location and criteria.location.is_geocoded
        else None,
        "departements": criteria.location.department_codes if criteria.location else [],
        "toute_la_france": criteria.whole_france or criteria.wants_remote_only,
        "contrats": [c.value for c in criteria.contract_types],
        "temps": criteria.schedule.value if criteria.schedule else None,
    }
    payload = {
        "system_instruction": {"parts": [{"text": JUDGE_SYSTEM}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": f"<demande>\n{query}\n</demande>\n\n<criteres>\n"
                        + json.dumps(understood, ensure_ascii=False)
                        + "\n</criteres>\n\n<offres>\n"
                        + "\n".join(lines)
                        + "\n</offres>"
                    }
                ],
            }
        ],
        "generation_config": {
            "temperature": 0.0,
            "response_mime_type": "application/json",
            "response_json_schema": JUDGE_SCHEMA,
            "thinking_config": {"thinking_budget": 0},
        },
    }
    try:
        body = (
            await client.post_json(f"models/{settings.gemini.text_model}:generateContent", payload)
        ).mapping
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        verdicts = {int(v["index"]): bool(v["relevant"]) for v in json.loads(text)["verdicts"]}
    except (OutboundError, KeyError, IndexError, ValueError, json.JSONDecodeError):
        return []
    return [verdicts.get(i, False) for i in range(len(top))]


class Timed:
    """Mesure le temps passé dans chaque port, par étage."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}

    def record(self, stage: str, seconds: float) -> None:
        """Ajoute une mesure."""
        self.samples.setdefault(stage, []).append(seconds * 1000)


class TimedEmbedder:
    """Le plongement de la requête, chronométré."""

    def __init__(self, inner: TextEmbedder, timed: Timed) -> None:
        self._inner, self._timed = inner, timed

    @property
    def version(self) -> str:
        """La version du modèle."""
        return self._inner.version

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        """Non chronométré : la recherche ne plonge que la requête."""
        return await self._inner.embed_documents(texts)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Chronométré."""
        started = time.perf_counter()
        try:
            return await self._inner.embed_query(text)
        finally:
            self._timed.record("embed", time.perf_counter() - started)


class TimedIndex:
    """L'index, chronométré à la recherche."""

    def __init__(self, inner: OfferIndex, timed: Timed) -> None:
        self._inner, self._timed = inner, timed

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def search(self, query: Any) -> Any:
        """Chronométré."""
        started = time.perf_counter()
        try:
            return await self._inner.search(query)
        finally:
            self._timed.record("index", time.perf_counter() - started)


class TimedOffers:
    """La base, chronométrée sur la lecture des candidats."""

    def __init__(self, inner: OfferRepository, timed: Timed) -> None:
        self._inner, self._timed = inner, timed

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def find_many(self, offer_ids: Any, *, light: bool = False) -> Any:
        """Chronométré."""
        started = time.perf_counter()
        try:
            return await self._inner.find_many(offer_ids, light=light)
        finally:
            self._timed.record("database", time.perf_counter() - started)


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 1),
        "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 1),
        "max": round(ordered[-1], 1),
    }


async def _understand(query: str) -> tuple[SearchCriteria | None, float, str]:
    settings = get_settings()
    dialogue = get_search_dialogue(build_language_model(settings))
    geocoder = get_geocoder(settings)
    started = time.perf_counter()
    proposal = await dialogue.propose(history=[], current=None, message=query)
    elapsed = (time.perf_counter() - started) * 1000
    criteria = proposal.criteria
    if criteria is not None and criteria.location is not None and not criteria.location.is_usable:
        place = await geocoder.geocode(criteria.location.label)
        if place is not None:
            criteria = criteria.model_copy(
                update={
                    "location": criteria.location.model_copy(
                        update={"latitude": place.latitude, "longitude": place.longitude}
                    )
                }
            )
    outcome = decide(dataclasses.replace(proposal, criteria=criteria), None)
    found = outcome.criteria if outcome.status is DialogueStatus.SEARCH else None
    return found, elapsed, outcome.reply


def _top_rows(result: Any) -> list[dict[str, Any]]:
    """Les dix premières offres, à plat, pour le fichier et pour le juge."""
    top: list[dict[str, Any]] = []
    for item in result.ranked[:10]:
        offer = result.offers[item.offer_id]
        location = offer.location
        top.append(
            {
                "score": item.score,
                "title": offer.title,
                "company": offer.company.name if offer.company else None,
                "city": location.city if location else None,
                "dept": location.department_code if location else None,
                "contract": offer.contract_type.value if offer.contract_type else None,
                "schedule": offer.schedule.value if offer.schedule else None,
                "remote": offer.remote.value if offer.remote else None,
                "source": offer.source.value,
                "reasons": list(item.reasons),
            }
        )
    return top


async def main(argv: list[str]) -> int:
    """Comprend, cherche, écrit les dix premiers, mesure."""
    parser = argparse.ArgumentParser(prog="scripts.offers_eval")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("offers_eval.jsonl"))
    parser.add_argument("--only", type=int, nargs="*", default=None)
    parser.add_argument("--judge", action="store_true", help="Gemini juge les dix premières")
    args = parser.parse_args(argv)
    judged: dict[str, list[int]] = {}  # source → [pertinentes, jugées]

    settings = get_settings()
    configure_logging(settings.observability)
    await connect(TORTOISE_ORM)
    timed = Timed()
    search = SearchOffers(
        embedder=TimedEmbedder(get_embedder(settings), timed),
        index=TimedIndex(get_offer_index(settings), timed),  # type: ignore[arg-type]  # doublure de mesure
        offers=TimedOffers(get_offer_repository(), timed),  # type: ignore[arg-type]  # doublure de mesure
    )
    totals: list[float] = []
    dialogue_ms: list[float] = []
    try:
        selected = [QUERIES[i] for i in args.only] if args.only else list(QUERIES)
        understood: list[tuple[str, SearchCriteria | None, str]] = []
        for query in selected:
            criteria, elapsed, reply = await _understand(query)
            dialogue_ms.append(elapsed)
            understood.append((query, criteria, reply))
        with args.out.open("w", encoding="utf-8") as out:
            for run in range(args.runs):
                for query, criteria, reply in understood:
                    if criteria is None:
                        if run == 0:
                            out.write(
                                json.dumps(
                                    {"query": query, "reply": reply, "criteria": None},
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                        continue
                    started = time.perf_counter()
                    result = await search.execute(criteria)
                    totals.append((time.perf_counter() - started) * 1000)
                    if run != args.runs - 1:
                        continue
                    top = _top_rows(result)
                    verdicts = await _judge(query, criteria, top) if args.judge and top else []
                    for item_row, verdict in zip(top, verdicts, strict=False):
                        item_row["relevant"] = verdict
                        counts = judged.setdefault(item_row["source"], [0, 0])
                        counts[0] += int(verdict)
                        counts[1] += 1
                    out.write(
                        json.dumps(
                            {
                                "query": query,
                                "criteria": criteria.model_dump(
                                    mode="json", exclude={"semantic_query"}
                                ),
                                "candidates": result.candidates,
                                "kept": len(result.ranked),
                                "top": top,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
    finally:
        await disconnect()
    report = {
        "queries": len(understood),
        "searchable": sum(1 for _, c, _ in understood if c is not None),
        "dialogue_ms": _percentiles(dialogue_ms) if dialogue_ms else None,
        "search_total_ms": _percentiles(totals) if totals else None,
        **{stage: _percentiles(values) for stage, values in timed.samples.items()},
    }
    if judged:
        report["precision_at_10"] = {
            source: {"relevant": ok, "judged": n, "precision": round(ok / n, 3)}
            for source, (ok, n) in sorted(judged.items())
        }
        total_ok = sum(ok for ok, _ in judged.values())
        total_n = sum(n for _, n in judged.values())
        report["precision_at_10"]["all"] = {
            "relevant": total_ok,
            "judged": total_n,
            "precision": round(total_ok / total_n, 3) if total_n else None,
        }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
