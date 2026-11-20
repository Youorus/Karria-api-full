"""Le total du fil ne se recompte pas à chaque ouverture, et ne se laisse pas retenir."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from karria.modules.offers.application.ports import OfferFilters
from karria.modules.offers.infrastructure import repositories
from karria.modules.offers.infrastructure.repositories import _feed_total, forget_feed_totals

pytestmark = pytest.mark.unit

TOTAL = 540_000


class CountedQuery:
    """Une requête dont on compte les `count()`, et qu'on peut retenir."""

    def __init__(self, total: int) -> None:
        self.total, self.counts, self.running, self.peak = total, 0, 0, 0
        self.gate: asyncio.Event | None = None

    def filter(self, **_: Any) -> CountedQuery:
        return self

    def exclude(self, **_: Any) -> CountedQuery:
        return self

    async def count(self) -> int:
        self.counts += 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(0)  # laisse passer les autres requêtes, comme une vraie base
        finally:
            self.running -= 1
        return self.total


@pytest.fixture
def query(monkeypatch: pytest.MonkeyPatch) -> CountedQuery:
    forget_feed_totals()
    monkeypatch.setattr(repositories, "_count_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(repositories, "_unfiltered_slot", asyncio.Semaphore(1))
    found = CountedQuery(TOTAL)
    monkeypatch.setattr(repositories.OfferModel, "filter", lambda **_: found)
    return found


def expire(monkeypatch: pytest.MonkeyPatch) -> None:
    # L'horloge des caches seulement : celle de la boucle d'événements ne bouge pas.
    later = repositories._now() + repositories.FEED_TOTAL_TTL_SECONDS + 1
    monkeypatch.setattr(repositories, "_now", lambda: later)


class TestTotalDuFil:
    async def test_le_total_se_compte_une_fois_par_fenetre(self, query: CountedQuery) -> None:
        for _ in range(50):
            assert await _feed_total(query, OfferFilters()) == TOTAL
        assert query.counts == 1

    async def test_trente_requetes_simultanees_ne_comptent_quune_fois(
        self, query: CountedQuery
    ) -> None:
        totals = await asyncio.gather(*(_feed_total(query, OfferFilters()) for _ in range(30)))
        assert set(totals) == {TOTAL}
        assert query.counts == 1

    async def test_les_offres_ecartees_se_retranchent_sans_recompter(
        self, query: CountedQuery
    ) -> None:
        await _feed_total(query, OfferFilters())
        dismissed = OfferFilters(exclude_offer_ids=(uuid4(), uuid4(), uuid4()))
        assert await _feed_total(query, dismissed) == TOTAL - 3
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
        expire(monkeypatch)
        await _feed_total(query, OfferFilters())
        assert query.counts == 2

    async def test_un_total_perime_en_recalcul_sert_la_valeur_precedente(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _feed_total(query, OfferFilters())
        expire(monkeypatch)
        totals = await asyncio.gather(*(_feed_total(query, OfferFilters()) for _ in range(20)))
        assert set(totals) == {TOTAL}
        assert query.counts == 2


class TestTenue:
    async def test_le_total_sans_filtre_nest_jamais_evince(self, query: CountedQuery) -> None:
        await _feed_total(query, OfferFilters())
        for n in range(repositories.FEED_TOTAL_CACHE_SIZE + 20):
            await _feed_total(query, OfferFilters(cities=(f"Ville {n}",)))
        assert len(repositories._feed_totals) == repositories.FEED_TOTAL_CACHE_SIZE
        before = query.counts
        assert await _feed_total(query, OfferFilters()) == TOTAL
        assert query.counts == before

    async def test_un_filtre_relu_reste_le_moins_recemment_lu_sort(
        self, query: CountedQuery
    ) -> None:
        paris = OfferFilters(cities=("Paris",))
        await _feed_total(query, paris)
        for n in range(repositories.FEED_TOTAL_CACHE_SIZE - 1):
            await _feed_total(query, OfferFilters(cities=(f"Ville {n}",)))
            await _feed_total(query, paris)  # relu : il redevient le plus récent
        await _feed_total(query, OfferFilters(cities=("Une de plus",)))
        assert ((), ("Paris",)) in repositories._feed_totals
        assert ((), ("Ville 0",)) not in repositories._feed_totals

    async def test_deux_comptes_a_la_fois_au_plus(self, query: CountedQuery) -> None:
        query.gate = asyncio.Event()
        tasks = [
            asyncio.create_task(_feed_total(query, OfferFilters(cities=(f"Ville {n}",))))
            for n in range(8)
        ]
        await asyncio.sleep(0.01)
        assert query.peak == 2
        query.gate.set()
        assert set(await asyncio.gather(*tasks)) == {TOTAL}
        assert not repositories._feed_counts  # chaque calcul fini libère sa place

    async def test_la_file_des_calculs_est_bornee(self, query: CountedQuery) -> None:
        await _feed_total(query, OfferFilters())
        query.gate = asyncio.Event()
        flood = [
            asyncio.create_task(_feed_total(query, OfferFilters(cities=(f"Inventée {n}",))))
            for n in range(200)
        ]
        await asyncio.sleep(0.01)
        # Huit calculs lancés ou en attente, pas deux cents ; les autres ont déjà leur réponse.
        assert len(repositories._feed_counts) == repositories.FEED_TOTAL_PENDING_MAX
        done = [t for t in flood if t.done()]
        assert len(done) == 200 - repositories.FEED_TOTAL_PENDING_MAX
        assert {t.result() for t in done} == {TOTAL}  # le majorant connu, sans compter
        query.gate.set()
        await asyncio.gather(*flood)

    async def test_le_total_sans_filtre_se_recompte_meme_file_pleine(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _feed_total(query, OfferFilters())
        slow = CountedQuery(1)
        slow.gate = asyncio.Event()
        monkeypatch.setattr(
            repositories, "_apply_filters", lambda q, f: slow if f.cities else query
        )
        flood = [
            asyncio.create_task(_feed_total(query, OfferFilters(cities=(f"Inventée {n}",))))
            for n in range(50)
        ]
        await asyncio.sleep(0.01)
        expire(monkeypatch)
        before = query.counts
        assert await asyncio.wait_for(_feed_total(query, OfferFilters()), 0.5) == TOTAL
        await asyncio.sleep(0.01)
        assert query.counts == before + 1  # sa place réservée : il s'est recompté
        slow.gate.set()
        await asyncio.gather(*flood)

    async def test_un_filtre_lent_ne_retient_pas_les_filtres_deja_comptes(
        self, query: CountedQuery
    ) -> None:
        await _feed_total(query, OfferFilters())
        query.gate = asyncio.Event()
        slow = [
            asyncio.create_task(_feed_total(query, OfferFilters(cities=(f"Lente {n}",))))
            for n in range(6)
        ]
        await asyncio.sleep(0.01)
        # Le fil sans filtre répond tout de suite pendant que six filtres attendent.
        assert await asyncio.wait_for(_feed_total(query, OfferFilters()), 0.5) == TOTAL
        query.gate.set()
        await asyncio.gather(*slow)

    async def test_passe_le_delai_aucun_compte_de_plus_le_majorant_est_rendu(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _feed_total(query, OfferFilters())
        monkeypatch.setattr(repositories, "FEED_TOTAL_WAIT_SECONDS", 0.02)
        query.gate = asyncio.Event()
        before = query.counts
        waiting = await asyncio.gather(
            *(_feed_total(query, OfferFilters(cities=("Lente",))) for _ in range(10))
        )
        # Dix requêtes, un seul compte lancé ; les dix reçoivent le total sans filtre.
        assert set(waiting) == {TOTAL}
        assert query.counts == before + 1
        query.gate.set()
        await asyncio.sleep(0.01)
        query.gate = None
        assert await _feed_total(query, OfferFilters(cities=("Lente",))) == TOTAL
        assert query.counts == before + 1  # le compte en retard a fini par être gardé

    async def test_sans_rien_de_garde_le_delai_rend_zero(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(repositories, "FEED_TOTAL_WAIT_SECONDS", 0.02)
        query.gate = asyncio.Event()
        assert await _feed_total(query, OfferFilters(cities=("Lente",))) == 0
        query.gate.set()
        await asyncio.sleep(0.01)

    async def test_un_filtre_non_garde_passe_par_la_meme_limite(
        self, query: CountedQuery, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dated = OfferFilters(published_after=datetime(2026, 11, 1, tzinfo=UTC))
        many = OfferFilters(cities=tuple(f"Ville {n}" for n in range(6)))
        await _feed_total(query, dated)
        await _feed_total(query, dated)
        await _feed_total(query, many)
        assert query.counts == 3
        assert not repositories._feed_totals
        query.gate = asyncio.Event()
        monkeypatch.setattr(repositories, "FEED_TOTAL_WAIT_SECONDS", 0.02)
        before = query.counts
        results = await asyncio.gather(*(_feed_total(query, dated) for _ in range(8)))
        assert query.peak == 2
        assert query.counts == before + 2
        assert set(results) == {0}  # rien de gardé : 0, sans s'empiler ni compter de plus
        query.gate.set()
