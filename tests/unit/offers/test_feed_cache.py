"""Le total du fil et ses options de filtre ne se recomptent pas à chaque ouverture."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from karria.modules.offers.application.ports import OfferFilters
from karria.modules.offers.infrastructure import repositories
from karria.modules.offers.infrastructure.repositories import _feed_total, forget_feed_totals

pytestmark = pytest.mark.unit


class CountedQuery:
    """Une requête dont on compte les `count()`."""

    def __init__(self, total: int) -> None:
        self.total, self.counts = total, 0

    def filter(self, **_: Any) -> CountedQuery:
        return self

    def exclude(self, **_: Any) -> CountedQuery:
        return self

    async def count(self) -> int:
        self.counts += 1
        await asyncio.sleep(0)  # laisse passer les autres requêtes, comme une vraie base
        return self.total


@pytest.fixture
def query(monkeypatch: pytest.MonkeyPatch) -> CountedQuery:
    forget_feed_totals()
    found = CountedQuery(540_000)
    monkeypatch.setattr(repositories.OfferModel, "filter", lambda **_: found)
    return found


class TestTotalDuFil:
    async def test_le_total_se_compte_une_fois_par_fenetre(self, query: CountedQuery) -> None:
        for _ in range(50):
            assert await _feed_total(query, OfferFilters()) == 540_000
        assert query.counts == 1

    async def test_trente_requetes_simultanees_ne_comptent_quune_fois(
        self, query: CountedQuery
    ) -> None:
        totals = await asyncio.gather(*(_feed_total(query, OfferFilters()) for _ in range(30)))
        assert set(totals) == {540_000}
        assert query.counts == 1

    async def test_les_offres_ecartees_se_retranchent_sans_recompter(
        self, query: CountedQuery
    ) -> None:
        await _feed_total(query, OfferFilters())
        dismissed = OfferFilters(exclude_offer_ids=(uuid4(), uuid4(), uuid4()))
        assert await _feed_total(query, dismissed) == 539_997
        assert query.counts == 1

    async def test_chaque_filtre_a_son_total(self, query: CountedQuery) -> None:
        await _feed_total(query, OfferFilters())
        await _feed_total(query, OfferFilters(cities=("Paris",)))
        await _feed_total(query, OfferFilters(cities=("Paris",)))
        assert query.counts == 2

    async def test_le_total_se_recompte_apres_la_fenetre(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _feed_total(query, OfferFilters())
        later = repositories.time.monotonic() + repositories.FEED_TOTAL_TTL_SECONDS + 1
        monkeypatch.setattr(repositories.time, "monotonic", lambda: later)
        await _feed_total(query, OfferFilters())
        assert query.counts == 2

    async def test_un_filtre_de_date_se_compte_a_chaque_fois(self, query: CountedQuery) -> None:
        from datetime import UTC, datetime  # noqa: PLC0415

        dated = OfferFilters(published_after=datetime(2026, 11, 1, tzinfo=UTC))
        await _feed_total(query, dated)
        await _feed_total(query, dated)
        assert query.counts == 2

    async def test_un_cache_plein_chasse_le_plus_ancien_pas_le_fil_sans_filtre(
        self, query: CountedQuery
    ) -> None:
        for n in range(repositories.FEED_TOTAL_CACHE_SIZE + 3):
            await _feed_total(query, OfferFilters())  # relu sans cesse : il reste frais
            await _feed_total(query, OfferFilters(cities=(f"Ville {n}",)))
        assert len(repositories._feed_totals) <= repositories.FEED_TOTAL_CACHE_SIZE
        assert len(repositories._feed_locks) <= repositories.FEED_TOTAL_CACHE_SIZE
        before = query.counts
        await _feed_total(query, OfferFilters())
        assert query.counts == before  # le total sans filtre n'a pas été chassé

    async def test_une_longue_liste_de_villes_nest_pas_gardee(self, query: CountedQuery) -> None:
        many = OfferFilters(cities=tuple(f"Ville {n}" for n in range(6)))
        await _feed_total(query, many)
        await _feed_total(query, many)
        assert query.counts == 2
        assert not repositories._feed_totals

    async def test_un_filtre_lent_ne_retient_pas_les_autres(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = asyncio.Event()
        slow = CountedQuery(7)

        async def blocked() -> int:
            await gate.wait()
            return 7

        monkeypatch.setattr(slow, "count", blocked)
        monkeypatch.setattr(
            repositories, "_apply_filters", lambda q, f: slow if f.cities else query
        )
        pending = asyncio.create_task(_feed_total(query, OfferFilters(cities=("Lente",))))
        await asyncio.sleep(0)
        # Le fil sans filtre répond pendant que l'autre filtre compte encore.
        assert await asyncio.wait_for(_feed_total(query, OfferFilters()), 1) == 540_000
        gate.set()
        assert await pending == 7

    async def test_un_total_perime_en_recalcul_sert_la_valeur_precedente(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _feed_total(query, OfferFilters())
        later = repositories.time.monotonic() + repositories.FEED_TOTAL_TTL_SECONDS + 1
        monkeypatch.setattr(repositories.time, "monotonic", lambda: later)
        totals = await asyncio.gather(*(_feed_total(query, OfferFilters()) for _ in range(20)))
        assert set(totals) == {540_000}
        assert query.counts == 2
