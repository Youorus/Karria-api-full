"""Le client de l'API, côté collecteur : deux routes, des reprises bornées."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from karria.collector import api_client
from karria.collector.api_client import ApiError, CollectorApi

pytestmark = pytest.mark.unit


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class FakeNetwork:
    """Remplace `urlopen` : rend ou lève dans l'ordre, et garde les requêtes."""

    def __init__(self, *outcomes: bytes | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(outcome)


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://api", code, "x", {}, io.BytesIO(b"motif"))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(api_client.asyncio, "sleep", sleep)
    return waits


def client(monkeypatch: pytest.MonkeyPatch, network: FakeNetwork) -> CollectorApi:
    monkeypatch.setattr(api_client.urllib.request, "urlopen", network)
    return CollectorApi("http://api/api/v1/", "jeton", timeout_seconds=5)


async def test_les_cibles_louees_sont_lues(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "targets": [
            {"key": "a", "search_term": "serveur", "location": "Paris", "distance_km": 20},
            {"key": "b", "search_term": "cuisinier", "location": "France", "distance_km": None},
        ]
    }
    network = FakeNetwork(json.dumps(body).encode())

    targets = await client(monkeypatch, network).lease_targets(7)

    assert [(t.key, t.distance_km) for t in targets] == [("a", 20), ("b", None)]
    request = network.requests[0]
    assert request.full_url == "http://api/api/v1/internal/offers/targets?limit=7"
    assert request.get_header("Authorization") == "Bearer jeton"


async def test_une_reponse_vide_ne_donne_aucune_cible(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await client(monkeypatch, FakeNetwork(b"")).lease_targets(1) == []


async def test_un_lot_part_avec_la_version_et_la_cible(monkeypatch: pytest.MonkeyPatch) -> None:
    network = FakeNetwork(b'{"key": "collector/a.gz"}')

    key = await client(monkeypatch, network).push_batch(b"gz", target_key="a")

    assert key == "collector/a.gz"
    request = network.requests[0]
    assert request.get_method() == "POST"
    assert request.data == b"gz"
    assert request.get_header("X-handoff-version") == "1"
    assert request.get_header("X-target-key") == "a"


async def test_un_lot_sans_cible_n_a_pas_d_en_tete_de_cible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = FakeNetwork(b'{"key": "k"}')

    await client(monkeypatch, network).push_batch(b"gz", target_key=None)

    assert network.requests[0].get_header("X-target-key") is None


async def test_une_panne_passagere_se_reprend(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    network = FakeNetwork(http_error(503), urllib.error.URLError("dns"), b'{"targets": []}')

    assert await client(monkeypatch, network).lease_targets(1) == []
    assert waits == [1, 2]


async def test_un_refus_ne_se_reprend_pas(monkeypatch: pytest.MonkeyPatch) -> None:
    network = FakeNetwork(http_error(401))

    with pytest.raises(ApiError) as error:
        await client(monkeypatch, network).lease_targets(1)

    assert error.value.status == 401
    assert "motif" in str(error.value)
    assert len(network.requests) == 1


async def test_apres_trois_essais_la_panne_remonte(monkeypatch: pytest.MonkeyPatch) -> None:
    network = FakeNetwork(TimeoutError(), TimeoutError(), TimeoutError())

    with pytest.raises(ApiError) as error:
        await client(monkeypatch, network).push_batch(b"", target_key=None)

    assert error.value.status is None
    assert len(network.requests) == 3


def test_le_detail_d_une_erreur_est_lisible() -> None:
    error: Any = ApiError(500, "boom")

    assert str(error) == "API 500: boom"
