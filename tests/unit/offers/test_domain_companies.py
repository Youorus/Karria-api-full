"""Les entreprises : clé de nom, domaine, rattachement, candidats et jugement des logos."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from karria.modules.offers.domain import RawPosting, Source
from karria.modules.offers.domain.companies import (
    MAX_REJECTED_URLS,
    RETRY_DELAYS,
    CompanyRecord,
    CompanySighting,
    ImageTraits,
    LogoAttempt,
    LogoCandidate,
    LogoOrigin,
    LogoRejection,
    LogoStatus,
    choose_company,
    company_domain,
    company_key,
    icon_links,
    is_placeholder_url,
    judge_logo,
    logo_candidates,
    logo_object_key,
    merge_sighting,
    new_company,
    registrable_domain,
    settle_logo,
    sighting_of,
    site_icon_urls,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
INDEED = "https://d2q79iu7y748jz.cloudfront.net/s/_squarelogo/256x256/abc"
HELLOWORK = "https://f.hellowork.com/img/entreprises/1.png?v=1"
FT = "https://api.francetravail.fr/logo/2"


def record(**changes: object) -> CompanyRecord:
    base = CompanyRecord(id=uuid4(), key="decathlon", name="Decathlon")
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


def sighting(**changes: object) -> CompanySighting:
    base = CompanySighting(key="decathlon", name="Decathlon")
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


class TestCleDeNom:
    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("DECATHLON SAS", "decathlon"),
            ("Décathlon S.A.S.", "decathlon"),
            ("L'Oréal  Groupe", "l oreal groupe"),
            ("SA", "sa"),
            ("Sasha Conseil", "sasha conseil"),
            ("Acme GmbH & Co.", "acme co"),
        ],
    )
    def test_plie_et_retire_la_forme_juridique(self, name: str, key: str) -> None:
        assert company_key(name) == key

    @pytest.mark.parametrize("name", [None, "", " - "])
    def test_rien_ne_reste(self, name: str | None) -> None:
        assert company_key(name) is None


class TestDomaine:
    @pytest.mark.parametrize(
        ("url", "domain"),
        [
            ("https://jobs.decathlon.fr/offre/1", "decathlon.fr"),
            ("www.lidl.fr", "lidl.fr"),
            ("https://emploi.x.gouv.fr", "x.gouv.fr"),
            ("https://shop.example.co.uk/a", "example.co.uk"),
            ("https://192.168.0.1/admin", None),
            ("https://localhost/", None),
            ("https://co.uk", None),
            ("http://[::1", None),
            ("", None),
            (None, None),
        ],
    )
    def test_domaine_enregistrable(self, url: str | None, domain: str | None) -> None:
        assert registrable_domain(url) == domain

    def test_un_site_d_offres_n_identifie_pas_l_employeur(self) -> None:
        assert company_domain("https://fr.indeed.com/cmp/Apase") is None
        assert company_domain("https://fr.linkedin.com/company/x") is None
        assert company_domain("https://decathlon.wd3.myworkdayjobs.com/x") is None
        assert company_domain("https://www.decathlon.fr") == "decathlon.fr"


class TestApparition:
    def posting(self, **changes: object) -> RawPosting:
        base = RawPosting(
            source=Source.INDEED,
            source_id="1",
            title="Vendeur",
            description="",
            company_name="DECATHLON SAS",
        )
        return dataclasses.replace(base, **changes)  # type: ignore[arg-type]

    def test_le_site_dit_le_domaine(self) -> None:
        found = sighting_of(
            self.posting(
                company_url="https://fr.indeed.com/cmp/Decathlon",
                company_website="https://www.decathlon.fr",
                company_logo_url=INDEED,
            )
        )
        assert found == CompanySighting(
            key="decathlon",
            name="DECATHLON SAS",
            domain="decathlon.fr",
            website="https://www.decathlon.fr",
            logo_url=INDEED,
            offer=("indeed", "1"),
        )

    def test_le_lien_pour_postuler_devine_le_domaine_sans_site(self) -> None:
        found = sighting_of(self.posting(job_url_direct="https://recrutement.decathlon.fr/1"))
        assert found is not None
        assert found.domain == "decathlon.fr"
        assert found.website is None

    @pytest.mark.parametrize(
        "changes",
        [{"company_name": None}, {"company_name": "  "}, {"company_name": "--"}, {"expired": True}],
    )
    def test_sans_employeur(self, changes: dict[str, object]) -> None:
        assert sighting_of(self.posting(**changes)) is None


class TestChoix:
    def test_aucune_connue(self) -> None:
        assert choose_company([], "decathlon.fr") is None

    def test_sans_domaine_la_plus_ancienne(self) -> None:
        first, second = record(), record(domain="x.fr")
        assert choose_company([first, second], None) is first

    def test_meme_domaine_d_abord_puis_l_homonyme_sans_domaine(self) -> None:
        bare, other, same = record(), record(domain="x.fr"), record(domain="decathlon.fr")
        assert choose_company([bare, other, same], "decathlon.fr") is same
        assert choose_company([other, bare], "decathlon.fr") is bare

    def test_deux_domaines_font_deux_entreprises(self) -> None:
        assert choose_company([record(domain="x.fr")], "decathlon.fr") is None


class TestFusion:
    def test_une_entreprise_sans_rien_a_chercher_nait_sans_logo(self) -> None:
        created = new_company(uuid4(), sighting(), now=NOW)
        assert created.logo_status is LogoStatus.MISSING
        assert created.logo_next_attempt_at is None

    def test_une_entreprise_avec_un_logo_a_chercher(self) -> None:
        created = new_company(uuid4(), sighting(logo_url=INDEED), now=NOW)
        assert created.logo_status is LogoStatus.PENDING
        assert created.logo_next_attempt_at == NOW
        assert created.source_logo_url == INDEED

    def test_un_domaine_suffit_a_chercher_une_icone(self) -> None:
        created = new_company(uuid4(), sighting(domain="decathlon.fr"), now=NOW)
        assert created.logo_status is LogoStatus.PENDING

    def test_on_complete_sans_remplacer(self) -> None:
        known = record(domain="decathlon.fr", website="https://decathlon.fr")
        merged = merge_sighting(
            known, sighting(domain="autre.fr", website="https://autre.fr"), now=NOW
        )
        assert merged == known

    def test_un_domaine_relance_une_entreprise_sans_logo(self) -> None:
        missing = record(logo_status=LogoStatus.MISSING)
        merged = merge_sighting(
            missing, sighting(domain="decathlon.fr", website="https://decathlon.fr"), now=NOW
        )
        assert merged.logo_status is LogoStatus.PENDING
        assert merged.website == "https://decathlon.fr"

    def test_un_domaine_ne_relance_pas_une_recherche_en_cours(self) -> None:
        pending = record(logo_next_attempt_at=NOW + timedelta(hours=1))
        merged = merge_sighting(pending, sighting(domain="decathlon.fr"), now=NOW)
        assert merged.logo_next_attempt_at == NOW + timedelta(hours=1)

    def test_le_logo_de_la_v1_entre_une_fois(self) -> None:
        merged = merge_sighting(
            record(logo_status=LogoStatus.MISSING), sighting(v1_id=7, logo_url=INDEED), now=NOW
        )
        assert (merged.v1_id, merged.v1_logo_url, merged.logo_status) == (
            7,
            INDEED,
            LogoStatus.PENDING,
        )
        again = merge_sighting(merged, sighting(v1_id=8, logo_url=HELLOWORK), now=NOW)
        assert again == merged

    def test_une_ligne_v1_sans_logo(self) -> None:
        merged = merge_sighting(record(logo_status=LogoStatus.MISSING), sighting(v1_id=7), now=NOW)
        assert merged.logo_status is LogoStatus.MISSING

    def test_un_meilleur_logo_de_source_remplace_un_moins_bon(self) -> None:
        known = record(source_logo_url=HELLOWORK, logo_status=LogoStatus.MISSING)
        merged = merge_sighting(known, sighting(logo_url=INDEED), now=NOW)
        assert merged.source_logo_url == INDEED
        assert merged.logo_status is LogoStatus.PENDING
        assert merge_sighting(merged, sighting(logo_url=FT), now=NOW) == merged

    def test_un_logo_quelconque_passe_apres_les_sources_connues(self) -> None:
        known = record(source_logo_url="https://cdn.example.com/a.png")
        assert merge_sighting(known, sighting(logo_url=FT), now=NOW).source_logo_url == FT

    def test_un_logo_en_stock_n_est_pas_recherche_de_nouveau(self) -> None:
        ready = record(logo_status=LogoStatus.READY, logo_hash="h", source_logo_url=HELLOWORK)
        merged = merge_sighting(ready, sighting(logo_url=INDEED), now=NOW)
        assert merged.source_logo_url == INDEED
        assert merged.logo_status is LogoStatus.READY
        assert merged.has_logo

    def test_une_adresse_rejetee_ne_relance_rien(self) -> None:
        missing = record(logo_status=LogoStatus.MISSING, logo_rejected_urls=(INDEED,))
        merged = merge_sighting(missing, sighting(logo_url=INDEED), now=NOW)
        assert merged.logo_status is LogoStatus.MISSING

    def test_la_meme_adresse_ne_change_rien(self) -> None:
        known = record(source_logo_url=INDEED)
        assert merge_sighting(known, sighting(logo_url=INDEED), now=NOW) == known


class TestCandidats:
    def test_ordre_et_filtres(self) -> None:
        candidates = logo_candidates(
            record(
                source_logo_url=INDEED,
                v1_logo_url="https://www.google.com/s2/favicons?domain=x.fr&sz=256",
            )
        )
        assert candidates == [
            LogoCandidate(INDEED, LogoOrigin.SOURCE),
            LogoCandidate(
                "https://www.google.com/s2/favicons?domain=x.fr&sz=256", LogoOrigin.FAVICON
            ),
        ]

    def test_v1_doublons_rejetees_et_generiques_ecartes(self) -> None:
        assert logo_candidates(record(source_logo_url=INDEED, v1_logo_url=INDEED)) == [
            LogoCandidate(INDEED, LogoOrigin.SOURCE)
        ]
        assert logo_candidates(record(v1_logo_url=HELLOWORK)) == [
            LogoCandidate(HELLOWORK, LogoOrigin.V1)
        ]
        assert logo_candidates(record(v1_logo_url=INDEED, logo_rejected_urls=(INDEED,))) == []
        assert logo_candidates(record(v1_logo_url="https://static.licdn.com/aero/ghost")) == []
        assert logo_candidates(record(v1_logo_url="data:image/png;base64,AAAA")) == []

    @pytest.mark.parametrize(
        ("url", "placeholder"),
        [
            ("https://static.licdn.com/aero-v1/sc/h/abc", True),
            ("https://x.fr/img/default-logo.png", True),
            ("https://x.fr/no_logo.png", True),
            (INDEED, False),
        ],
    )
    def test_images_generiques(self, url: str, placeholder: bool) -> None:
        assert is_placeholder_url(url) is placeholder

    def test_icones_du_site(self) -> None:
        assert site_icon_urls("decathlon.fr") == (
            "https://decathlon.fr/apple-touch-icon.png",
            "https://www.google.com/s2/favicons?domain=decathlon.fr&sz=256",
        )

    def test_icones_declarees_par_la_page_d_accueil(self) -> None:
        html = """<html><head>
        <link rel="icon" href="/favicon-32.png" sizes="32x32">
        <link rel="apple-touch-icon" href="/apple.png">
        <link rel="icon" type="image/svg+xml" href="/logo.svg">
        <link rel="icon" href="https://cdn.x.fr/i192.png" sizes="192x192 any">
        <link rel="stylesheet" href="/a.css">
        <link rel="icon">
        <link rel="shortcut icon" href="javascript:alert(1)">
        <meta name="x"><link rel="icon" href="/favicon.ico">
        </head></html>"""
        assert icon_links(html, "https://www.x.fr/") == [
            "https://cdn.x.fr/i192.png",
            "https://www.x.fr/apple.png",
            "https://www.x.fr/favicon-32.png",
            "https://www.x.fr/favicon.ico",
        ]


class TestJugement:
    @pytest.mark.parametrize(
        ("traits", "origin", "verdict"),
        [
            (ImageTraits(256, 256, 40.0), LogoOrigin.SOURCE, None),
            (ImageTraits(160, 60, 40.0, 0.4), LogoOrigin.SOURCE, None),
            (ImageTraits(1, 1, 0.0), LogoOrigin.SOURCE, LogoRejection.TOO_SMALL),
            (ImageTraits(40, 40, 40.0), LogoOrigin.SOURCE, LogoRejection.TOO_SMALL),
            (ImageTraits(64, 64, 40.0), LogoOrigin.FAVICON, LogoRejection.TOO_SMALL),
            (ImageTraits(180, 180, 40.0), LogoOrigin.FAVICON, None),
            (ImageTraits(300, 20, 40.0), LogoOrigin.SOURCE, LogoRejection.TOO_WIDE),
            (ImageTraits(200, 200, 2.0), LogoOrigin.V1, LogoRejection.BLANK),
            (ImageTraits(200, 200, 2.0, 0.5), LogoOrigin.V1, None),
            (ImageTraits(200, 200, 30.0, 0.005), LogoOrigin.V1, LogoRejection.BLANK),
        ],
    )
    def test_verdicts(
        self, traits: ImageTraits, origin: LogoOrigin, verdict: LogoRejection | None
    ) -> None:
        assert judge_logo(traits, origin) == verdict


class TestIssue:
    def test_un_logo_trouve(self) -> None:
        settled = settle_logo(
            record(logo_attempts=2, logo_next_attempt_at=NOW),
            LogoAttempt("abc", LogoOrigin.V1, (HELLOWORK,)),
            now=NOW,
        )
        assert settled.logo_status is LogoStatus.READY
        assert (settled.logo_hash, settled.logo_origin, settled.logo_attempts) == (
            "abc",
            LogoOrigin.V1,
            0,
        )
        assert settled.logo_rejected_urls == (HELLOWORK,)
        assert settled.logo_checked_at == NOW

    def test_un_echec_passager_se_reessaie_de_plus_en_plus_tard(self) -> None:
        current = record()
        for delay in RETRY_DELAYS:
            current = settle_logo(current, LogoAttempt(transient_failure=True), now=NOW)
            assert current.logo_status is LogoStatus.PENDING
            assert current.logo_next_attempt_at == NOW + delay
        given_up = settle_logo(current, LogoAttempt(transient_failure=True), now=NOW)
        assert given_up.logo_status is LogoStatus.MISSING
        assert given_up.logo_next_attempt_at is None

    def test_rien_d_acceptable(self) -> None:
        settled = settle_logo(record(), LogoAttempt(rejected_urls=(INDEED,)), now=NOW)
        assert settled.logo_status is LogoStatus.MISSING

    def test_un_logo_deja_en_stock_reste(self) -> None:
        ready = record(logo_status=LogoStatus.READY, logo_hash="h")
        assert settle_logo(ready, LogoAttempt(), now=NOW).logo_status is LogoStatus.READY

    def test_les_adresses_rejetees_sont_bornees(self) -> None:
        many = tuple(f"https://x.fr/{i}.png" for i in range(20))
        settled = settle_logo(record(), LogoAttempt(rejected_urls=many), now=NOW)
        assert settled.logo_rejected_urls == many[-MAX_REJECTED_URLS:]

    def test_cle_de_stockage(self) -> None:
        assert logo_object_key("abc", 128) == "abc/128.webp"
