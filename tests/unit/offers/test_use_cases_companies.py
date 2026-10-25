"""Les entreprises côté cas d'usage : rattachement, recherche des logos, rattrapage, ingestion."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from karria.modules.offers.application import (
    AttachCompanies,
    BackfillCompanies,
    FetchCompanyLogos,
    FetchedImage,
    IngestBatch,
    IngestPostings,
    sighting_of_offer,
)
from karria.modules.offers.domain import Company, Source
from karria.modules.offers.domain.companies import (
    CLAIM_LEASE,
    RETRY_DELAYS,
    CompanyRecord,
    CompanySighting,
    ImageTraits,
    LogoOrigin,
    LogoStatus,
)
from offers.fakes import (
    NOW,
    PARIS,
    FakeGeocoder,
    FakeLanding,
    FixedClock,
    InMemoryCompanyRepository,
    InMemoryLogoStore,
    InMemoryOfferRepository,
    InMemoryOutbox,
    InMemoryRunRepository,
    InMemoryTargetRepository,
    RecordingQueue,
    RecordingUnitOfWork,
    ScriptedLogoFetcher,
    TraitsProcessor,
    make_offer,
)

pytestmark = pytest.mark.unit

INDEED = "https://d2q79iu7y748jz.cloudfront.net/s/_squarelogo/256x256/abc"
V1_LOGO = "https://media.licdn.com/dms/image/x"
GOOD = ImageTraits(256, 256, 40.0)
TINY = ImageTraits(16, 16, 40.0)


def sighting(**changes: Any) -> CompanySighting:
    base = CompanySighting(key="decathlon", name="Decathlon", offer=("indeed", "1"))
    return dataclasses.replace(base, **changes)


class TestRattachement:
    def setup_method(self) -> None:
        self.companies = InMemoryCompanyRepository()
        self.attach = AttachCompanies(companies=self.companies, clock=FixedClock())

    async def test_rien_a_rattacher(self) -> None:
        report = await self.attach.execute([])
        assert (report.created, report.linked) == (0, 0)

    async def test_cree_une_fois_et_rattache_chaque_offre(self) -> None:
        report = await self.attach.execute(
            [
                sighting(logo_url=INDEED),
                sighting(offer=("france_travail", "2")),
                sighting(key="lidl", name="Lidl", offer=None),
            ]
        )

        assert report.created == 2
        assert report.updated == 0
        assert report.linked == 2
        assert report.logos_due == 1
        (decathlon,) = [c for c in self.companies.companies.values() if c.key == "decathlon"]
        assert self.companies.links == {
            ("indeed", "1"): decathlon.id,
            ("france_travail", "2"): decathlon.id,
        }

    async def test_revoir_la_meme_annonce_ne_change_rien(self) -> None:
        await self.attach.execute([sighting(logo_url=INDEED)])

        report = await self.attach.execute([sighting(logo_url=INDEED)])

        assert (report.created, report.updated, report.linked, report.logos_due) == (0, 0, 0, 0)

    async def test_un_homonyme_d_un_autre_domaine_est_une_autre_entreprise(self) -> None:
        await self.attach.execute([sighting(domain="decathlon.fr")])

        report = await self.attach.execute([sighting(domain="decathlon.be", offer=("indeed", "2"))])

        assert report.created == 1
        assert len(self.companies.companies) == 2

    async def test_ce_qu_on_apprend_met_a_jour(self) -> None:
        await self.attach.execute([sighting()])

        report = await self.attach.execute([sighting(domain="decathlon.fr")])

        assert (report.created, report.updated) == (0, 1)
        (only,) = self.companies.companies.values()
        assert only.domain == "decathlon.fr"
        assert only.logo_status is LogoStatus.PENDING

    async def test_une_reprise_plus_tard_n_est_pas_due(self) -> None:
        later = CompanyRecord(
            id=uuid4(),
            key="decathlon",
            name="Decathlon",
            logo_status=LogoStatus.PENDING,
            logo_next_attempt_at=NOW + timedelta(hours=1),
        )
        await self.companies.save_many([later])

        report = await self.attach.execute([sighting(domain="decathlon.fr")])

        assert report.updated == 1
        assert report.logos_due == 0


class Logos:
    def __init__(
        self,
        images: dict[str, FetchedImage] | None = None,
        icons: dict[str, list[str]] | None = None,
        traits: dict[bytes, ImageTraits] | None = None,
    ) -> None:
        self.clock = FixedClock()
        self.companies = InMemoryCompanyRepository()
        self.fetcher = ScriptedLogoFetcher(images, icons)
        self.store = InMemoryLogoStore()
        self.use_case = FetchCompanyLogos(
            companies=self.companies,
            fetcher=self.fetcher,
            processor=TraitsProcessor(traits or {}),
            store=self.store,
            clock=self.clock,
        )

    async def add(self, **changes: Any) -> CompanyRecord:
        base = CompanyRecord(
            id=uuid4(), key="decathlon", name="Decathlon", logo_next_attempt_at=NOW
        )
        record = dataclasses.replace(base, **changes)
        await self.companies.save_many([record])
        return record

    def get(self, company_id: UUID) -> CompanyRecord:
        return self.companies.companies[company_id]


def ok(payload: bytes) -> FetchedImage:
    return FetchedImage(payload=payload)


class TestRechercheDesLogos:
    async def test_rien_a_faire(self) -> None:
        report = await Logos().use_case.execute()
        assert report.claimed == 0

    async def test_le_logo_de_la_source_est_range_et_partage(self) -> None:
        env = Logos(images={INDEED: ok(b"indeed")}, traits={b"indeed": GOOD})
        first = await env.add(source_logo_url=INDEED)
        second = await env.add(key="decat", v1_logo_url=INDEED)

        report = await env.use_case.execute()

        assert (report.claimed, report.ready, report.stored) == (2, 2, 1)
        assert env.get(first.id).logo_status is LogoStatus.READY
        assert env.get(first.id).logo_origin is LogoOrigin.SOURCE
        assert env.get(second.id).logo_hash == env.get(first.id).logo_hash
        assert list(env.store.stored) == [env.get(first.id).logo_hash]

    async def test_les_refus_menent_a_l_icone_du_site(self) -> None:
        icon = "https://decathlon.fr/icon-192.png"
        env = Logos(
            images={
                INDEED: ok(b"tiny"),
                V1_LOGO: ok(b"garbage"),
                icon: ok(b"icon"),
            },
            icons={"decathlon.fr": [icon, INDEED]},
            traits={b"tiny": TINY, b"icon": ImageTraits(192, 192, 30.0)},
        )
        company = await env.add(source_logo_url=INDEED, v1_logo_url=V1_LOGO, domain="decathlon.fr")

        report = await env.use_case.execute()

        settled = env.get(company.id)
        assert settled.logo_status is LogoStatus.READY
        assert settled.logo_origin is LogoOrigin.FAVICON
        assert settled.logo_rejected_urls == (INDEED, V1_LOGO)
        assert report.rejections == {"too_small": 1, "unreadable": 1}
        assert env.fetcher.fetched == [INDEED, V1_LOGO, icon]

    async def test_rien_d_acceptable_donne_les_initiales(self) -> None:
        env = Logos()
        company = await env.add(
            domain="decathlon.fr", logo_rejected_urls=("https://decathlon.fr/apple-touch-icon.png",)
        )

        report = await env.use_case.execute()

        assert report.missing == 1
        assert env.get(company.id).logo_status is LogoStatus.MISSING
        assert env.fetcher.fetched == [
            "https://www.google.com/s2/favicons?domain=decathlon.fr&sz=256"
        ]

    async def test_un_echec_passager_se_reessaie(self) -> None:
        env = Logos(images={INDEED: FetchedImage(payload=None, permanent=False)})
        company = await env.add(source_logo_url=INDEED)

        report = await env.use_case.execute()

        settled = env.get(company.id)
        assert report.retry == 1
        assert settled.logo_status is LogoStatus.PENDING
        assert settled.logo_next_attempt_at == NOW + RETRY_DELAYS[0]
        assert settled.logo_rejected_urls == ()

    async def test_une_image_generique_connue_est_refusee(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = Logos(images={INDEED: ok(b"generic")}, traits={b"generic": GOOD})
        digest = "generic".ljust(32, "0")
        monkeypatch.setattr(
            "karria.modules.offers.application.use_cases.PLACEHOLDER_HASHES", frozenset({digest})
        )
        company = await env.add(source_logo_url=INDEED)

        report = await env.use_case.execute()

        assert report.rejections == {"placeholder": 1}
        assert env.get(company.id).logo_status is LogoStatus.MISSING

    async def test_une_entreprise_pas_encore_due_n_est_pas_prise(self) -> None:
        env = Logos()
        await env.add(logo_next_attempt_at=NOW + CLAIM_LEASE)
        assert (await env.use_case.execute()).claimed == 0


class TestRattrapage:
    async def test_les_offres_en_base_sont_rattachees_par_paquets(self) -> None:
        offers = InMemoryOfferRepository(
            [
                make_offer(source_id="1", company=Company(name="Decathlon", logo_url=INDEED)),
                make_offer(source_id="2", company=Company(name="Decathlon SA")),
                make_offer(source_id="3", company=Company(name="--")),
                make_offer(source_id="4", company=None),
                make_offer(source_id="5", company=Company(name="Lidl"), is_active=False),
            ]
        )
        companies = InMemoryCompanyRepository(offers)
        backfill = BackfillCompanies(
            companies=companies, attach=AttachCompanies(companies=companies, clock=FixedClock())
        )

        report = await backfill.execute(batch=2)

        assert report.linked == 2
        assert report.created == 1
        assert report.offers == 3
        assert sorted(sid for _, sid in companies.links) == ["1", "2"]

        everything = await backfill.execute(batch=10, active_only=False)
        assert everything.linked == 1

    def test_l_offre_sans_employeur(self) -> None:
        assert sighting_of_offer(make_offer(company=None)) is None
        found = sighting_of_offer(
            make_offer(
                source=Source.FRANCE_TRAVAIL,
                company=Company(name="Decathlon", website="https://www.decathlon.fr"),
            )
        )
        assert found is not None
        assert found.domain == "decathlon.fr"


class TestIngestion:
    def env(
        self, attach: AttachCompanies | None
    ) -> tuple[IngestBatch, FakeLanding, RecordingQueue]:
        clock = FixedClock()
        landing = FakeLanding()
        queue = RecordingQueue()
        batch = IngestBatch(
            landing=landing,
            ingest=IngestPostings(
                offers=InMemoryOfferRepository(),
                outbox=InMemoryOutbox(),
                geocoder=FakeGeocoder({"Paris": PARIS}),
                clock=clock,
                uow=RecordingUnitOfWork(),
            ),
            targets=InMemoryTargetRepository(),
            runs=InMemoryRunRepository(),
            queue=queue,
            clock=clock,
            companies=attach,
        )
        landing.records["k"] = [
            {
                "source": "indeed",
                "source_id": "in-1",
                "title": "Vendeur",
                "description": "Vente.",
                "company_name": "Decathlon",
                "company_logo_url": INDEED,
                "company_website": "https://www.decathlon.fr",
                "location_label": "Paris",
            }
        ]
        return batch, landing, queue

    async def test_le_lot_rattache_ses_employeurs_et_demande_leurs_logos(self) -> None:
        companies = InMemoryCompanyRepository()
        batch, _, queue = self.env(AttachCompanies(companies=companies, clock=FixedClock()))

        await batch.execute("k", target_key=None)

        (company,) = companies.companies.values()
        assert company.domain == "decathlon.fr"
        assert companies.links == {("indeed", "in-1"): company.id}
        assert queue.logo_fetches == 1

        await batch.execute("k", target_key=None)
        assert queue.logo_fetches == 1

    async def test_une_panne_du_rattachement_ne_perd_pas_le_lot(self) -> None:
        class Broken(InMemoryCompanyRepository):
            async def find_by_keys(self, keys: Any) -> Any:
                raise RuntimeError("base indisponible")

        batch, _, queue = self.env(AttachCompanies(companies=Broken(), clock=FixedClock()))

        report = await batch.execute("k", target_key=None)

        assert report.inserted == 1
        assert queue.logo_fetches == 0
