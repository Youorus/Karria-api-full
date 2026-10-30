"""Les branchements de `bootstrap/wiring.py` : ports → autres modules, et les ponts du bus."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import cast
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
    DocumentsTemplateStore,
    IdentityAccountDirectory,
    account_directory_from_identity,
    ai_consent_gate_from_identity,
    build_user_notifier,
    main_device_lookup_from_devices,
    subscribe_bridges,
    template_store_from_documents,
    user_notifier_from_devices,
)
from karria.modules.devices.application import GetMainDevice, RegisterDevice, SendPushToUser
from karria.modules.documents import PremiumTemplate, TemplateShop
from karria.modules.identity import (
    AccountDeleted,
    GetCurrentUser,
    MainDeviceSnapshot,
    UserNotFoundError,
    get_main_device_lookup,
)
from karria.modules.offers import get_user_notifier
from karria.modules.payments import PremiumOffer, get_account_directory, get_template_store
from karria.shared.auth import get_ai_consent_gate, resolve_principal
from karria.shared.config import AppSettings, Environment, Settings, StorageSettings
from karria.shared.database import NullUnitOfWork
from karria.shared.push import PushKind, PushMessage
from karria.shared.storage import InMemoryObjectStorage

pytestmark = pytest.mark.unit


def test_les_deux_ports_inter_modules_sont_branches() -> None:
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

    assert resolve_principal in app.dependency_overrides
    assert app.dependency_overrides[get_main_device_lookup] is main_device_lookup_from_devices
    assert app.dependency_overrides[get_user_notifier] is user_notifier_from_devices
    assert app.dependency_overrides[get_ai_consent_gate] is ai_consent_gate_from_identity


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


def test_les_ponts_abonnent_devices_cv_offers_documents_et_payments_a_la_suppression_de_compte() -> (
    None
):
    bus = CollectingBus()

    subscribe_bridges(bus, storage=InMemoryObjectStorage(StorageSettings()))

    assert bus.subscriptions() == {AccountDeleted.__name__: 5}


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

        accepted = await notifier.notify(
            user, PushMessage(kind=PushKind.OFFERS_FEED, title="3 offres", body="…", count=3)
        )

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


class TestPaymentsPorts:
    """`payments` lit l'adresse dans `identity` et vend les modèles de `documents`."""

    def test_les_deux_ports_de_payments_sont_branches(self) -> None:
        app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

        assert app.dependency_overrides[get_account_directory] is account_directory_from_identity
        assert app.dependency_overrides[get_template_store] is template_store_from_documents

    async def test_l_adresse_vient_du_compte_ou_de_rien(self) -> None:
        user = uuid4()

        class Reader:
            async def execute(self, user_id: object) -> object:
                if user_id != user:
                    raise UserNotFoundError
                return SimpleNamespace(email="toi@exemple.fr")

        directory = account_directory_from_identity(cast(GetCurrentUser, Reader()))

        assert isinstance(directory, IdentityAccountDirectory)
        assert await directory.email_of(user) == "toi@exemple.fr"
        assert await directory.email_of(uuid4()) is None

    async def test_le_prix_de_documents_devient_des_centimes(self) -> None:
        template = uuid4()
        user = uuid4()
        granted: list[tuple[object, object]] = []

        class Shop:
            async def premium_offers(self) -> list[PremiumTemplate]:
                return [PremiumTemplate(template, "Meridian", Decimal("4.99"))]

            async def premium_offer(self, template_id: object) -> PremiumTemplate | None:
                return (
                    PremiumTemplate(template, "Meridian", Decimal("12.50"))
                    if template_id == template
                    else None
                )

            async def owns(self, user_id: object, template_id: object) -> bool:
                return (user_id, template_id) in granted

            async def grant(self, user_id: object, template_id: object) -> object:
                granted.append((user_id, template_id))
                return object()

        store = template_store_from_documents(cast(TemplateShop, Shop()))

        assert isinstance(store, DocumentsTemplateStore)
        assert await store.premium_offers() == [PremiumOffer(template, "Meridian", 499)]
        assert await store.premium_offer(template) == PremiumOffer(template, "Meridian", 1250)
        assert await store.premium_offer(uuid4()) is None
        assert await store.owns(user, template) is False
        await store.grant(user, template)
        assert await store.owns(user, template) is True


async def test_le_pont_de_payments_oublie_la_personne(monkeypatch: pytest.MonkeyPatch) -> None:
    forgotten: list[object] = []

    class Forget:
        def __init__(self, settings: object) -> None:
            del settings

        async def execute(self, user_id: object) -> None:
            forgotten.append(user_id)

    monkeypatch.setattr(wiring, "build_forget_user_payments", Forget)
    bus = CollectingBus()
    subscribe_bridges(bus, storage=InMemoryObjectStorage(StorageSettings()))
    user = uuid4()

    await bus.publish(AccountDeleted(user_id=user))

    assert forgotten == [user]
