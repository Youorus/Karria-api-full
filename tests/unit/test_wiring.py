"""Les branchements de `bootstrap/wiring.py` : ports → autres modules, et les ponts du bus."""

from __future__ import annotations

from uuid import uuid4

import pytest

from doubles import (
    CollectingBus,
    FakeClock,
    FakePushGateway,
    InMemoryDeviceRepository,
    InMemoryPushTicketRepository,
    sample_device_info,
)
from karria.bootstrap import wiring
from karria.bootstrap.app import create_app
from karria.bootstrap.wiring import (
    DevicesMainDeviceLookup,
    DevicesUserNotifier,
    build_user_notifier,
    main_device_lookup_from_devices,
    subscribe_bridges,
    user_notifier_from_devices,
)
from karria.modules.devices.application import GetMainDevice, RegisterDevice, SendPushToUser
from karria.modules.identity import AccountDeleted, MainDeviceSnapshot, get_main_device_lookup
from karria.modules.offers import get_user_notifier
from karria.shared.auth import resolve_principal
from karria.shared.config import AppSettings, Environment, Settings, StorageSettings
from karria.shared.database import NullUnitOfWork
from karria.shared.storage import InMemoryObjectStorage

pytestmark = pytest.mark.unit


def test_les_deux_ports_inter_modules_sont_branches() -> None:
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

    assert resolve_principal in app.dependency_overrides
    assert app.dependency_overrides[get_main_device_lookup] is main_device_lookup_from_devices
    assert app.dependency_overrides[get_user_notifier] is user_notifier_from_devices


class TestMainDeviceLookup:
    async def test_traduit_la_reponse_de_devices_dans_les_termes_didentity(self) -> None:
        devices = InMemoryDeviceRepository()
        user = uuid4()
        await RegisterDevice(devices=devices, clock=FakeClock(), uow=NullUnitOfWork()).execute(
            user_id=user, installation_id="i", info=sample_device_info(), country=None
        )
        lookup = main_device_lookup_from_devices(GetMainDevice(devices=devices))

        assert isinstance(lookup, DevicesMainDeviceLookup)
        assert await lookup.main_device_of(user) == MainDeviceSnapshot(
            platform="ios", app_version="2.0.0"
        )
        assert await lookup.main_device_of(uuid4()) is None


def test_les_ponts_abonnent_devices_cv_et_offers_a_la_suppression_de_compte() -> None:
    bus = CollectingBus()

    subscribe_bridges(bus, storage=InMemoryObjectStorage(StorageSettings()))

    assert bus.subscriptions() == {AccountDeleted.__name__: 3}


class TestUserNotifier:
    async def test_offers_previent_par_les_appareils_de_devices(self) -> None:
        devices = InMemoryDeviceRepository()
        gateway = FakePushGateway()
        user = uuid4()
        await RegisterDevice(devices=devices, clock=FakeClock(), uow=NullUnitOfWork()).execute(
            user_id=user, installation_id="i", info=sample_device_info(), country=None
        )
        send = SendPushToUser(
            devices=devices,
            tickets=InMemoryPushTicketRepository(),
            gateway=gateway,
            clock=FakeClock(),
            uow=NullUnitOfWork(),
        )
        notifier = user_notifier_from_devices(send)

        accepted = await notifier.notify(user, title="3 offres", body="…", data={"k": "v"})

        assert isinstance(notifier, DevicesUserNotifier)
        assert accepted == 1
        assert len(gateway.batches) == 1

    def test_le_worker_obtient_le_meme_branchement(self) -> None:
        assert isinstance(build_user_notifier(), DevicesUserNotifier)


async def test_le_pont_d_offers_oublie_la_personne(monkeypatch: pytest.MonkeyPatch) -> None:
    forgotten: list[object] = []

    class Forget:
        async def execute(self, user_id: object) -> None:
            forgotten.append(user_id)

    monkeypatch.setattr(wiring, "build_forget_user_offers", Forget)
    bus = CollectingBus()
    subscribe_bridges(bus, storage=InMemoryObjectStorage(StorageSettings()))
    user = uuid4()

    await bus.publish(AccountDeleted(user_id=user))

    assert forgotten == [user]
