"""Le géocodeur : la BAN, ses refus, et ses deux étages de cache."""

from __future__ import annotations

from typing import Any

import pytest

from karria.modules.offers.domain import GeocodedPlace
from karria.modules.offers.infrastructure import geocoding
from karria.modules.offers.infrastructure.geocoding import (
    BanGeocoder,
    CachedGeocoder,
    acceptable_query,
    parse_feature,
)
from karria.shared.config import GeocodingSettings
from karria.shared.http.outbound import JsonResponse, OutboundError

pytestmark = pytest.mark.unit

LYON = {
    "features": [
        {
            "properties": {"city": "Lyon", "postcode": "69003", "context": "69, Rhône, Auvergne"},
            "geometry": {"coordinates": [4.85, 45.76]},
        }
    ]
}
PLACE = GeocodedPlace(
    city="Lyon", postal_code="69003", department_code="69", latitude=45.76, longitude=4.85
)


class FakeGetter:
    def __init__(self, *responses: JsonResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> JsonResponse:
        self.calls.append((path, params))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MemoryCache:
    def __init__(self) -> None:
        self.rows: dict[str, GeocodedPlace | None] = {}
        self.lookups = 0

    async def lookup(self, query: str) -> tuple[bool, GeocodedPlace | None]:
        self.lookups += 1
        return (query in self.rows), self.rows.get(query)

    async def remember(self, query: str, place: GeocodedPlace | None) -> None:
        self.rows[query] = place


class TestBan:
    async def test_la_premiere_commune_est_rendue(self) -> None:
        http = FakeGetter(JsonResponse(200, LYON))

        assert await BanGeocoder(http).geocode("69003 Lyon") == PLACE
        assert http.calls == [
            ("/search/", {"q": "69003 Lyon", "limit": "1", "type": "municipality"})
        ]

    async def test_une_requete_que_la_ban_refuserait_n_est_pas_envoyee(self) -> None:
        http = FakeGetter()

        assert await BanGeocoder(http).geocode("75") is None
        assert http.calls == []

    async def test_une_panne_rend_none(self) -> None:
        assert await BanGeocoder(FakeGetter(OutboundError("boom"))).geocode("Lyon") is None

    def test_la_configuration_regle_le_client(self) -> None:
        geocoder = BanGeocoder.from_settings(GeocodingSettings(ban_url="http://ban"))
        assert geocoder._http.base_url == "http://ban"  # type: ignore[attr-defined]


class TestRequeteAcceptable:
    @pytest.mark.parametrize(
        ("query", "ok"), [("Lyon", True), ("75", False), ("-Lyon", False), ("x" * 201, False)]
    )
    def test_les_regles_de_la_ban(self, query: str, ok: bool) -> None:
        assert acceptable_query(query) is ok


class TestLecture:
    def test_sans_resultat(self) -> None:
        assert parse_feature({"features": []}) is None

    def test_sans_ville_ni_coordonnees(self) -> None:
        assert parse_feature({"features": [{"properties": {}, "geometry": {}}]}) is None

    def test_le_departement_vient_du_code_postal_sans_contexte(self) -> None:
        body: dict[str, Any] = {
            "features": [
                {
                    "properties": {"name": "Ajaccio", "postcode": "20000"},
                    "geometry": {"coordinates": [8.7, 41.9]},
                }
            ]
        }
        place = parse_feature(body)
        assert place is not None
        assert (place.city, place.department_code) == ("Ajaccio", "2A")

    def test_sans_code_postal_ni_contexte_le_departement_est_inconnu(self) -> None:
        body: dict[str, Any] = {
            "features": [{"properties": {"city": "X"}, "geometry": {"coordinates": [1, 2]}}]
        }
        place = parse_feature(body)
        assert place is not None
        assert (place.postal_code, place.department_code) == (None, None)


class TestCache:
    async def test_la_ban_n_est_appelee_qu_une_fois_par_libelle(self) -> None:
        http = FakeGetter(JsonResponse(200, LYON))
        cache = MemoryCache()
        geocoder = CachedGeocoder(BanGeocoder(http), cache)

        assert await geocoder.geocode("Lyon") == PLACE
        assert await geocoder.geocode("LYON") == PLACE

        assert len(http.calls) == 1
        assert cache.lookups == 1
        assert cache.rows == {"lyon": PLACE}

    async def test_un_libelle_connu_en_base_evite_la_ban(self) -> None:
        cache = MemoryCache()
        cache.rows["lyon"] = None
        http = FakeGetter()

        assert await CachedGeocoder(BanGeocoder(http), cache).geocode("Lyon") is None
        assert http.calls == []

    async def test_un_libelle_vide_ne_coute_rien(self) -> None:
        cache = MemoryCache()

        assert await CachedGeocoder(BanGeocoder(FakeGetter()), cache).geocode("  ") is None
        assert cache.lookups == 0

    async def test_la_memoire_se_vide_quand_elle_deborde(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(geocoding, "MEMORY_ENTRIES", 1)
        cache = MemoryCache()
        cache.rows.update({"lyon": PLACE, "paris": None})
        geocoder = CachedGeocoder(BanGeocoder(FakeGetter()), cache)

        await geocoder.geocode("Lyon")
        await geocoder.geocode("Paris")
        await geocoder.geocode("Lyon")

        assert cache.lookups == 3
