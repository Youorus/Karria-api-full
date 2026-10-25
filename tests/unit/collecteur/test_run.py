"""Un passage de collecte : louer, interroger avec prudence, remettre — même vide."""

from __future__ import annotations

import gzip
import json
from typing import Any

import pytest

from karria.collector.run import Collector
from karria.collector.settings import CollectorSettings
from karria.collector.sources import SiteBlockedError, Target

pytestmark = pytest.mark.unit

PARIS = Target(key="serveur|paris", search_term="serveur", location="Paris", distance_km=None)
LYON = Target(key="serveur|lyon", search_term="serveur", location="Lyon", distance_km=None)


class FakeApi:
    def __init__(self, targets: list[Target]) -> None:
        self.targets = targets
        self.batches: list[tuple[list[dict[str, Any]], str | None]] = []

    async def lease_targets(self, limit: int) -> list[Target]:
        return self.targets[:limit]

    async def push_batch(self, payload: bytes, *, target_key: str | None) -> str:
        text = gzip.decompress(payload).decode() if payload else ""
        self.batches.append(([json.loads(line) for line in text.splitlines()], target_key))
        return "k"


class FakeScraper:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[tuple[str, str]] = []

    def __call__(self, site: str, target: Target, **options: Any) -> list[dict[str, Any]]:
        self.calls.append((site, target.key))
        if site in self.failing:
            raise RuntimeError("banni")
        return [
            {"source": site, "source_id": "1", "title": "Serveur"},
            {"source": site, "source_id": "1", "title": "Serveur (revu)"},
        ]


def collector(api: FakeApi, scraper: FakeScraper, **settings: Any) -> tuple[Collector, list[float]]:
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    configured = CollectorSettings(sites=["indeed", "hellowork"], pause_seconds=1.5, **settings)
    made = Collector(configured, api, scraper=scraper, sleep=sleep, jitter=lambda: 0.5)  # type: ignore[arg-type]
    return made, pauses


async def test_chaque_cible_donne_un_lot_dedoublonne_et_une_pause_par_requete() -> None:
    api = FakeApi([PARIS])
    run, pauses = collector(api, FakeScraper())

    summary = await run.run_once()

    records, key = api.batches[0]
    assert key == PARIS.key
    assert [(r["source"], r["title"]) for r in records] == [
        ("indeed", "Serveur"),
        ("hellowork", "Serveur"),
    ]
    assert (summary.targets, summary.batches, summary.postings) == (1, 1, 2)
    assert pauses == [1.5, 1.5]


async def test_un_site_banni_est_laisse_de_cote_et_le_lot_part_quand_meme() -> None:
    api = FakeApi([PARIS, LYON, PARIS])
    scraper = FakeScraper(failing={"indeed"})
    run, _ = collector(api, scraper, site_failure_limit=2)

    summary = await run.run_once()

    assert scraper.calls.count(("indeed", PARIS.key)) == 1
    assert ("indeed", LYON.key) in scraper.calls
    assert len([c for c in scraper.calls if c[0] == "indeed"]) == 2
    assert summary.site_failures == {"indeed": 2}
    assert summary.sites_tripped == ["indeed"]
    assert len(api.batches) == 3


async def test_un_echec_isole_ne_coupe_pas_le_site() -> None:
    class Flaky(FakeScraper):
        def __call__(self, site: str, target: Target, **options: Any) -> list[dict[str, Any]]:
            self.calls.append((site, target.key))
            if len(self.calls) == 1:
                raise RuntimeError("une fois")
            return []

    api = FakeApi([PARIS, LYON])
    scraper = Flaky()
    run, _ = collector(api, scraper, site_failure_limit=2)

    summary = await run.run_once()

    assert len(scraper.calls) == 4
    assert summary.sites_tripped == []
    assert [records for records, _ in api.batches] == [[], []]


