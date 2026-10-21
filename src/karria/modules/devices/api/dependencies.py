"""Câblage du module `devices`."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Path, Request

from karria.modules.devices.application import (
    CheckPushReceipts,
    DeviceRepository,
    ForgetUserDevices,
    GetMainDevice,
    PushGateway,
    PushTicketRepository,
    RegisterDevice,
    SendPushToUser,
    UnregisterDevice,
)
from karria.modules.devices.infrastructure import (
    ExpoPushGateway,
    TortoiseDeviceRepository,
    TortoisePushTicketRepository,
)
from karria.shared.clock import Clock, SystemClock
from karria.shared.http.dependencies import SettingsDep, UnitOfWorkDep, get_unit_of_work

#: En-tête posé par le mandataire de confiance (Cloudflare) avec le pays de
#: l'appelant. Lu tel quel, jamais déduit d'une adresse IP ici. Les valeurs
#: spéciales `XX` (inconnu) et `T1` (Tor) ne sont pas des pays.
COUNTRY_HEADER: Final = "CF-IPCountry"
_NOT_A_COUNTRY: Final = frozenset({"XX", "T1"})


def get_clock() -> Clock:
    """L'horloge réelle."""
    return SystemClock()


def get_device_repository() -> DeviceRepository:
    """Le dépôt des appareils."""
    return TortoiseDeviceRepository()


def get_ticket_repository() -> PushTicketRepository:
    """Le dépôt des tickets en attente de reçu."""
    return TortoisePushTicketRepository()


def get_push_gateway(settings: SettingsDep) -> PushGateway:
    """La passerelle Expo, d'après la configuration."""
    return ExpoPushGateway.from_settings(
        api_url=settings.push.expo_api_url,
        access_token=settings.push.expo_access_token.get_secret_value(),
        timeout_seconds=settings.push.request_timeout_seconds,
    )


def get_request_country(request: Request) -> str | None:
    """Le pays de l'appelant selon le mandataire, ou `None`."""
    raw = request.headers.get(COUNTRY_HEADER, "").strip().upper()
    if len(raw) != 2 or not raw.isalpha() or raw in _NOT_A_COUNTRY:  # noqa: PLR2004
        return None
    return raw


ClockDep = Annotated[Clock, Depends(get_clock)]
DeviceRepositoryDep = Annotated[DeviceRepository, Depends(get_device_repository)]
TicketRepositoryDep = Annotated[PushTicketRepository, Depends(get_ticket_repository)]
PushGatewayDep = Annotated[PushGateway, Depends(get_push_gateway)]
RequestCountryDep = Annotated[str | None, Depends(get_request_country)]

#: L'identifiant d'installation, tel que l'app le tire : opaque, court, sûr
#: dans une URL. Tout autre caractère est refusé en `422`.
InstallationIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description="Identifiant d'installation tiré par l'app, stable tant qu'elle est installée.",
    ),
]


def get_register_device(
    devices: DeviceRepositoryDep, clock: ClockDep, uow: UnitOfWorkDep
) -> RegisterDevice:
    """Assemble la déclaration d'un appareil."""
    return RegisterDevice(devices=devices, clock=clock, uow=uow)


def get_unregister_device(devices: DeviceRepositoryDep, uow: UnitOfWorkDep) -> UnregisterDevice:
    """Assemble le détachement d'un appareil."""
    return UnregisterDevice(devices=devices, uow=uow)


def get_send_push_to_user(
    devices: DeviceRepositoryDep,
    tickets: TicketRepositoryDep,
    gateway: PushGatewayDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> SendPushToUser:
    """Assemble l'envoi à un compte.

    **C'est la dépendance que `bootstrap/wiring.py` branche** sur le port
    d'un module qui veut notifier quelqu'un.
    """
    return SendPushToUser(devices=devices, tickets=tickets, gateway=gateway, clock=clock, uow=uow)


def get_check_push_receipts(
    devices: DeviceRepositoryDep,
    tickets: TicketRepositoryDep,
    gateway: PushGatewayDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> CheckPushReceipts:
    """Assemble la lecture des reçus."""
    return CheckPushReceipts(
        devices=devices, tickets=tickets, gateway=gateway, clock=clock, uow=uow
    )


def get_get_main_device(devices: DeviceRepositoryDep) -> GetMainDevice:
    """Assemble la lecture de l'appareil principal.

    **C'est la dépendance que `bootstrap/wiring.py` branche** sur le port
    `MainDeviceLookup` du module des comptes (ADR-0015).
    """
    return GetMainDevice(devices=devices)


def build_forget_user_devices() -> ForgetUserDevices:
    """Assemble l'oubli d'un compte **hors requête**, pour le pont du bus.

    Un abonné au bus n'a pas de requête, donc pas de `Depends` : il reçoit
    les adaptateurs réellement déployés, construits ici pour que la liste
    reste à un seul endroit.
    """
    return ForgetUserDevices(devices=get_device_repository(), uow=get_unit_of_work())


RegisterDeviceDep = Annotated[RegisterDevice, Depends(get_register_device)]
UnregisterDeviceDep = Annotated[UnregisterDevice, Depends(get_unregister_device)]
SendPushToUserDep = Annotated[SendPushToUser, Depends(get_send_push_to_user)]
CheckPushReceiptsDep = Annotated[CheckPushReceipts, Depends(get_check_push_receipts)]
GetMainDeviceDep = Annotated[GetMainDevice, Depends(get_get_main_device)]
