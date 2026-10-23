"""Plongements : un serveur Text Embeddings Inference, en REST.

Modèles retenus le 2026-10-22 après mesure sur le serveur (8 vCPU EPYC
Milan à 2 GHz, AVX2, sans AVX-512 ; détail dans l'ADR-0016) :

- `intfloat/multilingual-e5-base` (768 dimensions), **quantifié int8**
  (ONNX, quantification dynamique par canal) : 15,3 annonces/s à ~600
  caractères contre 9,7/s en fp32 ; une requête en 9 ms. Les modèles E5
  attendent les préfixes `query: ` / `passage: ` ; sans eux, la
  similarité se dégrade.
- aucun reclasseur : `mmarco-mMiniLMv2` (rapide) se trompe sur des
  intitulés courts, `bge-reranker-v2-m3` (juste) lit 40 intitulés en
  6,7 s ; la porte d'intitulé du domaine les remplace (ADR-0016).

Le serveur ONNX de TEI plafonne à huit textes par passe
(`max_batch_requests=8`) : les documents partent donc par paquets de huit,
plusieurs paquets en parallèle, ce qui occupe tous les cœurs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Final, Protocol

from karria.modules.offers.domain import SearchUnavailableError
from karria.shared.config import InferenceSettings
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Les familles de modèles qui attendent un préfixe de rôle.
_PREFIXED_FAMILIES: Final = ("e5",)


class JsonPoster(Protocol):
    """Ce que les adaptateurs attendent du client HTTP."""

    async def post_json(self, path: str, payload: Any) -> JsonResponse:
        """Un `POST` JSON."""
        ...


def needs_prefixes(model: str) -> bool:
    """Le modèle attend-il `query: ` et `passage: ` ?"""
    return any(family in model.lower() for family in _PREFIXED_FAMILIES)


class TeiEmbedder:
    """Implémente `TextEmbedder` sur `/embed`."""

    def __init__(
        self, http: JsonPoster, *, model: str, batch_size: int, concurrency: int = 1
    ) -> None:
        self._http = http
        self._model = model
        self._batch_size = max(1, batch_size)
        self._concurrency = max(1, concurrency)
        self._prefixed = needs_prefixes(model)

    @classmethod
    def from_settings(cls, settings: InferenceSettings) -> TeiEmbedder:
        """Le client du socle, pointé sur le serveur de plongements."""
        return cls(
            JsonClient(
                base_url=settings.embeddings_url, timeout_seconds=settings.request_timeout_seconds
            ),
            model=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            concurrency=settings.embedding_concurrency,
        )

    @property
    def version(self) -> str:
        """Le nom du modèle."""
        return self._model

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        """Par paquets de `batch_size`, `concurrency` paquets à la fois, dans l'ordre."""
        prefix = "passage: " if self._prefixed else ""
        batches = [
            [f"{prefix}{t}" for t in texts[start : start + self._batch_size]]
            for start in range(0, len(texts), self._batch_size)
        ]
        gate = asyncio.Semaphore(self._concurrency)

        async def run(batch: list[str]) -> list[tuple[float, ...]]:
            async with gate:
                return await self._embed(batch)

        results = await asyncio.gather(*(run(batch) for batch in batches))
        return [vector for batch in results for vector in batch]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Une requête, avec son préfixe."""
        prefix = "query: " if self._prefixed else ""
        return (await self._embed([f"{prefix}{text}"]))[0]

    async def _embed(self, inputs: list[str]) -> list[tuple[float, ...]]:
        try:
            response = await self._http.post_json(
                "/embed", {"inputs": inputs, "truncate": True, "normalize": True}
            )
        except OutboundError as exc:
            logger.warning("inference.embed_failed", status=exc.status, detail=exc.detail)
            raise SearchUnavailableError("embeddings", exc.detail) from exc
        body = response.body
        if not isinstance(body, list) or len(body) != len(inputs):
            raise SearchUnavailableError("embeddings", "réponse inattendue")
        return [tuple(float(x) for x in vector) for vector in body]
