"""Le contrat de remise, côté API : une ligne lue défensivement, jamais devinée."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from karria.modules.offers.domain import Source, posting_from_record

pytestmark = pytest.mark.unit


def record(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"source": "indeed", "source_id": "in-1", "title": "Serveur H/F"}
    base.update(changes)
    return base


def test_le_minimum_suffit() -> None:
    posting = posting_from_record(record())

    assert posting is not None
    assert (posting.source, posting.source_id, posting.title) == (
        Source.INDEED,
        "in-1",
        "Serveur H/F",
    )
    assert posting.description == ""
    assert posting.job_types == ()
    assert posting.raw == {}


def test_une_ligne_complete_est_lue_champ_par_champ() -> None:
    posting = posting_from_record(
        record(
            description="  Service en salle.  ",
            company_name="Le Bistrot",
            location_label="Paris (75)",
            date_posted="2026-10-21T08:00:00",
            job_types=["fulltime", 3, "contract"],
            is_remote=True,
            salary_min=12,
            salary_max=14.5,
            salary_interval="hourly",
            raw={"site": "indeed"},
        )
    )

    assert posting is not None
    assert posting.description == "Service en salle."
    assert posting.date_posted == date(2026, 10, 21)
    assert posting.job_types == ("fulltime", "contract")
    assert posting.is_remote is True
    assert (posting.salary_min, posting.salary_max) == (12.0, 14.5)
    assert posting.raw == {"site": "indeed"}


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "monster"},
        {"source": "manual"},
        {"source_id": "  "},
        {"title": 12},
        {"title": ""},
    ],
)
def test_une_ligne_inexploitable_est_ecartee(bad: dict[str, Any]) -> None:
    assert posting_from_record(record(**bad)) is None


def test_un_type_inattendu_vaut_absent() -> None:
    posting = posting_from_record(
        record(
            date_posted="hier",
            job_types="fulltime",
            is_remote="oui",
            salary_min=True,
            salary_max="1200",
            company_name=["?"],
            raw=["?"],
        )
    )

    assert posting is not None
    assert posting.date_posted is None
    assert posting.job_types == ()
    assert posting.is_remote is None
    assert (posting.salary_min, posting.salary_max) == (None, None)
    assert posting.company_name is None
    assert posting.raw == {}


def test_une_date_qui_n_est_pas_un_texte_est_ignoree() -> None:
    posting = posting_from_record(record(date_posted=20261021))

    assert posting is not None
    assert posting.date_posted is None


def test_les_indices_structures_sont_lus() -> None:
    posting = posting_from_record(
        record(
            contract_type="cdi",
            schedule="temps_plein",
            experience="debutant",
            experience_years=2,
            salary_text="Mensuel de 2300.0 Euros",
            latitude=46.47,
            longitude=0.52,
            postal_code="86340",
            posted_at="2026-10-22T12:28:37+00:00",
            updated_at="2026-10-22T12:30:00Z",
        )
    )

    assert posting is not None
    assert (posting.contract_type, posting.schedule, posting.experience) == (
        "cdi",
        "temps_plein",
        "debutant",
    )
    assert posting.experience_years == 2.0
    assert posting.salary_text == "Mensuel de 2300.0 Euros"
    assert (posting.latitude, posting.longitude, posting.postal_code) == (46.47, 0.52, "86340")
    assert posting.posted_at is not None
    assert posting.posted_at.hour == 12
    assert posting.updated_at is not None
    assert posting.updated_at.minute == 30
    assert posting.expired is False


@pytest.mark.parametrize("value", ["hier", "2026-10-22T12:28:37", 12, None])
def test_un_instant_illisible_ou_naif_vaut_absent(value: Any) -> None:
    posting = posting_from_record(record(posted_at=value, updated_at=value))

    assert posting is not None
    assert (posting.posted_at, posting.updated_at) == (None, None)


def test_une_ligne_de_retrait_n_a_besoin_que_de_la_source_et_de_l_identifiant() -> None:
    posting = posting_from_record(
        {"source": "france_travail", "source_id": "215NPDX", "expired": True}
    )

    assert posting is not None
    assert (posting.expired, posting.title) == (True, "")


def test_un_retrait_mal_forme_n_est_pas_un_retrait() -> None:
    posting = posting_from_record(record(expired="oui"))

    assert posting is not None
    assert posting.expired is False
    assert (
        posting_from_record({"source": "france_travail", "source_id": "X", "expired": "oui"})
        is None
    )
