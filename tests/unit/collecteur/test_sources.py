"""Les sites, par JobSpy : une ligne pandas devient une annonce au format de remise."""

from __future__ import annotations

import math
import sys
import types
from datetime import UTC, date, datetime
from typing import Any

import pytest
from bs4 import BeautifulSoup

from karria.collector.sources import Target, clean, patch_hellowork, row_to_record, scrape

pytestmark = pytest.mark.unit

TARGET = Target(key="serveur|paris", search_term="serveur", location="Paris", distance_km=32)


class NumpyLike:
    """Un scalaire numpy : il a `.item()`."""

    def __init__(self, value: Any, *, broken: bool = False) -> None:
        self.value = value
        self.broken = broken

    def item(self) -> Any:
        if self.broken:
            raise ValueError("pas de valeur")
        return self.value

    def __str__(self) -> str:
        return "numpy-ish"


class TestNettoyage:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (math.nan, None),
            (date(2026, 10, 21), "2026-10-21"),
            (datetime(2026, 10, 21, 8, tzinfo=UTC), "2026-10-21T08:00:00+00:00"),
            ("texte", "texte"),
            (3, 3),
            (1.5, 1.5),
            (True, True),
            (("a", math.nan), ["a", None]),
            (NumpyLike(7), 7),
            (NumpyLike(7, broken=True), "numpy-ish"),
            ({"x": 1}, "{'x': 1}"),
        ],
    )
    def test_une_valeur_pandas_devient_une_valeur_json(self, value: Any, expected: Any) -> None:
        assert clean(value) == expected


class TestLigne:
    def test_une_ligne_complete(self) -> None:
        row = {
            "id": "hw-1",
            "site": "hellowork",
            "title": " Serveur H/F ",
            "description": "Service en salle.",
            "company": "Le Bistrot",
            "company_logo": "https://logo",
            "company_url": "https://bistrot",
            "location": "Lyon, 69, France",
            "date_posted": date(2026, 10, 21),
            "job_type": "temporary, fulltime",
            "is_remote": False,
            "work_from_home_type": None,
            "job_level": math.nan,
            "min_amount": 14.5,
            "max_amount": "15,2",
            "interval": "hourly",
            "currency": "EUR",
            "job_url": "https://hw/1",
            "job_url_direct": None,
        }

        record = row_to_record(row, "hellowork")

        assert record is not None
        assert record["source"] == "hellowork"
        assert record["title"] == "Serveur H/F"
        assert record["date_posted"] == "2026-10-21"
        assert record["job_types"] == ["temporary", "fulltime"]
        assert record["is_remote"] is False
        assert record["job_level"] is None
        assert record["salary_min"] == 14.5
        assert record["salary_max"] is None
        assert record["raw"]["site"] == "hellowork"
        assert "description" not in record["raw"]

    def test_une_ligne_sans_identifiant_ou_sans_titre_est_ecartee(self) -> None:
        assert row_to_record({"id": "1", "title": " "}, "indeed") is None
        assert row_to_record({"title": "Serveur"}, "indeed") is None

    def test_les_champs_absents_restent_absents(self) -> None:
        record = row_to_record({"id": "in-1", "title": "Serveur", "is_remote": 1}, "indeed")

        assert record is not None
        assert record["description"] == ""
        assert record["date_posted"] is None
        assert record["job_types"] == []
        assert record["is_remote"] is None
        assert record["salary_min"] is None


class FakeHelloWork:
    """La classe de JobSpy, réduite à ce que le correctif touche."""

    def _process_job(self, card: Any) -> str:
        return " | ".join(p.get_text() for p in card.select("a[data-cy=offerTitle] h3 p"))


def fake_jobspy(monkeypatch: pytest.MonkeyPatch, frame_rows: list[dict[str, Any]]) -> list[Any]:
    calls: list[dict[str, Any]] = []

    class Frame:
        def to_dict(self, orient: str) -> list[dict[str, Any]]:
            assert orient == "records"
            return frame_rows

    def scrape_jobs(**kwargs: Any) -> Frame:
        calls.append(kwargs)
        return Frame()

    hellowork = type("FakeHW", (FakeHelloWork,), {})
    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=scrape_jobs))
    monkeypatch.setitem(sys.modules, "jobspy.hellowork", types.SimpleNamespace(HelloWork=hellowork))
    return calls


class TestCorrectifHelloWork:
    CARD = (
        '<li><a data-cy="offerTitle"><h3><span>Serveur H/F</span><span>Le Bistrot</span></h3>'
        "</a></li>"
    )

    def test_les_span_du_titre_redeviennent_des_p_et_le_correctif_est_idempotent(self) -> None:
        cls = type("HW", (FakeHelloWork,), {})
        patch_hellowork(cls)
        patched = cls._process_job
        patch_hellowork(cls)

        card = BeautifulSoup(self.CARD, "html.parser")

        assert cls._process_job is patched
        assert cls()._process_job(card) == "Serveur H/F | Le Bistrot"

    def test_une_carte_sans_lien_passe_telle_quelle(self) -> None:
        cls = type("HW", (FakeHelloWork,), {})
        patch_hellowork(cls)

        assert cls()._process_job(BeautifulSoup("<li></li>", "html.parser")) == ""


class TestRequete:
    def test_la_requete_parle_en_miles_et_demande_les_descriptions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = fake_jobspy(
            monkeypatch, [{"id": "in-1", "title": "Serveur"}, {"id": None, "title": "x"}]
        )

        records = scrape(
            "indeed",
            TARGET,
            results_wanted=40,
            hours_old=72,
            proxies=["http://p:1"],
            timeout_seconds=30,
        )

        assert [r["source_id"] for r in records] == ["in-1"]
        (call,) = calls
        assert call["distance"] == 20
        assert call["location"] == "Paris"
        assert call["fetch_description"] is True
        assert call["country_indeed"] == "france"
        assert call["proxies"] == ["http://p:1"]
        assert (call["results_wanted"], call["hours_old"]) == (40, 72)

    def test_sans_rayon_ni_mandataire_les_defauts_du_site(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = fake_jobspy(monkeypatch, [])
        france = Target(key="k", search_term="serveur", location=" France", distance_km=None)

        assert (
            scrape(
                "hellowork", france, results_wanted=1, hours_old=1, proxies=[], timeout_seconds=1
            )
            == []
        )
        scrape("indeed", france, results_wanted=1, hours_old=1, proxies=[], timeout_seconds=1)

        assert calls[0]["distance"] is None
        assert calls[0]["proxies"] is None
        assert calls[0]["location"] is None
        assert calls[1]["location"] == " France"