async def test_la_pause_porte_une_gigue_bornee() -> None:
    api = FakeApi([PARIS])
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    draws = iter([0.0, 1.0])
    configured = CollectorSettings(
        sites=["indeed", "hellowork"], pause_seconds=2.0, pause_jitter=0.3
    )
    run = Collector(configured, api, scraper=FakeScraper(), sleep=sleep, jitter=lambda: next(draws))  # type: ignore[arg-type]

    await run.run_once()

    assert pauses == [1.4, 2.6]


async def test_un_site_qui_rend_zero_la_ou_il_rendait_est_arrete_et_signale() -> None:
    rich = [
        Target(
            key=f"t{i}|paris",
            search_term="serveur",
            location="Paris",
            distance_km=None,
            last_found=40,
        )
        for i in range(4)
    ]
    api = FakeApi(rich)

    class Silent(FakeScraper):
        def __call__(self, site: str, target: Target, **options: Any) -> list[dict[str, Any]]:
            if site == "hellowork":
                self.calls.append((site, target.key))
                return []
            return super().__call__(site, target, **options)

    scraper = Silent()
    run, _ = collector(api, scraper, silent_zero_limit=2)

    summary = await run.run_once()

    assert len([c for c in scraper.calls if c[0] == "hellowork"]) == 2
    assert len([c for c in scraper.calls if c[0] == "indeed"]) == 4
    assert summary.sites_silent == ["hellowork"]
    assert summary.sites_tripped == []


async def test_un_zero_sur_une_cible_qui_ne_rendait_rien_ne_prouve_rien() -> None:
    poor = [
        Target(key=f"t{i}|paris", search_term="x", location="Paris", distance_km=None, last_found=0)
        for i in range(4)
    ]
    api = FakeApi(poor)

    class Silent(FakeScraper):
        def __call__(self, site: str, target: Target, **options: Any) -> list[dict[str, Any]]:
            self.calls.append((site, target.key))
            return []

    run, _ = collector(api, Silent(), silent_zero_limit=2)

    summary = await run.run_once()

    assert summary.sites_silent == []
    assert len(api.batches) == 4


async def test_un_echec_de_site_ne_compte_pas_comme_un_zero_silencieux() -> None:
    rich = [
        Target(
            key=f"t{i}|paris", search_term="x", location="Paris", distance_km=None, last_found=40
        )
        for i in range(3)
    ]
    run, _ = collector(
        FakeApi(rich), FakeScraper(failing={"indeed"}), silent_zero_limit=1, site_failure_limit=5
    )

    summary = await run.run_once()

    assert summary.sites_silent == []
    assert summary.site_failures == {"indeed": 3}


async def test_le_budget_horaire_par_site_borne_les_requetes() -> None:
    api = FakeApi([PARIS, LYON, PARIS])
    scraper = FakeScraper()
    run, _ = collector(api, scraper, site_hourly_budget=2)

    summary = await run.run_once()

    assert len([c for c in scraper.calls if c[0] == "indeed"]) == 2
    assert summary.over_budget == {"indeed": 1, "hellowork": 1}
    assert len(api.batches) == 3


async def test_le_budget_se_libere_avec_le_temps() -> None:
    clock = {"now": 0.0}
    api = FakeApi([PARIS, LYON])
    scraper = FakeScraper()
    configured = CollectorSettings(sites=["indeed"], site_hourly_budget=1, pause_seconds=0)

    async def sleep(seconds: float) -> None:
        clock["now"] += 3_601

    run = Collector(configured, api, scraper=scraper, sleep=sleep, clock=lambda: clock["now"])  # type: ignore[arg-type]

    summary = await run.run_once()

    assert len(scraper.calls) == 2
    assert summary.over_budget == {}


async def test_sans_cible_rien_ne_part() -> None:
    api = FakeApi([])
    run, pauses = collector(api, FakeScraper())

    summary = await run.run_once()

    assert summary.targets == 0
    assert api.batches == []
    assert pauses == []


