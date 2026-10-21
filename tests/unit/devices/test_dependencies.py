"""Le câblage de `devices` : ce que les fabriques assemblent."""

from __future__ import annotations

import pytest

from doubles import (
    FakeClock,
    FakePushGateway,
    InMemoryDeviceRepository,
    InMemoryPushTicketRepository,
)
from karria.modules.devices.api.dependencies import get_check_push_receipts, get_push_gateway
from karria.modules.devices.application import CheckPushReceipts
from karria.modules.devices.infrastructure import ExpoPushGateway
from karria.shared.config import AppSettings, Environment, PushSettings, Settings
from karria.shared.database import NullUnitOfWork

pytestmark = pytest.mark.unit


def test_la_lecture_des_recus_sassemble_comme_le_script_le_fait() -> None:
    use_case = get_check_push_receipts(
        devices=InMemoryDeviceRepository(),
        tickets=InMemoryPushTicketRepository(),
        gateway=FakePushGateway(),
        clock=FakeClock(),
        uow=NullUnitOfWork(),
    )
    assert isinstance(use_case, CheckPushReceipts)


def test_la_passerelle_lit_la_configuration() -> None:
    settings = Settings(
        app=AppSettings(environment=Environment.TEST),
        push=PushSettings(expo_api_url="https://exemple.test/api", request_timeout_seconds=3.0),
    )
    gateway = get_push_gateway(settings)
    assert isinstance(gateway, ExpoPushGateway)
    assert gateway._client.base_url == "https://exemple.test/api"
    assert gateway._client.timeout_seconds == 3.0
