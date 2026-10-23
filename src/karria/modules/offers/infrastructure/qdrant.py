"""L'index de recherche : Qdrant par son API REST, sans SDK.

Deux collections, nommées par environnement (`karria-production-offers`,
`karria-production-criteria`) :

- **offres** — trois vecteurs par point :
  - `semantic`, dense, cosinus, original sur disque et copie quantifiée
    int8 en mémoire (un million de points à 768 dimensions : ~0,8 Go de
    RAM au lieu de 3 Go) ;
  - `title` et `body`, creux, BM25 **calculé par Qdrant** (modèle
    `qdrant/bm25`, racinisation française, accents pliés), IDF appliquée
    côté serveur au moment de la requête ;
  - les attributs filtrables en payload indexé ;
- **critères** — le vecteur de la demande de chaque personne, un point
  par compte, et de quoi pré-filtrer par lieu, pour le matching inversé
  des alertes.

Les formes d'API (vecteurs nommés, `modifier: idf`, inférence BM25 par
`{"text", "model", "options"}`, `prefetch` + RRF pondérée, `geo_radius`,
quantification scalaire) ont été vérifiées sur le serveur en v1.20.0 le
2026-10-22. La segmentation est réglée **ici**, à la création : la V1
avait laissé 111 segments s'accumuler (`max_segment_size` trop bas) et l'a
payé en latence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol
from uuid import UUID

from karria.modules.offers.application import (
    CriteriaMatch,
    IndexedOffer,
    IndexQuery,
)
from karria.modules.offers.domain import (
    MAX_RADIUS_KM,
    Candidate,
    Offer,
    RemoteMode,
    SearchCriteria,
    SearchUnavailableError,
)
from karria.shared.config import QdrantSettings
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

DENSE: Final = "semantic"
TITLE: Final = "title"
BODY: Final = "body"
BM25_MODEL: Final = "qdrant/bm25"
#: Longueurs moyennes mesurées le 2026-10-22 sur 9 100 annonces réelles
#: (en mots) : elles calibrent la normalisation de longueur de BM25.
TITLE_AVG_WORDS: Final = 7
BODY_AVG_WORDS: Final = 330
#: Candidats demandés à chaque branche avant la fusion.
PREFETCH_LIMIT: Final = 200
#: Poids de chaque branche dans la fusion RRF : dense, intitulé, corps.
#: Posés sur l'évaluation du 2026-10-22 (`scripts/offers_eval.py`).
FUSION_WEIGHTS: Final = (1.0, 1.0, 0.5)
RRF_K: Final = 60
#: Exploration du graphe HNSW à la requête, et suréchantillonnage de la
#: quantification avant re-score sur les vecteurs originaux.
HNSW_EF: Final = 128
OVERSAMPLING: Final = 2.0
#: Champs de payload indexés de la collection des offres.
OFFER_PAYLOAD_INDEXES: Final[dict[str, str]] = {
    "active": "bool",
    "contract": "keyword",
    "remote": "keyword",
    "schedule": "keyword",
    "experience_rank": "integer",
    "salary_ceiling": "integer",
    "department": "keyword",
    "published_at": "integer",
    "location": "geo",
}
CRITERIA_PAYLOAD_INDEXES: Final[dict[str, str]] = {
    "alerts_enabled": "bool",
    "remote_anywhere": "bool",
    "scope": "keyword",
    "departments": "keyword",
    "location": "geo",
}
_NOT_FOUND: Final = 404


class JsonTransport(Protocol):
    """Ce que les adaptateurs attendent du client HTTP."""

    async def request_json(self, method: str, path: str, payload: Any = None) -> JsonResponse:
        """Une requête JSON de méthode quelconque."""
        ...


def build_transport(settings: QdrantSettings) -> JsonTransport:
    """Le client sortant du socle, configuré pour Qdrant."""
    key = settings.api_key.get_secret_value()
    return JsonClient(
        base_url=settings.url,
        headers={"api-key": key} if key else {},
        timeout_seconds=settings.request_timeout_seconds,
    )


def collection_name(settings: QdrantSettings, environment: str, kind: str) -> str:
    """`<préfixe>-<environnement>-<kind>`, comme les buckets."""
    return f"{settings.collection_prefix}-{environment}-{kind}"


def bm25_document(text: str, *, avg_words: int) -> dict[str, Any]:
    """Un texte que Qdrant transforme lui-même en vecteur BM25."""
    return {
        "text": text,
        "model": BM25_MODEL,
        "options": {"language": "french", "ascii_folding": True, "avg_len": avg_words},
    }


class _Collection:
    """Ce que les deux collections partagent : création idempotente, erreurs."""

    def __init__(self, transport: JsonTransport, name: str, dimension: int) -> None:
        self._http = transport
        self._name = name
        self._dimension = dimension

    async def _call(self, method: str, path: str, payload: Any = None) -> dict[str, Any]:
        try:
            response = await self._http.request_json(
                method, f"/collections/{self._name}{path}", payload
            )
            return response.mapping
        except OutboundError as exc:
            logger.warning(
                "qdrant.call_failed",
                collection=self._name,
                path=path,
                status=exc.status,
                detail=exc.detail,
            )
            raise SearchUnavailableError(
                "Qdrant", f"HTTP {exc.status}" if exc.status else "injoignable"
            ) from exc

    async def _exists(self) -> bool:
        try:
            await self._http.request_json("GET", f"/collections/{self._name}")
        except OutboundError as exc:
            if exc.status == _NOT_FOUND:
                return False
            raise SearchUnavailableError("Qdrant", exc.detail) from exc
        return True

    async def _ensure(self, config: dict[str, Any], indexes: Mapping[str, str]) -> None:
        if not await self._exists():
            await self._call("PUT", "", config)
            logger.info("qdrant.collection_created", collection=self._name)
        for field_name, schema in indexes.items():
            await self._call(
                "PUT", "/index?wait=true", {"field_name": field_name, "field_schema": schema}
            )

    def _dense_config(self) -> dict[str, Any]:
        return {
            "vectors": {DENSE: {"size": self._dimension, "distance": "Cosine", "on_disk": True}},
            "quantization_config": {
                "scalar": {"type": "int8", "quantile": 0.99, "always_ram": True}
            },
            "hnsw_config": {"m": 16, "ef_construct": 128, "on_disk": False},
            "optimizers_config": {"default_segment_number": 4},
            "on_disk_payload": True,
        }


class QdrantOfferIndex(_Collection):
    """Implémente `OfferIndex`."""

    async def ensure_ready(self) -> None:
        """Crée la collection, ses vecteurs et ses index de payload si besoin."""
        await self._ensure(
            {
                **self._dense_config(),
                "sparse_vectors": {TITLE: {"modifier": "idf"}, BODY: {"modifier": "idf"}},
            },
            OFFER_PAYLOAD_INDEXES,
        )

    async def upsert(self, items: Sequence[IndexedOffer]) -> None:
        """Écrit ces offres (un point par offre, identifié par son UUID)."""
        if items:
            await self._call(
                "PUT", "/points?wait=true", {"points": [_point(item) for item in items]}
            )

    async def update_payloads(self, offers: Sequence[Offer]) -> None:
        """Réécrit les attributs, une opération par offre, en un seul lot."""
        if not offers:
            return
        operations = [
            {"overwrite_payload": {"payload": offer_payload(offer), "points": [str(offer.id)]}}
            for offer in offers
        ]
        await self._call("POST", "/points/batch?wait=true", {"operations": operations})

    async def delete(self, offer_ids: Sequence[UUID]) -> None:
        """Retire ces points."""
        if offer_ids:
            await self._call(
                "POST", "/points/delete?wait=true", {"points": [str(i) for i in offer_ids]}
            )

    async def search(self, query: IndexQuery) -> Sequence[Candidate]:
        """Trois branches filtrées, une fusion RRF pondérée côté serveur."""
        conditions = filter_for(query.criteria)
        depth = max(PREFETCH_LIMIT, query.offset + query.limit)
        prefetch: list[dict[str, Any]] = [
            {
                "query": list(query.dense),
                "using": DENSE,
                "limit": depth,
                "filter": conditions,
                "params": {
                    "hnsw_ef": HNSW_EF,
                    "quantization": {"rescore": True, "oversampling": OVERSAMPLING},
                },
            }
        ]
        weights = [FUSION_WEIGHTS[0]]
        if query.title_terms.strip():
            prefetch.append(
                {
                    "query": bm25_document(query.title_terms, avg_words=TITLE_AVG_WORDS),
                    "using": TITLE,
                    "limit": depth,
                    "filter": conditions,
                }
            )
            weights.append(FUSION_WEIGHTS[1])
        if query.body_terms.strip():
            prefetch.append(
                {
                    "query": bm25_document(query.body_terms, avg_words=BODY_AVG_WORDS),
                    "using": BODY,
                    "limit": depth,
                    "filter": conditions,
                }
            )
            weights.append(FUSION_WEIGHTS[2])
        body = await self._call(
            "POST",
            "/points/query",
            {
                "prefetch": prefetch,
                "query": {"rrf": {"k": RRF_K, "weights": weights}},
                "limit": query.limit,
                "offset": query.offset,
                "with_payload": False,
            },
        )
        points = (body.get("result") or {}).get("points") or []
        return [
            Candidate(offer_id=UUID(str(p["id"])), retrieval_score=float(p.get("score", 0.0)))
            for p in points
        ]

    async def title_candidates(self, term: str, *, limit: int) -> Sequence[UUID]:
        """La branche lexicale d'intitulé seule, offres actives : le vocabulaire des titres réels."""
        body = await self._call(
            "POST",
            "/points/query",
            {
                "query": bm25_document(term, avg_words=TITLE_AVG_WORDS),
                "using": TITLE,
                "filter": {"must": [{"key": "active", "match": {"value": True}}]},
                "limit": limit,
                "with_payload": False,
            },
        )
        points = (body.get("result") or {}).get("points") or []
        return [UUID(str(p["id"])) for p in points]

    async def count(self) -> int:
        """Nombre de points, exact."""
        body = await self._call("POST", "/points/count", {"exact": True})
        return int((body.get("result") or {}).get("count", 0))


