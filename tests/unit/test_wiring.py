"""Les branchements de `bootstrap/wiring.py` : ports → autres modules, et les ponts du bus."""

from __future__ import annotations

from uuid import uuid4

import pytest

from doubles import CollectingBus, FakeClock, InMemoryDeviceRepository, sample_device_info
from karria.bootstrap.app import create_app
from karria.bootstrap.wiring import (
    DevicesMainDeviceLookup,
    main_device_lookup_from_devices,
    subscribe_bridges,
)
from karria.modules.devices.application import GetMainDevice, RegisterDevice
from karria.modules.identity import AccountDeleted, MainDeviceSnapshot, get_main_device_lookup
from karria.shared.auth import resolve_principal
from karria.shared.config import AppSettings, Environment, Settings, StorageSettings
from karria.shared.database import NullUnitOfWork
from karria.shared.storage import InMemoryObjectStorage

pytestmark = pytest.mark.unit


def test_les_deux_ports_inter_modules_sont_branches() -> None:
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

    assert resolve_principal in app.dependency_overrides
    assert app.dependency_overrides[get_main_device_lookup] is main_device_lookup_from_devices


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


def test_les_ponts_abonnent_devices_et_cv_a_la_suppression_de_compte() -> None:
    bus = CollectingBus()

    subscribe_bridges(bus, storage=InMemoryObjectStorage(StorageSettings()))

    assert bus.subscriptions() == {AccountDeleted.__name__: 2}