# ---------------------------------------------------------------------
# France Travail
# ---------------------------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402 - section à part du même fichier

from karria.collector.france_travail import FranceTravailError  # noqa: E402
from karria.collector.run import FranceTravailCollector  # noqa: E402

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class FakeFtClient:
    requests = 0


def ft_offer(ident: str) -> dict[str, Any]:
    return {"id": ident, "intitule": f"Poste {ident}", "dateCreation": "2026-10-22T11:58:00Z"}


def ft_collector(
    api: FakeApi, offers: list[dict[str, Any]] | Exception, **settings: Any
) -> tuple[FranceTravailCollector, list[tuple[datetime, datetime]]]:
    windows: list[tuple[datetime, datetime]] = []
    clock = {"now": NOW}

    def collect(client: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
        windows.append((start, end))
        if isinstance(offers, Exception):
            raise offers
        return offers

    configured = CollectorSettings(
        france_travail_overlap_seconds=600, france_travail_startup_hours=2, **settings
    )
    collector = FranceTravailCollector(
        configured,
        api,  # type: ignore[arg-type]
        FakeFtClient(),  # type: ignore[arg-type]
        collect=collect,
        clock=lambda: clock["now"],
    )
    collector.clock = clock  # type: ignore[attr-defined]  # pour avancer l'horloge dans le test
    return collector, windows


async def test_le_premier_passage_relit_les_heures_de_demarrage_puis_recouvre_le_dernier() -> None:
    api = FakeApi([])
    collector, windows = ft_collector(api, [ft_offer("A"), ft_offer("B"), ft_offer("A")])

    first = await collector.poll_once()
    collector.clock["now"] = NOW + timedelta(minutes=3)  # type: ignore[attr-defined]
    await collector.poll_once()

    assert windows[0] == (NOW - timedelta(hours=2), NOW)
    assert windows[1] == (NOW - timedelta(minutes=10), NOW + timedelta(minutes=3))
    assert (first.fetched, first.postings, first.batches) == (3, 2, 1)
    records, key = api.batches[0]
    assert key is None
    assert [r["source_id"] for r in records] == ["A", "B"]
    assert all(r["source"] == "france_travail" for r in records)


async def test_un_gros_passage_part_en_lots_de_deux_mille() -> None:
    api = FakeApi([])
    collector, _ = ft_collector(api, [ft_offer(str(i)) for i in range(4_500)])

    summary = await collector.poll_once()

    assert summary.batches == 3
    assert [len(records) for records, _ in api.batches] == [2_000, 2_000, 500]


async def test_une_panne_france_travail_ne_remet_rien_et_avance_le_repere() -> None:
    api = FakeApi([])
    collector, _ = ft_collector(api, FranceTravailError(503, "indisponible"))

    summary = await collector.poll_once()

    assert (summary.fetched, summary.batches) == (0, 0)
    assert api.batches == []
    assert collector.watermark == NOW


async def test_le_balayage_couvre_les_jours_demandes_et_se_note() -> None:
    api = FakeApi([])
    collector, windows = ft_collector(api, [ft_offer("A")], france_travail_sweep_days=1)

    assert not collector.sweep_due(NOW.replace(hour=1))
    assert collector.sweep_due(NOW.replace(hour=2))
    await collector.sweep(3)

    assert windows == [(NOW - timedelta(days=3), NOW)]
    assert collector.last_sweep_on == NOW
    assert not collector.sweep_due(NOW.replace(hour=2))
    assert collector.sweep_due(NOW.replace(hour=2) + timedelta(days=1))


async def test_un_balayage_desactive_n_est_jamais_du() -> None:
    collector, _ = ft_collector(FakeApi([]), [], france_travail_sweep_days=0)

    assert not collector.sweep_due(NOW.replace(hour=2))


async def test_une_annonce_france_travail_sans_titre_est_ecartee_du_lot() -> None:
    api = FakeApi([])
    collector, _ = ft_collector(api, [ft_offer("A"), {"id": "B"}])

    summary = await collector.poll_once()

    assert (summary.fetched, summary.postings) == (2, 1)


class LinkedInScraper(FakeScraper):
    """LinkedIn bloque (429/999) à partir d'un appel donné ; les autres sites répondent."""

    def __init__(self, blocked_from: int, records: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.blocked_from = blocked_from
        self.records = records or []
        self.options: list[dict[str, Any]] = []
        self.linkedin: list[tuple[str, str]] = []

    def __call__(self, site: str, target: Target, **options: Any) -> list[dict[str, Any]]:
        self.options.append({"site": site, **options})
        if site == "linkedin":
            self.linkedin.append((site, target.key))
            if len(self.linkedin) >= self.blocked_from:
                raise SiteBlockedError("response status code 429 (blocked)", self.records)
            return [{"source": "linkedin", "source_id": target.key, "title": "Serveur"}]
        return super().__call__(site, target, **options)


def linkedin_run(
    scraper: LinkedInScraper, clock: dict[str, float], **settings: Any
) -> tuple[Collector, list[float]]:
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    configured = CollectorSettings(sites=["indeed", "linkedin"], pause_seconds=2.0, **settings)
    made = Collector(
        configured,
        FakeApi([PARIS, LYON, PARIS]),
        scraper=scraper,
        sleep=sleep,
        clock=lambda: clock["now"],
        jitter=lambda: 0.5,
    )  # type: ignore[arg-type]
    return made, pauses


async def test_linkedin_a_sa_pause_son_budget_et_sa_taille_de_page() -> None:
    clock = {"now": 0.0}
    scraper = LinkedInScraper(blocked_from=99)
    run, pauses = linkedin_run(scraper, clock, site_hourly_budgets={"linkedin": 2})

    summary = await run.run_once()

    linkedin = [o for o in scraper.options if o["site"] == "linkedin"]
    assert len(linkedin) == 2
    assert all(o["results_wanted"] == 25 for o in linkedin)
    assert {o["results_wanted"] for o in scraper.options if o["site"] == "indeed"} == {60}
    assert summary.over_budget == {"linkedin": 1}
    assert pauses == [2.0, 12.0, 2.0, 12.0, 2.0]


async def test_un_blocage_linkedin_ouvre_le_disjoncteur_d_un_passage_a_l_autre() -> None:
    clock = {"now": 0.0}
    kept = [{"source": "linkedin", "source_id": "lu", "title": "Serveur"}]
    scraper = LinkedInScraper(blocked_from=2, records=kept)
    run, _ = linkedin_run(
        scraper, clock, blocked_cooldown_seconds=100, blocked_cooldown_max_seconds=150
    )

    summary = await run.run_once()

    assert scraper.linkedin == [("linkedin", PARIS.key), ("linkedin", LYON.key)]
    assert summary.sites_blocked == ["linkedin"]
    assert run.blocked_until["linkedin"] == 100
    lyon_batch = run._api.batches[1][0]  # type: ignore[attr-defined]
    assert {"source": "linkedin", "source_id": "lu", "title": "Serveur"} in lyon_batch

    again = await run.run_once()
    assert again.sites_blocked == []
    assert len(scraper.linkedin) == 2

    clock["now"] = 101.0
    await run.run_once()
    assert run.blocked_until["linkedin"] == 251.0  # doublé, plafonné à 150


async def test_un_succes_remet_le_compteur_de_blocages_a_zero() -> None:
    clock = {"now": 0.0}
    scraper = LinkedInScraper(blocked_from=1)
    run, _ = linkedin_run(scraper, clock, blocked_cooldown_seconds=10)
    await run.run_once()
    scraper.blocked_from = 99
    clock["now"] = 11.0
    await run.run_once()
    scraper.blocked_from = 0
    clock["now"] = 1_000.0
    await run.run_once()
    assert run.blocked_until["linkedin"] == 1_010.0
