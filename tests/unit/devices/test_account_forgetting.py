"""Quand un compte disparaît : ses appareils partent, son appareil principal se décrit."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from doubles import FakeClock, InMemoryDeviceRepository, sample_device_info
from karria.modules.devices.api.dependencies import build_forget_user_devices, get_get_main_device
from karria.modules.devices.application import (
    ForgetUserDevices,
    GetMainDevice,
    MainDeviceSummary,
    RegisterDevice,
)
from karria.modules.devices.domain import Platform
from karria.modules.devices.infrastructure import TortoiseDeviceRepository
from karria.shared.database import NullUnitOfWork, TortoiseUnitOfWork

pytestmark = pytest.mark.unit


@pytest.fixture
def devices() -> InMemoryDeviceRepository:
    return InMemoryDeviceRepository()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def register(devices: InMemoryDeviceRepository, clock: FakeClock) -> RegisterDevice:
    return RegisterDevice(devices=devices, clock=clock, uow=NullUnitOfWork())


class TestGetMainDevice:
    async def test_decrit_lappareil_vu_en_dernier(
        self, register: RegisterDevice, devices: InMemoryDeviceRepository, clock: FakeClock
    ) -> None:
        user = uuid4()
        await register.execute(
            user_id=user,
            installation_id="tablette",
            info=sample_device_info(platform=Platform.IOS),
            country="FR",
        )
        clock.advance(timedelta(hours=1))
        await register.execute(
            user_id=user,
            installation_id="telephone",
            info=sample_device_info(platform=Platform.ANDROID),
            country="FR",
        )

        summary = await GetMainDevice(devices=devices).execute(user)

        assert summary == MainDeviceSummary(platform="android", app_version="2.0.0")

    async def test_sans_appareil_rend_none(self, devices: InMemoryDeviceRepository) -> None:
        assert await GetMainDevice(devices=devices).execute(uuid4()) is None


class TestForgetUserDevices:
    async def test_efface_tous_les_appareils_du_compte_et_seulement_les_siens(
        self, register: RegisterDevice, devices: InMemoryDeviceRepository
    ) -> None:
        partant, autre = uuid4(), uuid4()
        for installation in ("a", "b"):
            await register.execute(
                user_id=partant,
                installation_id=installation,
                info=sample_device_info(),
                country=None,
            )
        await register.execute(
            user_id=autre, installation_id="a", info=sample_device_info(), country=None
        )
        forget = ForgetUserDevices(devices=devices, uow=NullUnitOfWork())

        removed = await forget.execute(partant)

        assert removed == 2
        assert await devices.main_device_of(partant) is None
        assert await devices.main_device_of(autre) is not None

    async def test_est_idempotent(self, devices: InMemoryDeviceRepository) -> None:
        forget = ForgetUserDevices(devices=devices, uow=NullUnitOfWork())
        assert await forget.execute(uuid4()) == 0


class TestCablage:
    def test_lappareil_principal_se_lit_dans_tortoise(self) -> None:
        assert isinstance(get_get_main_device(TortoiseDeviceRepository()), GetMainDevice)

    def test_loubli_hors_requete_recoit_les_adaptateurs_deployes(self) -> None:
        forget = build_forget_user_devices()
        assert isinstance(forget, ForgetUserDevices)
        assert isinstance(forget._devices, TortoiseDeviceRepository)
        assert isinstance(forget._uow, TortoiseUnitOfWork)
