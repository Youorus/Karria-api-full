"""Les dépôts de `devices` contre PostgreSQL et le schéma migré."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doubles import sample_device_info
from karria.modules.devices.application import StoredTicket
from karria.modules.devices.domain import Device, Platform
from karria.modules.devices.infrastructure import (
    TortoiseDeviceRepository,
    TortoisePushTicketRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def _device(user_id, installation_id: str, token: str | None) -> Device:  # noqa: ANN001
    return Device(
        id=uuid4(),
        user_id=user_id,
        installation_id=installation_id,
        info=sample_device_info(push_token=token),
        country="FR",
        first_seen_at=NOW,
        last_seen_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


class TestDevices:
    async def test_le_cycle_complet(self) -> None:
        repo = TortoiseDeviceRepository()
        user = uuid4()
        a = _device(user, "a", "ExponentPushToken[a]")
        b = _device(user, "b", None)
        other = _device(uuid4(), "a", "ExponentPushToken[a]")
        for device in (a, b, other):
            await repo.add(device)

        found = await repo.find(user, "a")
        assert found is not None
        assert found.country == "FR"
        assert found.info.device_model == "iPhone 15"
        assert await repo.find(user, "zzz") is None

        later = NOW + timedelta(days=1)
        await repo.save(b.declared(sample_device_info(), country=None, at=later))
        main = await repo.main_device_of(user)
        assert main is not None
        assert main.installation_id == "b"
        assert main.country is None

        targets = await repo.push_targets_of_user(user)
        assert sorted(d.installation_id for d in targets) == ["a", "b"]

        # Le jeton partagé est détaché partout, y compris chez l'autre compte.
        assert await repo.revoke_token("ExponentPushToken[a]", at=later) == 2
        assert [d.installation_id for d in await repo.push_targets_of_user(user)] == ["b"]

        assert await repo.platform_stats() == {Platform.IOS: 3, Platform.ANDROID: 0}
        assert await repo.delete(user, "a") is True
        assert await repo.delete(user, "a") is False


class TestPushTickets:
    async def test_le_cycle_complet(self) -> None:
        repo = TortoisePushTicketRepository()
        await repo.add_many(
            [
                StoredTicket("T1", "ExponentPushToken[a]"),
                StoredTicket("T2", "ExponentPushToken[b]"),
            ],
            at=NOW,
        )
        await repo.add_many([StoredTicket("T1", "ExponentPushToken[a]")], at=NOW)  # doublon ignoré

        future = datetime.now(UTC) + timedelta(hours=1)
        pending = await repo.pending(before=future, limit=10)
        assert [t.ticket_id for t in pending] == ["T1", "T2"]
        assert await repo.pending(before=future, limit=1) == [pending[0]]
        assert await repo.pending(before=NOW - timedelta(days=1), limit=10) == []

        await repo.mark_checked(["T1"], at=future)
        assert [t.ticket_id for t in await repo.pending(before=future, limit=10)] == ["T2"]
        assert await repo.purge_checked_before(future + timedelta(days=1)) == 1