class QdrantCriteriaIndex(_Collection):
    """Implémente `CriteriaIndex`."""

    async def ensure_ready(self) -> None:
        """Crée la collection si besoin."""
        await self._ensure(self._dense_config(), CRITERIA_PAYLOAD_INDEXES)

    async def upsert(
        self,
        user_id: UUID,
        dense: Sequence[float],
        criteria: SearchCriteria,
        *,
        alerts_enabled: bool,
    ) -> None:
        """Un point par personne."""
        await self._call(
            "PUT",
            "/points?wait=true",
            {
                "points": [
                    {
                        "id": str(user_id),
                        "vector": {DENSE: list(dense)},
                        "payload": criteria_payload(criteria, alerts_enabled=alerts_enabled),
                    }
                ]
            },
        )

    async def delete(self, user_id: UUID) -> None:
        """Retire la personne."""
        await self._call("POST", "/points/delete?wait=true", {"points": [str(user_id)]})

    async def set_alerts_enabled(self, user_id: UUID, *, enabled: bool) -> None:
        """Ne touche qu'au payload."""
        await self._call(
            "POST",
            "/points/payload?wait=true",
            {"payload": {"alerts_enabled": enabled}, "points": [str(user_id)]},
        )

    async def similar(
        self, dense: Sequence[float], offer: Offer, *, limit: int, min_similarity: float
    ) -> Sequence[CriteriaMatch]:
        """Les personnes dont la demande ressemble à cette offre et couvre son lieu."""
        must: list[dict[str, Any]] = [{"key": "alerts_enabled", "match": {"value": True}}]
        must.append(criteria_place_filter(offer))
        body = await self._call(
            "POST",
            "/points/query",
            {
                "query": list(dense),
                "using": DENSE,
                "filter": {"must": must},
                "limit": limit,
                "score_threshold": min_similarity,
                "params": {"quantization": {"rescore": True}},
                "with_payload": False,
            },
        )
        points = (body.get("result") or {}).get("points") or []
        return [
            CriteriaMatch(user_id=UUID(str(p["id"])), similarity=float(p["score"])) for p in points
        ]


