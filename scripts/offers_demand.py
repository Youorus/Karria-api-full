"""Le planificateur de collecte, vu de l'exploitation : semer une demande, lire le catalogue, l'ordre du jour.

    uv run python -m scripts.offers_demand seed [--queries N]     # N demandes réelles → critères (Gemini) → fil, cibles, liens
    uv run python -m scripts.offers_demand complex                # les demandes longues et touffues : critères et cibles, pour juger
    uv run python -m scripts.offers_demand catalogue [--limit 60] # le catalogue des cibles et ce qu'elles ont appris
    uv run python -m scripts.offers_demand plan [--limit 30]      # ce que le collecteur recevrait maintenant, par priorité

`seed` écrit en base (critères, fil, cibles, liens de demande) pour des
personnes **fictives** (identifiants déterministes) : c'est l'outil de
l'environnement d'évaluation, jamais de la production. Les autres
commandes ne font que lire. Aucune commande ne parle à un site : une
demande ne déclenche jamais une collecte en direct — elle rend des cibles
dues, que le collecteur prendra à son passage (ADR-0016 bis).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import uuid
from datetime import UTC, datetime

from scripts.offers_eval import QUERIES

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.offers import build_apply_criteria, build_lease_targets
from karria.modules.offers.api.dependencies import get_search_dialogue, get_target_repository
from karria.modules.offers.domain import (
    DialogueStatus,
    SearchCriteria,
    candidate_terms,
    decide,
    demand_scores,
    priority,
    targets_of,
)
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.llm import build_language_model
from karria.shared.logging import configure_logging

#: Des demandes telles qu'on les reçoit vraiment : longues, mêlées, avec des
#: préférences qui ne sont pas des filtres. Le planificateur doit en tirer
#: des termes courts, et rien d'autre ne doit partir vers un site.
COMPLEX_QUERIES: tuple[str, ...] = (
    "Bonjour, alors moi je suis serveuse depuis 6 ans mais j'en ai un peu marre des coupures, je cherche "
    "soit du service en salle soit un poste de barmaid, à Lyon ou pas trop loin (je suis en vélo), "
    "idéalement une équipe sympa et pas un patron qui crie. Pas de temps partiel, j'ai un loyer.",
    "Je viens de finir mon BTS compta et je voudrais une alternance pour mon DCG à partir de septembre, "
    "sur Bordeaux, dans un cabinet ou en entreprise peu importe, par contre je n'ai pas le permis donc "
    "il faut que ce soit accessible en tram.",
    "Développeur Python / Django 5 ans d'expérience, je veux du télétravail complet ou presque, "
    "une startup ou une PME à taille humaine, pas de SSII, salaire 50k minimum, et je suis basé à "
    "Nantes mais ça n'a pas d'importance si c'est full remote.",
    "Mon mari et moi on déménage à Toulouse cet été. Je suis aide-soignante de nuit en EHPAD, j'aimerais "
    "passer en service de jour, CDI, et si possible dans le public. Je peux commencer en août.",
    "jsuis cariste caces 1 3 5 je cherche de l'interim ou un cdd sur lille et alentours 20km max, "
    "horaires de journée svp pas de nuit, et je prends aussi prépa de commandes si c'est bien payé",
    "Actuellement responsable de rayon en grande distribution, je souhaite évoluer vers un poste de "
    "chef de secteur ou directeur adjoint de magasin, en Île-de-France, avec une vraie perspective "
    "d'évolution et un management bienveillant. Pas de discount.",
    "je cherche quelque chose dans la vente, pas trop stressant, à Marseille",
    "Infirmière libérale pendant 10 ans, je veux revenir au salariat : CDI en clinique ou en hôpital à "
    "Montpellier ou Nîmes, pas de nuits, et un salaire au moins à 2 600 brut. Je suis aussi ouverte à "
    "un poste de cadre de santé si on me forme.",
    "Étudiant en L3 info à Grenoble, je cherche un stage de 2 à 3 mois cet été en data ou en dev, ou "
    "sinon un job étudiant le week-end dans la restauration pour payer mon loyer. Les deux m'intéressent.",
    "Électricien du bâtiment, 15 ans de métier, j'en ai assez des chantiers à 2 h de route. Je veux un "
    "poste de technicien de maintenance en usine ou en site tertiaire autour de Rennes, en CDI, avec "
    "des astreintes payées. Je refuse l'intérim.",
    "Je suis un peu perdu, j'ai fait de la comm, de l'événementiel et un peu de marketing digital, je "
    "voudrais un CDI ou un CDD long à Paris dans une boîte qui a du sens (ESS, culture, asso), "
    "chargé de communication ou chef de projet événementiel, télétravail 2 jours par semaine.",
    "cuisinier en temps plein dans toute la France",
)


def _user(index: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"karria-eval-demand-{index}")


async def _understand(query: str) -> tuple[SearchCriteria | None, str]:
    """Un tour : Gemini comprend, le lieu est géocodé, le domaine décide — comme `ConverseSearch`."""
    settings = get_settings()
    proposal = await get_search_dialogue(build_language_model(settings)).propose(
        history=(), current=None, message=query
    )
    criteria = proposal.criteria
    if criteria is not None:
        criteria = await build_apply_criteria(settings).locate(criteria)
    outcome = decide(dataclasses.replace(proposal, criteria=criteria), None)
    if outcome.status is not DialogueStatus.SEARCH and outcome.criteria is not None:
        # Pour juger : ce qui manquait, et ce que le modèle avait compris.
        return (
            None,
            f"{outcome.reply} [manque : {outcome.criteria.missing()} ; compris : {outcome.criteria.model_dump(mode='json', include={'job_titles', 'search_terms', 'location', 'whole_france'})}]",
        )
    return (outcome.criteria if outcome.status is DialogueStatus.SEARCH else None), outcome.reply


def _targets_of(criteria: SearchCriteria) -> list[dict[str, object]]:
    return [
        {
            "key": t.key,
            "term": t.query_term,
            "location": t.location,
            "distance_km": t.distance_km,
        }
        for t in targets_of(criteria)
    ]


async def _seed(count: int) -> None:
    settings = get_settings()
    apply = build_apply_criteria(settings)
    for index, query in enumerate(QUERIES[:count]):
        criteria, reply = await _understand(query)
        if criteria is None:
            print(
                json.dumps(
                    {"query": query, "status": "clarify", "reply": reply}, ensure_ascii=False
                )
            )
            continue
        applied = await apply.execute(_user(index), criteria)
        print(
            json.dumps(
                {
                    "query": query,
                    "user": str(_user(index)),
                    "results": applied.results,
                    "terms": candidate_terms(criteria),
                    "targets": [t["key"] for t in _targets_of(criteria)],
                },
                ensure_ascii=False,
            )
        )


async def _complex() -> None:
    for query in COMPLEX_QUERIES:
        criteria, reply = await _understand(query)
        row: dict[str, object] = {"query": query, "reply": reply}
        if criteria is None:
            row["status"] = "clarify"
        else:
            row["status"] = "search"
            row["criteria"] = criteria.model_dump(mode="json", exclude={"semantic_query", "query"})
            row["terms"] = candidate_terms(criteria)
            row["targets"] = _targets_of(criteria)
        print(json.dumps(row, ensure_ascii=False))


async def _catalogue(limit: int) -> None:
    targets = get_target_repository()
    now = datetime.now(UTC)
    demand = demand_scores(await targets.all_demands(), now)
    for stored in await targets.catalogue(limit=limit):
        print(
            json.dumps(
                {
                    "key": stored.key,
                    "term": stored.target.query_term,
                    "location": stored.target.location,
                    "origin": stored.target.origin.value,
                    "state": stored.stats.state.value,
                    "people": stored.target.weight,
                    "demand": round(demand.get(stored.key, 0.0), 2),
                    "scrapes": stored.stats.scrapes,
                    "yield_ema": stored.stats.yield_ema,
                    "overlap_ft": stored.stats.overlap_ema,
                    "zero_streak": stored.stats.zero_streak,
                    "next_at": stored.next_scrape_at.isoformat() if stored.next_scrape_at else None,
                    "priority": priority(
                        stored.target,
                        stored.stats,
                        demand=demand.get(stored.key, 0.0),
                        thin_since=stored.thin_since,
                        last_scraped_at=stored.last_scraped_at,
                        now=now,
                    ),
                },
                ensure_ascii=False,
            )
        )


async def _plan(limit: int) -> None:
    """Ce que `GET /internal/offers/targets` rendrait — sans louer (lecture seule)."""
    targets = get_target_repository()
    now = datetime.now(UTC)
    demand = demand_scores(await targets.all_demands(), now)
    due = list(await targets.due(now=now, limit=limit * 4))
    due.sort(
        key=lambda stored: priority(
            stored.target,
            stored.stats,
            demand=demand.get(stored.key, 0.0),
            thin_since=stored.thin_since,
            last_scraped_at=stored.last_scraped_at,
            now=now,
        ),
        reverse=True,
    )
    for rank, stored in enumerate(due[:limit], start=1):
        print(
            json.dumps(
                {
                    "rank": rank,
                    "key": stored.key,
                    "term": stored.target.query_term,
                    "location": stored.target.location,
                    "state": stored.stats.state.value,
                    "demand": round(demand.get(stored.key, 0.0), 2),
                    "thin": stored.thin_since is not None,
                },
                ensure_ascii=False,
            )
        )
    _ = build_lease_targets  # la vraie location passe par la route interne


async def main(argv: list[str]) -> int:
    """Analyse la ligne de commande et lance la commande demandée."""
    parser = argparse.ArgumentParser(prog="scripts.offers_demand")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("--queries", type=int, default=len(QUERIES))
    commands.add_parser("complex")
    catalogue = commands.add_parser("catalogue")
    catalogue.add_argument("--limit", type=int, default=60)
    plan = commands.add_parser("plan")
    plan.add_argument("--limit", type=int, default=30)
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.observability)
    await connect(TORTOISE_ORM)
    try:
        match args.command:
            case "seed":
                await _seed(args.queries)
            case "complex":
                await _complex()
            case "catalogue":
                await _catalogue(args.limit)
            case "plan":
                await _plan(args.limit)
    finally:
        await disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
