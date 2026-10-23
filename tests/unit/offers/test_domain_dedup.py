"""Doublons exacts et quasi-doublons : condensat, SimHash, bandes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from karria.modules.offers.domain import (
    RawPosting,
    Source,
    bands,
    canonical_text,
    content_hash,
    hamming,
    is_near_duplicate,
    normalize_posting,
    posting_hash,
    simhash64,
)
from karria.modules.offers.domain.dedup import NEAR_DUPLICATE_MAX_DISTANCE

pytestmark = pytest.mark.unit

DESCRIPTION = (
    "Rejoignez notre brasserie parisienne au cœur du onzième arrondissement. Vous assurez "
    "le service en salle et en terrasse, la prise de commande, le conseil sur les vins et "
    "l'encaissement. Vous participez à la mise en place avant chaque service et au "
    "rangement de la salle en fin de journée. Vous travaillez en binôme avec le chef de "
    "rang et vous veillez à la satisfaction de chaque client. Profil : une première "
    "expérience en restauration est appréciée, le sens du contact et la ponctualité sont "
    "indispensables. Conditions : poste en CDI, trente-neuf heures par semaine, deux "
    "jours de repos consécutifs, repas pris sur place, prime de fin d'année, mutuelle "
    "d'entreprise prise en charge à moitié, évolution possible vers un poste de chef de "
    "rang après une année. Prise de poste dès que possible, entretien sur place avec le "
    "directeur de salle."
)


class TestCondensat:
    def test_le_texte_canonique_est_plie_et_sans_ponctuation(self) -> None:
        assert canonical_text("Serveur H/F", "Café Été", "Service, encaissement !") == (
            "serveur h f cafe ete service encaissement"
        )

    def test_la_casse_et_les_accents_ne_changent_pas_le_condensat(self) -> None:
        assert content_hash("SERVEUR", "Café", "Salle") == content_hash("serveur", "cafe", "salle")

    def test_un_employeur_absent_est_permis(self) -> None:
        assert content_hash("Serveur", None, "Salle") == content_hash("Serveur", "", "Salle")

    def test_le_condensat_dune_annonce_brute_est_celui_de_loffre_normalisee(self) -> None:
        raw = RawPosting(
            source=Source.INDEED,
            source_id="in-42",
            title="  Serveur   H/F ",
            description="Service en salle.\r\n\n\n\nCDI.",
            company_name=" Le Bistrot ",
        )
        offer = normalize_posting(raw, now=datetime(2026, 10, 22, tzinfo=UTC), place=None)
        company = offer.company.name if offer.company else None
        assert posting_hash(raw) == content_hash(offer.title, company, offer.description)

    def test_une_annonce_sans_employeur_a_un_condensat(self) -> None:
        raw = RawPosting(
            source=Source.INDEED, source_id="1", title="Serveur", description="x", company_name="  "
        )
        assert posting_hash(raw) == content_hash("Serveur", None, "x")


class TestSimHash:
    def test_une_retouche_reste_un_quasi_doublon(self) -> None:
        a = simhash64(canonical_text("Serveur H/F", "Le Bistrot", DESCRIPTION))
        b = simhash64(canonical_text("Serveur H/F", "Le Bistrot", DESCRIPTION + " Mutuelle."))
        assert hamming(a, b) <= NEAR_DUPLICATE_MAX_DISTANCE
        assert is_near_duplicate(a, b)

    def test_deux_postes_differents_ne_sont_pas_des_quasi_doublons(self) -> None:
        a = simhash64(canonical_text("Serveur H/F", "Le Bistrot", DESCRIPTION))
        b = simhash64(
            canonical_text(
                "Développeur Python",
                "Atos",
                "Vous développez des services web en Python et Django au sein d'une équipe agile.",
            )
        )
        assert not is_near_duplicate(a, b)

    def test_un_texte_vide_a_une_empreinte_nulle(self) -> None:
        assert simhash64("") == 0

    def test_un_texte_tres_court_a_une_empreinte(self) -> None:
        assert simhash64("serveur paris") != 0

    def test_les_quatre_bandes_recomposent_lempreinte(self) -> None:
        value = simhash64(canonical_text("Serveur", None, DESCRIPTION))
        b0, b1, b2, b3 = bands(value)
        assert b0 | b1 << 16 | b2 << 32 | b3 << 48 == value
        assert all(0 <= band < 1 << 16 for band in (b0, b1, b2, b3))