def offer_payload(offer: Offer) -> dict[str, Any]:
    """Les attributs filtrables d'une offre ; une information absente n'est pas écrite."""
    location = offer.location
    payload: dict[str, Any] = {
        "active": offer.indexable,
        "published_at": int(offer.published_at.timestamp()),
        "contract": offer.contract_type.value if offer.contract_type else None,
        "remote": offer.remote.value if offer.remote else None,
        "schedule": offer.schedule.value if offer.schedule else None,
        "experience_rank": offer.experience.rank if offer.experience else None,
        "salary_ceiling": offer.salary.max_monthly or offer.salary.min_monthly,
        "department": location.department_code if location else None,
        "city": location.city if location else None,
    }
    if location is not None and location.is_geocoded:
        payload["location"] = {"lat": location.latitude, "lon": location.longitude}
    return {k: v for k, v in payload.items() if v is not None}


def _point(item: IndexedOffer) -> dict[str, Any]:
    return {
        "id": str(item.offer.id),
        "vector": {
            DENSE: list(item.dense),
            TITLE: bm25_document(item.title_text, avg_words=TITLE_AVG_WORDS),
            BODY: bm25_document(item.body_text, avg_words=BODY_AVG_WORDS),
        },
        "payload": offer_payload(item.offer),
    }


def _or_empty(key: str, condition: dict[str, Any]) -> dict[str, Any]:
    """La condition, ou l'absence de l'attribut : une offre muette n'est pas éliminée."""
    return {"should": [condition, {"is_empty": {"key": key}}]}


