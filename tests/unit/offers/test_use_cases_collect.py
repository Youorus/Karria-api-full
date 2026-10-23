"""La collecte côté API : pilotage, location des cibles, remise et ingestion des lots."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    IngestBatch,
    IngestionReport,
    IngestPostings,
    LeaseScrapeTargets,
    ReceiveBatch,
    RefreshScrapeTargets,
)
from karria.modules.offers.domain import (
    BATCH_MAX_BYTES,
    DEMAND_TTL,
    TARGET_LEASE,
    BatchRejectedError,
    ContractType,
    ScrapeTarget,
    TargetOrigin,
    TargetState,
    TargetStats,
    interval_for,
    seed_targets,
)
from offers.fakes import (
    NOW,
    PARIS,
    FakeGeocoder,
    FakeLanding,
    FixedClock,
    InMemoryOfferRepository,
    InMemoryOutbox,
    InMemoryRunRepository,
    InMemoryTargetRepository,
    RecordingQueue,
    RecordingUnitOfWork,
    make_offer,
)

pytestmark = pytest.mark.unit

SERVEUR = ScrapeTarget(
    search_term="serveur",
    location="Paris",
    origin=TargetOrigin.DEMAND,
    distance_km=20,
    location_key="75",
)
CUISINIER = ScrapeTarget(search_term="cuisinier", location="France", origin=TargetOrigin.SEED)
ACTIVE = TargetStats(state=TargetState.ACTIVE)


def record(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source": "indeed",
        "source_id": "in-1",
        "title": "Serveur H/F",
        "description": "Service en salle, CDI temps plein.",
        "company_name": "Brasserie du Port",
        "location_label": "Paris",
        "date_posted": "2026-10-21",
    }
    base.update(changes)
    return base


class Env:
    def __init__(self) -> None:
        self.clock = FixedClock()
        self.targets = InMemoryTargetRepository()
        self.landing = FakeLanding()
        self.runs = InMemoryRunRepository()
        self.queue = RecordingQueue()
        self.offers = InMemoryOfferRepository()
        self.lease = LeaseScrapeTargets(targets=self.targets, clock=self.clock)
        self.receive = ReceiveBatch(
            landing=self.landing, targets=self.targets, queue=self.queue, clock=self.clock
        )
        self.ingest = IngestBatch(
            landing=self.landing,
            ingest=IngestPostings(
                offers=self.offers,
                outbox=InMemoryOutbox(),
                geocoder=FakeGeocoder({"Paris": PARIS}),
                clock=self.clock,
                uow=RecordingUnitOfWork(),
            ),
            targets=self.targets,
            runs=self.runs,
            queue=self.queue,
            clock=self.clock,
        )


@pytest.fixture
def env() -> Env:
    return Env()


class TestLocation:
    async def test_les_cibles_dues_sont_louees_et_ne_reviennent_pas_avant_la_fin_du_bail(
        self, env: Env
    ) -> None:
        env.targets.add(SERVEUR)
        env.targets.add(CUISINIER, next_at=NOW + timedelta(hours=1))

        leased = await env.lease.execute()
        again = await env.lease.execute()

        assert [t.key for t in leased] == [SERVEUR.key]
        assert leased[0].search_term == "serveur"
        assert leased[0].location == "Paris"
        assert leased[0].distance_km == 20
        assert leased[0].last_found == 0
        assert env.targets.leased == [([SERVEUR.key], NOW + TARGET_LEASE)]
        assert again == []

    async def test_sans_cible_due_rien_n_est_loue(self, env: Env) -> None:
        assert await env.lease.execute() == []
        assert env.targets.leased == []

    async def test_les_cibles_partent_par_priorite_et_le_terme_porte_le_contrat(
        self, env: Env
    ) -> None:
        quiet = ScrapeTarget(
            search_term="plongeur", location="Paris", origin=TargetOrigin.DEMAND, location_key="75"
        )
        wanted = ScrapeTarget(
            search_term="développeur",
            location="Lyon",
            origin=TargetOrigin.DEMAND,
            location_key="69",
            contract=ContractType.ALTERNANCE,
        )
        env.targets.add(quiet, stats=ACTIVE, last_scraped_at=NOW - timedelta(hours=6))
        env.targets.add(
            SERVEUR,
            stats=TargetStats(state=TargetState.ACTIVE, last_found=40, yield_ema=15),
            last_scraped_at=NOW - timedelta(hours=1),
        )
        env.targets.add(
            wanted,
            stats=ACTIVE,
            last_scraped_at=NOW - timedelta(hours=6),
            thin_since=NOW - timedelta(minutes=5),
        )
        await env.targets.replace_demands(uuid4(), [SERVEUR.key], at=NOW)
        await env.targets.replace_demands(uuid4(), [SERVEUR.key], at=NOW)

        leased = await env.lease.execute(limit=2)

        assert [t.key for t in leased] == [wanted.key, SERVEUR.key]
        assert leased[0].search_term == "alternance développeur"
        assert leased[1].last_found == 40
        assert env.targets.leased == [([wanted.key, SERVEUR.key], NOW + TARGET_LEASE)]


class TestRemise:
    async def test_un_lot_est_atterri_tel_quel_et_son_ingestion_differee(self, env: Env) -> None:
        env.targets.add(SERVEUR)

        key = await env.receive.execute(b"lot", target_key=SERVEUR.key)

        assert env.landing.objects[key] == b"lot"
        assert env.queue.batches == [(key, SERVEUR.key)]

    async def test_un_lot_sans_cible_est_accepte(self, env: Env) -> None:
        key = await env.receive.execute(b"lot", target_key=None)

        assert env.queue.batches == [(key, None)]

    async def test_un_lot_trop_gros_est_refuse(self, env: Env) -> None:
        with pytest.raises(BatchRejectedError) as error:
            await env.receive.execute(b"x" * (BATCH_MAX_BYTES + 1), target_key=None)

        assert error.value.details["reason"] == "batch_too_large"
        assert env.landing.objects == {}

    async def test_un_lot_pour_une_cible_inconnue_est_refuse(self, env: Env) -> None:
        with pytest.raises(BatchRejectedError) as error:
            await env.receive.execute(b"lot", target_key="inconnue|nulle part")

        assert error.value.details["reason"] == "target_unknown"


class TestIngestion:
    async def test_un_lot_est_ingere_la_cible_replanifiee_et_le_bilan_garde(self, env: Env) -> None:
        env.targets.add(SERVEUR)
        env.landing.records["k"] = [
            record(),
            record(source_id="in-2", description="Autre texte."),
            {"source": "monster", "source_id": "x", "title": "Inconnu"},
        ]

        report = await env.ingest.execute("k", target_key=SERVEUR.key)

        assert report.fetched == 3
        assert report.inserted == 2
        assert report.errors == 1
        assert report.targets == 1
        assert report.landing_keys == ("k",)
        assert env.queue.index_syncs == 1
        key, learned, next_at = env.targets.scraped[0]
        assert key == SERVEUR.key
        assert (learned.state, learned.scrapes, learned.last_found, learned.yield_ema) == (
            TargetState.ACTIVE,
            1,
            3,
            2.0,
        )
        assert learned.gate_rejects == 1  # « Inconnu » ne dit pas « serveur »
        assert next_at == NOW + interval_for(TargetOrigin.DEMAND, learned)
        assert env.runs.reports == [report]

    async def test_un_lot_vide_clot_la_cible_sans_resynchronisation_et_retrograde_l_essai(
        self, env: Env
    ) -> None:
        env.targets.add(CUISINIER)

        report = await env.ingest.execute("vide", target_key=CUISINIER.key)

        assert report.fetched == 0
        assert report.new_share == 0.0
        assert env.queue.index_syncs == 0
        _, learned, next_at = env.targets.scraped[0]
        assert learned.state is TargetState.DEMOTED
        assert next_at == NOW + interval_for(TargetOrigin.SEED, learned)

    async def test_un_essai_dont_les_titres_disent_un_autre_metier_est_retrograde(
        self, env: Env
    ) -> None:
        env.targets.add(SERVEUR)
        env.landing.records["k"] = [
            record(source_id=f"in-{i}", title="Plongeur", description=f"Texte {i}")
            for i in range(3)
        ]

        await env.ingest.execute("k", target_key=SERVEUR.key)

        _, learned, _ = env.targets.scraped[0]
        assert learned.state is TargetState.DEMOTED
        assert learned.gate_rejects == 3

    async def test_une_cible_active_apprend_sa_redondance_avec_france_travail(
        self, env: Env
    ) -> None:
        env.targets.add(
            SERVEUR, stats=TargetStats(state=TargetState.ACTIVE, scrapes=2, yield_ema=10)
        )
        canonical = make_offer(
            source=__import__(
                "karria.modules.offers.domain", fromlist=["Source"]
            ).Source.FRANCE_TRAVAIL,
            title="Serveur H/F",
            description="Service en salle, CDI temps plein.",
            company=__import__("karria.modules.offers.domain", fromlist=["Company"]).Company(
                name="Brasserie du Port"
            ),
        )
        env.offers.add(canonical)
        env.landing.records["k"] = [record()]

        await env.ingest.execute("k", target_key=SERVEUR.key)

        _, learned, _ = env.targets.scraped[0]
        assert learned.state is TargetState.ACTIVE
        assert learned.overlap_ema == pytest.approx(0.3)
        assert learned.yield_ema == 7.0
        assert learned.zero_streak == 1

    async def test_un_lot_deja_connu_ne_demande_aucune_resynchronisation(self, env: Env) -> None:
        env.landing.records["k"] = [record()]
        await env.ingest.execute("k", target_key=None)
        env.queue.index_syncs = 0

        report = await env.ingest.execute("k", target_key=None)

        assert report.refreshed == 1
        assert report.targets == 0
        assert env.queue.index_syncs == 0
        assert env.targets.scraped == []

    async def test_une_cible_disparue_entre_remise_et_ingestion_n_est_pas_replanifiee(
        self, env: Env
    ) -> None:
        env.landing.records["k"] = [record()]

        await env.ingest.execute("k", target_key="disparue|nulle part")

        assert env.targets.scraped == []


class TestBilan:
    def test_un_bilan_vide_a_une_part_de_nouveau_nulle(self) -> None:
        report = IngestionReport(run_id=uuid4(), started_at=NOW, finished_at=NOW)

        assert report.new_share == 0.0


class TestPilotage:
    async def test_le_socle_est_pose_la_demande_pesee_et_les_oubliees_eteintes(self) -> None:
        targets = InMemoryTargetRepository()
        targets.retired = 3
        targets.add(SERVEUR)
        alice, bob = uuid4(), uuid4()
        await targets.replace_demands(alice, [SERVEUR.key], at=NOW)
        await targets.replace_demands(bob, [SERVEUR.key, "cuisinier|75|-"], at=NOW)
        refresh = RefreshScrapeTargets(targets=targets, clock=FixedClock())

        demanded = await refresh.execute()

        upserted, due_now = targets.upserts[0]
        assert demanded == 2
        assert due_now is False
        assert len(upserted) == len(seed_targets())
        assert targets.weights == [{SERVEUR.key: 2, "cuisinier|75|-": 1}]
        assert targets.targets[SERVEUR.key].target.weight == 2
        assert targets.retired_before == [NOW - DEMAND_TTL]
