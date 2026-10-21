"""La passerelle Expo : composition des lots et lecture des tickets et reçus."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from karria.modules.devices.domain import PushEnvelope, PushError, PushNotification
from karria.modules.devices.infrastructure import ExpoPushGateway
from karria.shared.errors import DependencyUnavailableError
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError

pytestmark = pytest.mark.unit

NOTIF = PushNotification(title="Titre", body="Corps", data={"type": "X"})


class ScriptedClient(JsonClient):
    """Un client du socle qui rend une réponse préparée."""

    script: Any = None
    failing = False
    posted: ClassVar[list[tuple[str, Any]]] = []

    async def post_json(self, path: str, payload: Any) -> JsonResponse:
        ScriptedClient.posted.append((path, payload))
        if ScriptedClient.failing:
            raise OutboundError("HTTP 503", status=503)
        return JsonResponse(200, ScriptedClient.script)


def _gateway() -> ExpoPushGateway:
    return ExpoPushGateway(ScriptedClient(base_url="https://exp.host/--/api/v2"))


class TestSend:
    def setup_method(self) -> None:
        ScriptedClient.posted = []
        ScriptedClient.failing = False

    async def test_compose_le_lot_et_lit_les_tickets(self) -> None:
        ScriptedClient.script = {
            "data": [
                {"status": "ok", "id": "T1"},
                {"status": "error", "message": "x", "details": {"error": "DeviceNotRegistered"}},
                {"status": "error", "message": "y"},
            ]
        }
        envelopes = [
            PushEnvelope(token=f"ExponentPushToken[{i}]", notification=NOTIF) for i in range(3)
        ]

        tickets = await _gateway().send(envelopes)

        path, payload = ScriptedClient.posted[0]
        assert path == "/push/send"
        assert payload[0] == {
            "to": "ExponentPushToken[0]",
            "title": "Titre",
            "body": "Corps",
            "data": {"type": "X"},
            "sound": "default",
            "priority": "high",
        }
        assert tickets[0].ok
        assert tickets[0].ticket_id == "T1"
        assert not tickets[1].ok
        assert tickets[1].error is PushError.DEVICE_NOT_REGISTERED
        assert not tickets[2].ok
        assert tickets[2].error is PushError.UNKNOWN
        assert tickets[1].token == "ExponentPushToken[1]"  # noqa: S105

    async def test_un_ticket_ok_sans_identifiant_est_un_echec(self) -> None:
        ScriptedClient.script = {"data": [{"status": "ok"}]}
        tickets = await _gateway().send(
            [PushEnvelope(token="ExponentPushToken[a]", notification=NOTIF)]
        )
        assert not tickets[0].ok
        assert tickets[0].ticket_id is None

    async def test_une_reponse_malformee_est_une_panne(self) -> None:
        ScriptedClient.script = {"data": [{"status": "ok", "id": "T1"}]}
        with pytest.raises(DependencyUnavailableError):
            await _gateway().send(
                [PushEnvelope(token="ExponentPushToken[a]", notification=NOTIF)] * 2
            )
        ScriptedClient.script = {"errors": [{"code": "x"}]}
        with pytest.raises(DependencyUnavailableError):
            await _gateway().send([PushEnvelope(token="ExponentPushToken[a]", notification=NOTIF)])

    async def test_expo_injoignable_est_une_panne(self) -> None:
        ScriptedClient.failing = True
        with pytest.raises(DependencyUnavailableError) as exc:
            await _gateway().send([PushEnvelope(token="ExponentPushToken[a]", notification=NOTIF)])
        assert exc.value.details["dependency"] == "expo_push"

    def test_from_settings_pose_le_jeton_et_une_seule_tentative(self) -> None:
        gateway = ExpoPushGateway.from_settings(
            api_url="https://exp.host/--/api/v2", access_token="secret", timeout_seconds=5.0
        )
        client = gateway._client
        assert client.headers == {"Authorization": "Bearer secret"}
        assert client.attempts == 1
        anonymous = ExpoPushGateway.from_settings(
            api_url="https://exp.host/--/api/v2", access_token="", timeout_seconds=5.0
        )
        assert anonymous._client.headers == {}


class TestReceipts:
    def setup_method(self) -> None:
        ScriptedClient.posted = []
        ScriptedClient.failing = False

    async def test_lit_les_recus_par_ticket(self) -> None:
        ScriptedClient.script = {
            "data": {
                "T1": {"status": "ok"},
                "T2": {"status": "error", "details": {"error": "DeviceNotRegistered"}},
                "T3": "n'importe quoi",
            }
        }

        receipts = await _gateway().receipts(["T1", "T2", "T3"])

        assert ScriptedClient.posted[0] == ("/push/getReceipts", {"ids": ["T1", "T2", "T3"]})
        by_id = {r.ticket_id: r for r in receipts}
        assert by_id["T1"].ok
        assert by_id["T2"].error is PushError.DEVICE_NOT_REGISTERED
        assert by_id["T3"].error is PushError.UNKNOWN

    async def test_une_reponse_malformee_ou_une_panne_est_une_panne(self) -> None:
        ScriptedClient.script = {"data": []}
        with pytest.raises(DependencyUnavailableError):
            await _gateway().receipts(["T1"])
        ScriptedClient.failing = True
        with pytest.raises(DependencyUnavailableError):
            await _gateway().receipts(["T1"])