def filter_for(criteria: SearchCriteria) -> dict[str, Any]:
    """Traduit les contraintes dures en filtre Qdrant.

    Les deux règles — ce filtre et `SearchCriteria.accepts` — doivent dire
    la même chose, sinon l'index écarte ce que le domaine accepterait, ou
    rend des candidats que le domaine jettera (et le fil maigrit sans
    raison visible). Un test les confronte offre par offre.
    """
    must: list[dict[str, Any]] = [{"key": "active", "match": {"value": True}}]
    if criteria.contract_types:
        wanted = {"key": "contract", "match": {"any": [c.value for c in criteria.contract_types]}}
        must.append(wanted if criteria.strict_contracts else _or_empty("contract", wanted))
    if criteria.wants_remote_only:
        must.append({"key": "remote", "match": {"value": RemoteMode.REMOTE.value}})
    elif criteria.remote:
        must.append(
            _or_empty(
                "remote", {"key": "remote", "match": {"any": [r.value for r in criteria.remote]}}
            )
        )
    if criteria.schedule is not None:
        wanted = {"key": "schedule", "match": {"value": criteria.schedule.value}}
        must.append(wanted if criteria.strict_schedule else _or_empty("schedule", wanted))
    if criteria.max_experience is not None:
        must.append(
            _or_empty(
                "experience_rank",
                {"key": "experience_rank", "range": {"lte": criteria.max_experience.rank}},
            )
        )
    if criteria.salary_min_monthly:
        must.append(
            _or_empty(
                "salary_ceiling",
                {"key": "salary_ceiling", "range": {"gte": criteria.salary_min_monthly}},
            )
        )
    place = _place_condition(criteria)
    if place is not None:
        must.append(place)
    return {"must": must}


def _place_condition(criteria: SearchCriteria) -> dict[str, Any] | None:
    if criteria.whole_france or criteria.wants_remote_only or criteria.location is None:
        return None
    location = criteria.location
    alternatives: list[dict[str, Any]] = (
        [{"key": "remote", "match": {"value": RemoteMode.REMOTE.value}}]
        if criteria.remote_anywhere
        else []
    )
    if location.department_codes:
        alternatives.append({"key": "department", "match": {"any": location.department_codes}})
    if location.is_geocoded:
        alternatives.append(
            {
                "key": "location",
                "geo_radius": {
                    "center": {"lat": location.latitude, "lon": location.longitude},
                    "radius": location.radius_km * 1_000,
                },
            }
        )
    if len(alternatives) == (1 if criteria.remote_anywhere else 0):
        return None
    return {"should": alternatives}


def criteria_payload(criteria: SearchCriteria, *, alerts_enabled: bool) -> dict[str, Any]:
    """Ce qui permet de pré-filtrer les critères par le lieu d'une offre."""
    payload: dict[str, Any] = {
        "alerts_enabled": alerts_enabled,
        "remote_anywhere": criteria.remote_anywhere,
    }
    if criteria.whole_france:
        payload["scope"] = "france"
    elif criteria.wants_remote_only:
        payload["scope"] = "remote"
    else:
        payload["scope"] = "place"
        location = criteria.location
        if location is not None:
            payload["departments"] = location.department_codes
            if location.is_geocoded:
                payload["location"] = {"lat": location.latitude, "lon": location.longitude}
    return payload


def criteria_place_filter(offer: Offer) -> dict[str, Any]:
    """Les critères qui **peuvent** couvrir le lieu de cette offre (filtre large).

    Les recherches « toute la France », celles qui visent son département,
    et celles dont le centre est à moins du rayon maximal ; pour une offre
    en télétravail complet, en plus, les recherches « en télétravail » et
    celles qui l'acceptent où qu'il soit. Le rayon exact de chacun est
    vérifié ensuite par le domaine.
    """
    alternatives: list[dict[str, Any]] = [{"key": "scope", "match": {"value": "france"}}]
    if offer.remote is RemoteMode.REMOTE:
        alternatives += [
            {"key": "scope", "match": {"value": "remote"}},
            {"key": "remote_anywhere", "match": {"value": True}},
        ]
    location = offer.location
    if location is not None and location.department_code:
        alternatives.append({"key": "departments", "match": {"value": location.department_code}})
    if location is not None and location.is_geocoded:
        alternatives.append(
            {
                "key": "location",
                "geo_radius": {
                    "center": {"lat": location.latitude, "lon": location.longitude},
                    "radius": MAX_RADIUS_KM * 1_000,
                },
            }
        )
    return {"should": alternatives}
