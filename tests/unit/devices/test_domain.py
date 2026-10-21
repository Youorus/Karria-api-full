"""Les règles pures du module `devices`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doubles import sample_device_info
from karria.modules.devices.domain import (
    MAX_PUSH_BATCH_SIZE,
    Device,
    PushEnvelope,
    PushError,
    PushNotification,
    PushPermission,
    batched,
    is_expo_push_token,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        ("ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]", True),
        ("ExpoPushToken[abc-DEF_123]", True),
        ("ExponentPushToken[]", False),
        ("fcm:abcdef", False),
        ("ExponentPushToken[espace interdit]", False),
        ("", False),
    ],
)
def test_le_format_du_jeton_expo(valeur: str, attendu: bool) -> None:
    assert is_expo_push_token(valeur) is attendu


class TestDevice:
    def _device(self, **info_overrides: object) -> Device:
        return Device(
            id=uuid4(),
            user_id=uuid4(),
            installation_id="inst-1",
            info=sample_device_info(**info_overrides),  # type: ignore[arg-type]
            country="FR",
            first_seen_at=NOW,
            last_seen_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )

    def test_joignable_seulement_avec_jeton_et_permission(self) -> None:
        assert self._device().push_enabled
        assert not self._device(push_token=None).push_enabled
        assert not self._device(permission=PushPermission.DENIED).push_enabled
        assert not self._device(permission=PushPermission.UNDETERMINED).push_enabled

    def test_une_declaration_avance_la_derniere_vue(self) -> None:
        later = NOW + timedelta(days=2)
        seen = self._device().declared(sample_device_info(push_token=None), country=None, at=later)
        assert seen.last_seen_at == later
        assert seen.first_seen_at == NOW
        assert seen.country is None
        assert not seen.push_enabled

    def test_la_revocation_detache_le_jeton_sans_oublier_lappareil(self) -> None:
        revoked = self._device().token_revoked(at=NOW + timedelta(hours=1))
        assert revoked.info.push_token is None
        assert revoked.installation_id == "inst-1"
        assert revoked.info.push_permission is PushPermission.GRANTED


class TestPushNotification:
    def test_refuse_un_titre_ou_un_corps_vide_ou_trop_long(self) -> None:
        with pytest.raises(ValueError, match="titre"):
            PushNotification(title="  ", body="x", data={})
        with pytest.raises(ValueError, match="titre"):
            PushNotification(title="t" * 121, body="x", data={})
        with pytest.raises(ValueError, match="corps"):
            PushNotification(title="t", body="", data={})
        with pytest.raises(ValueError, match="corps"):
            PushNotification(title="t", body="b" * 501, data={})

    def test_le_decoupage_en_lots_de_cent(self) -> None:
        notification = PushNotification(title="t", body="b", data={})
        envelopes = [
            PushEnvelope(token=f"ExponentPushToken[{i}]", notification=notification)
            for i in range(250)
        ]
        lots = batched(envelopes)
        assert [len(lot) for lot in lots] == [MAX_PUSH_BATCH_SIZE, MAX_PUSH_BATCH_SIZE, 50]
        assert batched([]) == []


class TestPushError:
    def test_lit_les_erreurs_connues_et_inconnues(self) -> None:
        assert PushError.parse("DeviceNotRegistered") is PushError.DEVICE_NOT_REGISTERED
        assert PushError.parse("QuelqueChoseDeNeuf") is PushError.UNKNOWN
        assert PushError.parse(None) is PushError.UNKNOWN

    def test_seul_un_appareil_desinscrit_revoque_le_jeton(self) -> None:
        assert PushError.DEVICE_NOT_REGISTERED.revokes_token
        assert not PushError.MESSAGE_RATE_EXCEEDED.revokes_token
        assert not PushError.UNKNOWN.revokes_token
