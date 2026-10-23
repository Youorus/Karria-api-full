"""Les adaptateurs d'`offers` contre les vrais services — ignorés par défaut.

Lancer explicitement, services joignables (tunnel SSH vers le serveur, ou
conteneur sur `dokploy-network`), le `.env` étant coupé par la suite :

    OFFERS_LIVE=1 \\
    QDRANT__URL=http://localhost:6333 QDRANT__API_KEY=… \\
    INFERENCE__EMBEDDINGS_URL=http://localhost:8080 \\
    GEMINI__API_KEY=… \\
    uv run pytest tests/integration/test_offers_services_live.py

Ils vérifient que les formes d'API (Qdrant v1.20 : vecteurs nommés, BM25
côté serveur, RRF pondérée ; TEI : `/embed` ; Gemini : schéma
imposé) tiennent encore face aux services, pas la logique — celle-ci est
couverte sans réseau. Une collection jetable est créée puis supprimée.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from karria.modules.offers.application import IndexedOffer, IndexQuery
from karria.modules.offers.domain import (
    Company,
    ContractType,
    Location,
    LocationCriteria,
    Offer,
    Salary,
    SearchCriteria,
    Source,
    body_text,
    embedding_text,
    title_text,
)
from karria.modules.offers.infrastructure import (
    LlmDialogue,
    QdrantOfferIndex,
    TeiEmbedder,
    build_transport,
)
from karria.shared.config import Settings
from karria.shared.llm import build_language_model

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("OFFERS_LIVE") != "1", reason="Services réels : OFFERS_LIVE=1."
    ),
]

NOW = datetime.now(UTC)


def offer(title: str, city: str, lat: float, lon: float) -> Offer:
    return Offer(
        id=uuid4(),
        source=Source.MANUAL,
        source_id=uuid4().hex,
        title=title,
        company=Company(name="Essai"),
        description=f"{title} : poste à pourvoir immédiatement, équipe accueillante.",
        location=Location(label=city, city=city, latitude=lat, longitude=lon),
        contract_type=ContractType.CDI,
        remote=None,
        schedule=None,
        experience=None,
        salary=Salary(min_monthly=None, max_monthly=None, raw=None),
        published_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        apply_url=None,
        origin_url=None,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
async def index(settings: Settings) -> AsyncIterator[QdrantOfferIndex]:
    transport = build_transport(settings.qdrant)
    name = f"karria-live-{uuid4().hex[:8]}"
    collection = QdrantOfferIndex(transport, name, settings.inference.embedding_dimension)
    await collection.ensure_ready()
    try:
        yield collection
    finally:
        await transport.request_json("DELETE", f"/collections/{name}")


async def test_les_plongements_ont_la_dimension_configuree(settings: Settings) -> None:
    embedder = TeiEmbedder.from_settings(settings.inference)

    vectors = await embedder.embed_documents(["Serveur H/F à Paris", "Développeur Python"])

    assert [len(v) for v in vectors] == [settings.inference.embedding_dimension] * 2


async def test_la_recherche_hybride_filtre_par_lieu(
    settings: Settings, index: QdrantOfferIndex
) -> None:
    embedder = TeiEmbedder.from_settings(settings.inference)
    paris = offer("Serveur H/F", "Paris", 48.8566, 2.3522)
    lyon = offer("Serveur H/F", "Lyon", 45.764, 4.8357)
    vectors = await embedder.embed_documents([embedding_text(paris), embedding_text(lyon)])
    await index.upsert(
        [
            IndexedOffer(offer=o, dense=tuple(v), title_text=title_text(o), body_text=body_text(o))
            for o, v in zip([paris, lyon], vectors, strict=True)
        ]
    )
    criteria = SearchCriteria(
        query="serveur à Paris",
        job_titles=["serveur"],
        location=LocationCriteria(label="Paris", latitude=48.8566, longitude=2.3522),
    )

    candidates = await index.search(
        IndexQuery(
            dense=await embedder.embed_query("serveur à Paris"),
            title_terms="serveur",
            body_terms="serveur",
            criteria=criteria,
            limit=10,
        )
    )

    assert [c.offer_id for c in candidates] == [paris.id]
    assert await index.count() == 2


async def test_le_dialogue_route_comprend_une_demande_simple(settings: Settings) -> None:
    proposal = await LlmDialogue(build_language_model(settings)).propose(
        history=[], current=None, message="serveur à Paris en CDI"
    )

    assert proposal.criteria is not None
    assert proposal.criteria.contract_types == [ContractType.CDI]
    assert any("serveu" in title.lower() for title in proposal.criteria.job_titles)
