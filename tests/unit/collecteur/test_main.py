"""Le point d'entrée du collecteur : deux boucles, un passage, un balayage ; une API muette ne le fait pas tomber."""

from __future__ import annotations

from typing import Any

import pytest

import karria.collector.__main__ as entry
from karria.collector.api_client import ApiError
from karria.collector.settings import CollectorSettings

pytestmark = pytest.mark.unit


class StopLoopError(Exception):
    """Sort de la boucle infinie."""


class FakeCollector:
    def __init__(self, outcomes: list[Exception | None]) -> None:
        self.outcomes = outcomes
        self.runs = 0
        self.sweeps: list[int | None] = []
        self.due = False

    def _next(self) -> None:
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome

    async def run_once(self) -> None:
        self.runs += 1
        self._next()

    async def poll_once(self) -> None:
        self.runs += 1
        self._next()

    async def sweep(self, days: int | None = None) -> None:
        self.sweeps.append(days)
        self._next()

    def sweep_due(self, now: Any) -> bool:
        return self.due


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "jobspy": FakeCollector([]),
        "france_travail": FakeCollector([]),
        "sleeps": [],
    }

    def build_collector(settings: CollectorSettings, api: Any) -> FakeCollector:
        state["api"] = api
        return state["jobspy"]

    def build_ft(settings: CollectorSettings, api: Any, client: Any) -> FakeCollector:
        state["client"] = client
        return state["france_travail"]

    async def sleep(seconds: float) -> None:
        state["sleeps"].append(seconds)
        raise StopLoopError

    monkeypatch.setattr(entry, "Collector", build_collector)
    monkeypatch.setattr(entry, "FranceTravailCollector", build_ft)
    monkeypatch.setattr(entry.asyncio, "sleep", sleep)
    monkeypatch.setenv("COLLECTOR__TOKEN", "t")
    monkeypatch.setenv("COLLECTOR__INTERVAL_SECONDS", "42")
    monkeypatch.setenv("COLLECTOR__FRANCE_TRAVAIL_CLIENT_ID", "id")
    monkeypatch.setenv("COLLECTOR__FRANCE_TRAVAIL_CLIENT_SECRET", "s")
    monkeypatch.setenv("COLLECTOR__FRANCE_TRAVAIL_POLL_SECONDS", "180")
    return state


async def test_un_passage_unique_fait_un_passage_de_chaque_et_rend_zero(
    fake: dict[str, Any],
) -> None:
    assert await entry.main(["--once"]) == 0
    assert fake["jobspy"].runs == 1
    assert fake["france_travail"].runs == 1
    assert fake["api"]._token == "t"  # noqa: S105 - jeton factice


async def test_un_passage_unique_sans_api_rend_un(fake: dict[str, Any]) -> None:
    fake["jobspy"] = FakeCollector([ApiError(503, "x")])

    assert await entry.main(["--once"]) == 1


async def test_un_passage_france_travail_sans_api_rend_un(fake: dict[str, Any]) -> None:
    fake["france_travail"] = FakeCollector([ApiError(503, "x")])

    assert await entry.main(["--once"]) == 1


async def test_en_boucle_une_api_muette_n_arrete_aucune_boucle(fake: dict[str, Any]) -> None:
    fake["jobspy"] = FakeCollector([ApiError(None, "x")])
    fake["france_travail"] = FakeCollector([ApiError(None, "x")])

    with pytest.raises(StopLoopError):
        await entry.main([])

    assert fake["jobspy"].runs == 1
    assert fake["france_travail"].runs == 1
    assert sorted(fake["sleeps"]) == [42.0, 180.0]


async def test_le_balayage_quotidien_precede_le_passage_quand_il_est_du(
    fake: dict[str, Any],
) -> None:
    fake["france_travail"].due = True

    assert await entry.main(["--once"]) == 0
    assert fake["france_travail"].sweeps == [None]
    assert fake["france_travail"].runs == 1


async def test_un_balayage_explicite_ne_fait_que_cela(fake: dict[str, Any]) -> None:
    assert await entry.main(["--ft-sweep", "3"]) == 0
    assert fake["france_travail"].sweeps == [3]
    assert fake["jobspy"].runs == 0
    assert fake["france_travail"].runs == 0


async def test_un_balayage_sans_api_rend_un(fake: dict[str, Any]) -> None:
    fake["france_travail"] = FakeCollector([ApiError(502, "x")])

    assert await entry.main(["--ft-sweep", "1"]) == 1


async def test_sans_identifiants_france_travail_est_absent(
    fake: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COLLECTOR__FRANCE_TRAVAIL_CLIENT_ID")

    assert await entry.main(["--once"]) == 0
    assert fake["france_travail"].runs == 0
    assert await entry.main(["--ft-sweep", "1"]) == 2


def test_la_configuration_par_defaut_est_prudente() -> None:
    settings = CollectorSettings()

    assert settings.sites == ["indeed", "hellowork"]
    assert settings.token.get_secret_value() == ""
    assert settings.pause_seconds > 0
    assert settings.france_travail is None
    assert settings.france_travail_poll_seconds >= 60


def test_les_identifiants_france_travail_forment_des_credentials() -> None:
    settings = CollectorSettings(
        france_travail_client_id="id",
        france_travail_client_secret="secret",  # type: ignore[arg-type]  # pydantic enveloppe
    )
    credentials = settings.france_travail

    assert credentials is not None
    assert credentials.client_secret == "secret"  # noqa: S105 - factice
    assert (
        credentials.offer_url == "https://api.francetravail.io/partenaire/offresdemploi/v2/offres"
    )
    assert "francetravail" in credentials.token_url
