"""Le géocodeur : la Base Adresse Nationale, derrière un cache en base.

La BAN est publique, française, sans clé, et limite à ~50 requêtes par
seconde ; une collecte en pose des milliers par passage, dont l'immense
majorité répètent les mêmes villes. Le cache rend chaque libellé distinct
une seule fois coûteux, échec compris (une requête que la BAN ne reconnaît
pas ne se repose pas).
"""

from __future__ import annotations

from typing import Any, Final, Protocol

from karria.modules.offers.domain import GeocodedPlace, department_from_postal, fold
from karria.shared.config import GeocodingSettings
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError
from karria.shared.logging import get_logger

logger = get_logger(__name__)


class JsonGetter(Protocol):
    """Ce que l'adaptateur attend du client HTTP."""

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> JsonResponse:
        """Un `GET` JSON."""
        ...


class GeocodeCache(Protocol):
    """Interne à l'infrastructure : la mémoire des requêtes déjà posées."""

    async def lookup(self, query: str) -> tuple[bool, GeocodedPlace | None]:
        """`(connu, lieu)` : connu et lieu `None` veut dire « déjà cherché, rien trouvé »."""
        ...

    async def remember(self, query: str, place: GeocodedPlace | None) -> None:
        """Mémorise le résultat, positif ou négatif."""
        ...


class BanGeocoder:
    """Implémente `Geocoder` sur `/search/` de la BAN."""

    def __init__(self, http: JsonGetter) -> None:
        self._http = http

    @classmethod
    def from_settings(cls, settings: GeocodingSettings) -> BanGeocoder:
        """Le client du socle, pointé sur la BAN (ou un miroir)."""
        return cls(
            JsonClient(
                base_url=settings.ban_url,
                timeout_seconds=settings.request_timeout_seconds,
                attempts=2,
            )
        )

    async def geocode(self, query: str) -> GeocodedPlace | None:
        """La première commune qui correspond, ou `None`."""
        if not acceptable_query(query):
            return None
        try:
            response = await self._http.get_json(
                "/search/", {"q": query, "limit": "1", "type": "municipality"}
            )
        except OutboundError as exc:
            logger.warning("geocoding.failed", status=exc.status, detail=exc.detail)
            return None
        return parse_feature(response.mapping)


#: La BAN refuse une requête de moins de trois caractères, de plus de deux
#: cents, ou qui ne commence ni par une lettre ni par un chiffre (HTTP 400).
_QUERY_MIN, _QUERY_MAX = 3, 200


def acceptable_query(query: str) -> bool:
    """La BAN acceptera-t-elle cette requête ? Inutile de l'appeler sinon."""
    text = query.strip()
    return _QUERY_MIN <= len(text) <= _QUERY_MAX and text[0].isalnum()


def parse_feature(body: dict[str, Any]) -> GeocodedPlace | None:
    """Lit la première `feature` GeoJSON d'une réponse de la BAN."""
    features = body.get("features") or []
    if not features:
        return None
    feature = features[0]
    properties = feature.get("properties") or {}
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    city = properties.get("city") or properties.get("name")
    if not city or len(coordinates) != 2:  # noqa: PLR2004
        return None
    postal = properties.get("postcode")
    context = str(properties.get("context") or "")
    department = context.split(",", maxsplit=1)[0].strip() if context else None
    if not department and postal:
        department = department_from_postal(str(postal))
    return GeocodedPlace(
        city=str(city),
        postal_code=str(postal) if postal else None,
        department_code=department or None,
        latitude=float(coordinates[1]),
        longitude=float(coordinates[0]),
    )


#: Libellés gardés en mémoire par processus : une collecte en revoit
#: quelques centaines, toujours les mêmes villes.
MEMORY_ENTRIES: Final = 20_000


class CachedGeocoder:
    """Implémente `Geocoder` : la mémoire, puis la base, puis la BAN ; le résultat retenu.

    Deux étages de cache : la base survit aux redémarrages et se partage
    entre processus ; la mémoire évite l'aller-retour SQL pour les villes
    revues cent fois par passage.
    """

    def __init__(self, inner: BanGeocoder, cache: GeocodeCache) -> None:
        self._inner = inner
        self._cache = cache
        self._memory: dict[str, GeocodedPlace | None] = {}

    async def geocode(self, query: str) -> GeocodedPlace | None:
        """Même requête, même réponse — sans réseau la deuxième fois."""
        key = fold(query)[:255]
        if not key:
            return None
        if key in self._memory:
            return self._memory[key]
        known, place = await self._cache.lookup(key)
        if not known:
            place = await self._inner.geocode(query)
            await self._cache.remember(key, place)
        if len(self._memory) >= MEMORY_ENTRIES:
            self._memory.clear()
        self._memory[key] = place
        return place
