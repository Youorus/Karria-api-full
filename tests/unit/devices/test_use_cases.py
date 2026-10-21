"""Déclarer, détacher, notifier, lire les reçus — sur doublures."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from doubles import (
    FakeClock,
    FakePushGateway,
    InMemoryDeviceRepository,
    InMemoryPushTicketRepository,
    sample_device_info,
)
from karria.modules.devices.application import (
    RECEIPT_DELAY,
    CheckPushReceipts,
    RegisterDevice,
    SendPushToUser,
    UnregisterDevice,
)
from karria.modules.devices.domain import Platform, PushError, PushPermission, PushReceipt
from karria.shared.database import NullUnitOfWork

pytestmark = pytest.mark.unit


@pytest.fixture
def devices() -> InMemoryDeviceRepository:
    return InMemoryDeviceRepository()


@pytest.fixture
def tickets() -> InMemoryPushTicketRepository:
    return InMemoryPushTicketRepository()


@pytest.fixture
def gateway() -> FakePushGateway:
    return FakePushGateway()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def register(devices: InMemoryDeviceRepository, clock: FakeClock) -> RegisterDevice:
    return RegisterDevice(devices=devices, clock=clock, uow=NullUnitOfWork())


@pytest.fixture
def send(
    devices: InMemoryDeviceRepository,
    tickets: InMemoryPushTicketRepository,
    gateway: FakePushGateway,
    clock: FakeClock,
) -> SendPushToUser:
    return SendPushToUser(
        devices=devices, tickets=tickets, gateway=gateway, clock=clock, uow=NullUnitOfWork()
    )


@pytest.fixture
def check(
    devices: InMemoryDeviceRepository,
    tickets: InMemoryPushTicketRepository,
    gateway: FakePushGateway,
    clock: FakeClock,
) -> CheckPushReceipts:
    return CheckPushReceipts(
        devices=devices, tickets=tickets, gateway=gateway, clock=clock, uow=NullUnitOfWork()
    )


class TestRegister:
    async def test_cree_puis_met_a_jour_la_meme_installation(
        self, register: RegisterDevice, devices: InMemoryDeviceRepository, clock: FakeClock
    ) -> None:
        user = uuid4()
        first = await register.execute(
            user_id=user, installation_id="inst", info=sample_device_info(), country="FR"
        )
        clock.advance(timedelta(days=1))

        again = await register.execute(
            user_id=user,
            installation_id="inst",
            info=sample_device_info(permission=PushPermission.DENIED),
            country=None,
        )

        assert again.id == first.id
        assert len(devices.rows) == 1
        assert again.first_seen_at == first.first_seen_at
        assert again.last_seen_at == clock.now()
        assert not again.push_enabled
        assert first.push_enabled

    async def test_deux_comptes_sur_le_meme_appareil_sont_deux_installations(
        self, register: RegisterDevice, devices: InMemoryDeviceRepository
    ) -> None:
        await register.execute(
            user_id=uuid4(), installation_id="inst", info=sample_device_info(), country=None
        )
        await register.execute(
            user_id=uuid4(), installation_id="inst", info=sample_device_info(), country=None
        )
        assert len(devices.rows) == 2
        assert await devices.platform_stats() == {Platform.IOS: 2, Platform.ANDROID: 0}


class TestUnregister:
    async def test_retire_lappareil_et_reste_silencieux_ensuite(
        self, register: RegisterDevice, devices: InMemoryDeviceRepository
    ) -> None:
        user = uuid4()
        await register.execute(
            user_id=user, installation_id="a", info=sample_device_info(), country=None
        )
        await register.execute(
            user_id=user, installation_id="b", info=sample_device_info(), country=None
        )
        unregister = UnregisterDevice(devices=devices, uow=NullUnitOfWork())

        await unregister.execute(user_id=user, installation_id="a")
        await unregister.execute(user_id=user, installation_id="a")

        assert [d.installation_id for d in devices.rows.values()] == ["b"]
        main = await devices.main_device_of(user)
        assert main is not None
        assert main.installation_id == "b"
        assert await devices.main_device_of(uuid4()) is None


class TestSendPushToUser:
    async def test_envoie_a_tous_les_appareils_joignables_et_memorise_les_tickets(
        self,
        *,
        register: RegisterDevice,
        send: SendPushToUser,
        gateway: FakePushGateway,
        tickets: InMemoryPushTicketRepository,
    ) -> None:
        user = uuid4()
        await register.execute(
            user_id=user,
            installation_id="a",
            info=sample_device_info(push_token="ExponentPushToken[a]"),
            country=None,
        )
        await register.execute(
            user_id=user,
            installation_id="b",
            info=sample_device_info(push_token="ExponentPushToken[b]"),
            country=None,
        )
        await register.execute(
            user_id=user,
            installation_id="c",
            info=sample_device_info(push_token=None),
            country=None,
        )
        await register.execute(
            user_id=uuid4(),
            installation_id="d",
            info=sample_device_info(push_token="ExponentPushToken[d]"),
            country=None,
        )

        summary = await send.execute(
            user_id=user, title="Bonjour", body="Une offre", data={"type": "X"}
        )

        assert summary.accepted == 2
        assert summary.rejected == 0
        assert summary.targeted == 2
        assert len(gateway.batches) == 1
        assert sorted(e.token for e in gateway.batches[0]) == [
            "ExponentPushToken[a]",
            "ExponentPushToken[b]",
        ]
        assert gateway.batches[0][0].notification.data == {"type": "X"}
        assert len(tickets.rows) == 2

    async def test_sans_appareil_rien_ne_part(
        self, send: SendPushToUser, gateway: FakePushGateway
    ) -> None:
        summary = await send.execute(user_id=uuid4(), title="t", body="b")
        assert summary.targeted == 0
        assert gateway.batches == []

    async def test_un_jeton_mort_est_detache_sans_faire_echouer_lenvoi(
        self,
        *,
        register: RegisterDevice,
        send: SendPushToUser,
        gateway: FakePushGateway,
        devices: InMemoryDeviceRepository,
    ) -> None:
        user = uuid4()
        await register.execute(
            user_id=user,
            installation_id="a",
            info=sample_device_info(push_token="ExponentPushToken[a]"),
            country=None,
        )
        await register.execute(
            user_id=user,
            installation_id="b",
            info=sample_device_info(push_token="ExponentPushToken[b]"),
            country=None,
        )
        gateway.errors["ExponentPushToken[a]"] = PushError.DEVICE_NOT_REGISTERED
        gateway.errors["ExponentPushToken[b]"] = PushError.MESSAGE_RATE_EXCEEDED

        summary = await send.execute(user_id=user, title="t", body="b")

        assert summary.accepted == 0
        assert summary.rejected == 2
        assert summary.revoked == 1
        dead = await devices.find(user, "a")
        assert dead is not None
        assert dead.info.push_token is None
        alive = await devices.find(user, "b")
        assert alive is not None
        assert alive.info.push_token == "ExponentPushToken[b]"  # noqa: S105

    async def test_decoupe_en_lots_de_cent(
        self, register: RegisterDevice, send: SendPushToUser, gateway: FakePushGateway
    ) -> None:
        user = uuid4()
        for i in range(101):
            await register.execute(
                user_id=user,
                installation_id=f"i{i}",
                info=sample_device_info(push_token=f"ExponentPushToken[{i}]"),
                country=None,
            )

        summary = await send.execute(user_id=user, title="t", body="b")

        assert summary.accepted == 101
        assert [len(b) for b in gateway.batches] == [100, 1]

    async def test_refuse_une_notification_vide(self, send: SendPushToUser) -> None:
        with pytest.raises(ValueError, match="titre"):
            await send.execute(user_id=uuid4(), title="", body="b")


class TestCheckPushReceipts:
    async def test_lit_les_recus_et_revoque_les_jetons_morts(
        self,
        *,
        register: RegisterDevice,
        send: SendPushToUser,
        check: CheckPushReceipts,
        gateway: FakePushGateway,
        devices: InMemoryDeviceRepository,
        tickets: InMemoryPushTicketRepository,
        clock: FakeClock,
    ) -> None:
        user = uuid4()
        await register.execute(
            user_id=user,
            installation_id="a",
            info=sample_device_info(push_token="ExponentPushToken[a]"),
            country=None,
        )
        await register.execute(
            user_id=user,
            installation_id="b",
            info=sample_device_info(push_token="ExponentPushToken[b]"),
            country=None,
        )
        await send.execute(user_id=user, title="t", body="b")
        gateway.receipts_by_ticket = {
            "ticket-1": PushReceipt(
                ticket_id="ticket-1", ok=False, error=PushError.DEVICE_NOT_REGISTERED
            ),
            "ticket-2": PushReceipt(ticket_id="ticket-2", ok=True),
        }

        # Trop tôt : rien n'est interrogé.
        assert await check.execute() == 0
        assert gateway.receipt_queries == []

        clock.advance(RECEIPT_DELAY + timedelta(seconds=1))
        revoked = await check.execute()

        assert revoked == 1
        assert gateway.receipt_queries == [["ticket-1", "ticket-2"]]
        assert await tickets.pending(before=clock.now() + timedelta(days=1), limit=10) == []
        remaining = [d.info.push_token for d in devices.rows.values()]
        assert set(remaining) == {None, "ExponentPushToken[b]"}
        # Les tickets vérifiés se purgent plus tard.
        assert await tickets.purge_checked_before(clock.now() + timedelta(days=1)) == 2

    async def test_un_recu_inconnu_ou_benin_ne_revoque_rien(
        self,
        *,
        register: RegisterDevice,
        send: SendPushToUser,
        check: CheckPushReceipts,
        gateway: FakePushGateway,
        clock: FakeClock,
    ) -> None:
        user = uuid4()
        await register.execute(
            user_id=user,
            installation_id="a",
            info=sample_device_info(push_token="ExponentPushToken[a]"),
            country=None,
        )
        await send.execute(user_id=user, title="t", body="b")
        gateway.receipts_by_ticket = {
            "ticket-1": PushReceipt(
                ticket_id="ticket-1", ok=False, error=PushError.MESSAGE_TOO_BIG
            ),
            "fantome": PushReceipt(
                ticket_id="fantome", ok=False, error=PushError.DEVICE_NOT_REGISTERED
            ),
        }

        class ChattyGateway(FakePushGateway):
            async def receipts(self, ticket_ids: list[str]) -> list[PushReceipt]:
                _ = ticket_ids
                return list(gateway.receipts_by_ticket.values())

        check_with_ghost = CheckPushReceipts(
            devices=check._devices,
            tickets=check._tickets,
            gateway=ChattyGateway(),
            clock=clock,
            uow=NullUnitOfWork(),
        )
        clock.advance(RECEIPT_DELAY + timedelta(seconds=1))

        assert await check_with_ghost.execute() == 0


class TestCheckPushReceiptsSansRecu:
    async def test_un_ticket_sans_recu_reste_en_attente(
        self,
        *,
        register: RegisterDevice,
        send: SendPushToUser,
        check: CheckPushReceipts,
        gateway: FakePushGateway,
        tickets: InMemoryPushTicketRepository,
        clock: FakeClock,
    ) -> None:
        """Expo n'a pas encore de reçu : on ne marque rien, on réessaiera."""
        user = uuid4()
        await register.execute(
            user_id=user,
            installation_id="a",
            info=sample_device_info(push_token="ExponentPushToken[a]"),
            country=None,
        )
        await send.execute(user_id=user, title="t", body="b")
        clock.advance(RECEIPT_DELAY + timedelta(seconds=1))

        assert await check.execute() == 0

        assert gateway.receipt_queries == [["ticket-1"]]
        assert len(await tickets.pending(before=clock.now(), limit=10)) == 1
