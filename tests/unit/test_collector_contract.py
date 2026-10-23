"""Les deux rives du contrat de remise disent la même chose.

Le collecteur met une ligne JobSpy au format (`karria.collector`) ; l'API
la relit (`offers.infrastructure.landing` puis `offers.domain.handoff`).
Ce test traverse le contrat de bout en bout, sans réseau.
"""

from __future__ import annotations

from datetime import date

import pytest

from karria.collector.contract import BATCH_MAX_POSTINGS, FIELDS, HANDOFF_VERSION, encode_batch
from karria.collector.france_travail import expired_record, offer_to_record
from karria.collector.sources import row_to_record
from karria.modules.offers.domain import BATCH_MAX_POSTINGS as API_BATCH_MAX_POSTINGS
from karria.modules.offers.domain import HANDOFF_VERSION as API_HANDOFF_VERSION
from karria.modules.offers.domain import RawPosting, Source, posting_from_record
from karria.modules.offers.infrastructure.landing import decode_batch

pytestmark = pytest.mark.unit

ROW = {
    "id": "hw-84683722",
    "site": "hellowork",
    "job_url": "https://www.hellowork.com/fr-fr/emplois/84683722.html",
    "title": "Serveur - Serveuse Mariage H/F",
    "company": "Temporis Interim",
    "location": "Vienne, 38, France",
    "date_posted": date(2026, 10, 22),
    "job_type": "temporary, fulltime",
    "interval": "hourly",
    "min_amount": 14.54,
    "max_amount": 14.54,
    "currency": "EUR",
    "is_remote": False,
    "description": "Tu apprécies les belles réceptions ?",
}


def test_les_deux_rives_parlent_la_meme_version() -> None:
    assert HANDOFF_VERSION == API_HANDOFF_VERSION


def test_les_cles_du_collecteur_sont_les_champs_de_l_annonce_brute() -> None:
    assert set(FIELDS) == set(RawPosting.__dataclass_fields__)


def test_une_ligne_jobspy_traverse_le_contrat_a_l_identique() -> None:
    record = row_to_record(ROW, "hellowork")
    assert record is not None

    (decoded,) = decode_batch(encode_batch([record]))
    posting = posting_from_record(decoded)

    assert posting is not None
    assert posting.source is Source.HELLOWORK
    assert posting.source_id == "hw-84683722"
    assert posting.title == "Serveur - Serveuse Mariage H/F"
    assert posting.company_name == "Temporis Interim"
    assert posting.location_label == "Vienne, 38, France"
    assert posting.date_posted == date(2026, 10, 22)
    assert posting.job_types == ("temporary", "fulltime")
    assert (posting.salary_min, posting.salary_interval) == (14.54, "hourly")
    assert posting.is_remote is False
    assert posting.raw["company"] == "Temporis Interim"


def test_un_lot_vide_reste_vide_des_deux_cotes() -> None:
    assert decode_batch(encode_batch([])) == []


def test_une_annonce_france_travail_traverse_le_contrat_avec_ses_indices() -> None:
    record = offer_to_record(
        {
            "id": "215NPDX",
            "intitule": "Cuisinier / Cuisinière",
            "description": "Vous préparez les plats.",
            "dateCreation": "2026-10-22T12:28:37.803Z",
            "lieuTravail": {
                "libelle": "86 - FLEURE",
                "latitude": 46.47,
                "longitude": 0.52,
                "codePostal": "86340",
            },
            "typeContrat": "CDI",
            "dureeTravailLibelleConverti": "Temps plein",
            "experienceExige": "E",
            "experienceLibelle": "1 An(s)",
            "salaire": {"libelle": "Mensuel de 2300.0 Euros"},
            "romeCode": "G1609",
        }
    )
    assert record is not None

    (decoded,) = decode_batch(encode_batch([record]))
    posting = posting_from_record(decoded)

    assert posting is not None
    assert posting.source is Source.FRANCE_TRAVAIL
    assert posting.contract_type == "cdi"
    assert posting.schedule == "temps_plein"
    assert posting.experience_years == 1.0
    assert posting.salary_text == "Mensuel de 2300.0 Euros"
    assert (posting.latitude, posting.longitude, posting.postal_code) == (46.47, 0.52, "86340")
    assert posting.posted_at is not None
    assert posting.posted_at.isoformat() == "2026-10-22T10:28:37+00:00"
    assert posting.location_label == "Fleure (86)"
    assert posting.raw["romeCode"] == "G1609"


def test_une_ligne_de_retrait_traverse_le_contrat() -> None:
    (decoded,) = decode_batch(encode_batch([expired_record("215NPDX")]))
    posting = posting_from_record(decoded)

    assert posting is not None
    assert (posting.source, posting.source_id, posting.expired, posting.title) == (
        Source.FRANCE_TRAVAIL,
        "215NPDX",
        True,
        "",
    )


def test_la_borne_de_lot_est_la_meme_des_deux_cotes() -> None:
    assert BATCH_MAX_POSTINGS == API_BATCH_MAX_POSTINGS
