"""Déclarer un appareil, le détacher, lui envoyer une notification.

Ce que la V1 faisait, et ce qu'on garde
---------------------------------------
La V1 (`app/push_notifications/`) rangeait **un** jeton FCM sur le compte,
expédiait par Firebase, et désactivait le jeton sur `UnregisteredError`. On
garde l'intention — un jeton mort se détache tout seul — et on change le
reste : un compte a **plusieurs** appareils, le transport est Expo (celui
que l'app Expo produit sans configuration Firebase), et l'envoi groupé lit
les reçus, que la V1 ignorait.

Ce qu'on ne reprend pas : les seuils de pertinence, les files de reprise
et les gabarits métier (« nouvelle offre »). Ils appartiennent aux modules
qui auront quelque chose à dire, et à une décision produit qui n'est pas
prise.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from uuid import UUID, uuid4

from karria.modules.devices.application.ports import (
    DeviceRepository,
    PushGateway,
    PushTicketRepository,
    StoredTicket,
)
from karria.modules.devices.domain import (
    Device,
    DeviceInfo,
    PushEnvelope,
    PushNotification,
    PushTicket,
    batched,
)
from karria.shared.clock import Clock
from karria.shared.database import UnitOfWork
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Délai avant de lire le reçu d'un ticket. Expo recommande d'attendre
#: qu'Apple et Google aient répondu — un quart d'heure suffit largement.
RECEIPT_DELAY: Final = timedelta(minutes=15)

#: Tickets interrogés par passage. Expo accepte jusqu'à mille identifiants
#: par appel ; on reste en deçà pour que la réponse tienne en mémoire.
RECEIPT_BATCH_SIZE: Final = 300


class RegisterDevice:
    """`PUT /devices/{installation_id}` : déclarer, ou redéclarer, un appareil.

    Idempotent par `(compte, installation)` : l'app l'appelle à chaque
    démarrage et à chaque changement de permission ou de jeton. Une
    déclaration identique ne fait qu'avancer `last_seen_at`.
    """

    def __init__(self, *, devices: DeviceRepository, clock: Clock, uow: UnitOfWork) -> None:
        self._devices = devices
        self._clock = clock
        self._uow = uow

    async def execute(
        self, *, user_id: UUID, installation_id: str, info: DeviceInfo, country: str | None
    ) -> Device:
        """Crée ou met à jour l'appareil, et le rend."""
        now = self._clock.now()
        existing = await self._devices.find(user_id, installation_id)
        if existing is None:
            device = Device(
                id=uuid4(),
                user_id=user_id,
                installation_id=installation_id,
                info=info,
                country=country,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            async with self._uow.atomic():
                await self._devices.add(device)
            logger.info(
                "devices.registered",
                user_id=str(user_id),
                platform=info.platform.value,
                push_enabled=device.push_enabled,
            )
            return device

        device = existing.declared(info, country=country, at=now)
        async with self._uow.atomic():
            await self._devices.save(device)
        logger.debug("devices.seen", user_id=str(user_id), push_enabled=device.push_enabled)
        return device


class UnregisterDevice:
    """`DELETE /devices/{installation_id}` : l'app se déconnecte.

    Silencieux si l'appareil n'existe pas : se déconnecter deux fois n'est
    pas une erreur. Ne touche **qu'à cet appareil** — les autres restent
    joignables.
    """

    def __init__(self, *, devices: DeviceRepository, uow: UnitOfWork) -> None:
        self._devices = devices
        self._uow = uow

    async def execute(self, *, user_id: UUID, installation_id: str) -> None:
        """Retire l'appareil."""
        async with self._uow.atomic():
            removed = await self._devices.delete(user_id, installation_id)
        logger.info("devices.unregistered", user_id=str(user_id), existed=removed)


@dataclass(frozen=True, slots=True)
class PushDispatchSummary:
    """Ce qu'un envoi a donné, appareil par appareil."""

    #: Enveloppes acceptées par Expo (un ticket émis). Pas « livrées ».
    accepted: int = 0
    #: Enveloppes refusées d'emblée, jeton mort compris.
    rejected: int = 0
    #: Jetons détachés parce qu'Expo les a déclarés morts.
    revoked: int = 0

    @property
    def targeted(self) -> int:
        """Combien d'appareils ont été visés."""
        return self.accepted + self.rejected


class SendPushToUser:
    """Envoie une notification à **tous** les appareils joignables d'un compte.

    C'est le service que les autres modules utilisent — via un port à eux,
    branché dans `bootstrap/wiring.py` (règle 4). Il ne décide ni du
    moment ni de la pertinence : il achemine ce qu'on lui donne.

    Ne lève pas pour un jeton mort : il le détache et continue. Lève si
    Expo est injoignable — l'appelant décide alors de réessayer ou pas.
    """

    def __init__(
        self,
        *,
        devices: DeviceRepository,
        tickets: PushTicketRepository,
        gateway: PushGateway,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._devices = devices
        self._tickets = tickets
        self._gateway = gateway
        self._clock = clock
        self._uow = uow

    async def execute(
        self, *, user_id: UUID, title: str, body: str, data: Mapping[str, str] | None = None
    ) -> PushDispatchSummary:
        """Compose, envoie par lots de 100, mémorise les tickets, détache les morts."""
        notification = PushNotification(title=title, body=body, data=dict(data or {}))
        targets = await self._devices.push_targets_of_user(user_id)
        if not targets:
            logger.debug("devices.push_skipped_no_target", user_id=str(user_id))
            return PushDispatchSummary()

        envelopes = [
            PushEnvelope(token=device.info.push_token, notification=notification)
            for device in targets
            if device.info.push_token is not None
        ]
        summary = PushDispatchSummary()
        for batch in batched(envelopes):
            tickets = await self._gateway.send(batch)
            summary = await self._settle(summary, tickets)

        logger.info(
            "devices.push_sent",
            user_id=str(user_id),
            accepted=summary.accepted,
            rejected=summary.rejected,
            revoked=summary.revoked,
        )
        return summary

    async def _settle(
        self, summary: PushDispatchSummary, tickets: Sequence[PushTicket]
    ) -> PushDispatchSummary:
        """Tire les conséquences d'un lot de tickets."""
        now = self._clock.now()
        accepted, rejected, revoked = summary.accepted, summary.rejected, summary.revoked
        to_store: list[StoredTicket] = []
        for ticket in tickets:
            if ticket.ok and ticket.ticket_id is not None:
                accepted += 1
                to_store.append(StoredTicket(ticket_id=ticket.ticket_id, token=ticket.token))
                continue
            rejected += 1
            if ticket.error is not None and ticket.error.revokes_token:
                async with self._uow.atomic():
                    revoked += await self._devices.revoke_token(ticket.token, at=now)
                logger.warning("devices.token_revoked", reason=ticket.error.value, stage="ticket")
        if to_store:
            async with self._uow.atomic():
                await self._tickets.add_many(to_store, at=now)
        return PushDispatchSummary(accepted=accepted, rejected=rejected, revoked=revoked)


class CheckPushReceipts:
    """Lit les reçus des tickets en attente et détache les jetons morts.

    À lancer par une commande planifiée (`scripts/push_receipts.py`), jamais
    au démarrage (AGENTS.md §6.4). Sans elle, un appareil désinstallé après
    avoir été accepté par Expo continuerait à recevoir des envois — et Expo
    finit par bannir les expéditeurs qui persistent.
    """

    def __init__(
        self,
        *,
        devices: DeviceRepository,
        tickets: PushTicketRepository,
        gateway: PushGateway,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._devices = devices
        self._tickets = tickets
        self._gateway = gateway
        self._clock = clock
        self._uow = uow

    async def execute(self, *, limit: int = RECEIPT_BATCH_SIZE) -> int:
        """Traite un lot de tickets. Rend le nombre de jetons révoqués."""
        now = self._clock.now()
        pending = await self._tickets.pending(before=now - RECEIPT_DELAY, limit=limit)
        if not pending:
            return 0

        by_ticket = {ticket.ticket_id: ticket.token for ticket in pending}
        receipts = await self._gateway.receipts(list(by_ticket))

        revoked = 0
        checked: list[str] = []
        for receipt in receipts:
            token = by_ticket.get(receipt.ticket_id)
            if token is None:
                # Un reçu qu'on n'a pas demandé : on ne touche à rien.
                continue
            checked.append(receipt.ticket_id)
            if receipt.ok or receipt.error is None or not receipt.error.revokes_token:
                continue
            async with self._uow.atomic():
                revoked += await self._devices.revoke_token(token, at=now)
            logger.warning("devices.token_revoked", reason=receipt.error.value, stage="receipt")

        if checked:
            async with self._uow.atomic():
                await self._tickets.mark_checked(checked, at=now)
        logger.info("devices.receipts_checked", checked=len(checked), revoked=revoked)
        return revoked


# ---------------------------------------------------------------------
# Quand un compte disparaît
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MainDeviceSummary:
    """L'appareil principal d'un compte, réduit à deux champs.

    C'est ce que `bootstrap/wiring.py` sert au module des comptes pour la
    trace de départ (ADR-0015). Pas la fiche entière : la trace est anonyme,
    et modèle + version d'OS suffisent à reconnaître quelqu'un dans un petit
    effectif.
    """

    platform: str
    app_version: str


class GetMainDevice:
    """L'appareil vu en dernier d'un compte — « le téléphone de la personne »."""

    def __init__(self, *, devices: DeviceRepository) -> None:
        self._devices = devices

    async def execute(self, user_id: UUID) -> MainDeviceSummary | None:
        """Rend plateforme et version de l'app, ou `None` sans appareil déclaré."""
        device = await self._devices.main_device_of(user_id)
        if device is None:
            return None
        return MainDeviceSummary(
            platform=device.info.platform.value, app_version=device.info.app_version
        )


class ForgetUserDevices:
    """Efface les appareils d'un compte supprimé — réaction à `AccountDeleted`.

    Tout part : fiches, jetons, pays. Idempotent : rejouer l'événement pour
    un compte déjà oublié n'efface rien et ne lève pas. Les tickets Expo en
    attente de reçu ne portent pas de `user_id` et se purgent avec la
    commande planifiée ; ils ne sont pas touchés ici.
    """

    def __init__(self, *, devices: DeviceRepository, uow: UnitOfWork) -> None:
        self._devices = devices
        self._uow = uow

    async def execute(self, user_id: UUID) -> int:
        """Efface les appareils du compte. Rend le nombre effacé."""
        async with self._uow.atomic():
            removed = await self._devices.delete_all_of_user(user_id)
        logger.info("devices.user_forgotten", user_id=str(user_id), devices=removed)
        return removed
