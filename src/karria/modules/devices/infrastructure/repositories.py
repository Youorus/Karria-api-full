"""Dépôts Tortoise du module `devices`. Ne rendent jamais un modèle."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from karria.modules.devices.application import StoredTicket
from karria.modules.devices.domain import Device, DeviceInfo, Platform
from karria.modules.devices.infrastructure.models import DeviceModel, PushTicketModel


def _to_device(row: DeviceModel) -> Device:
    return Device(
        id=row.id,
        user_id=row.user_id,
        installation_id=row.installation_id,
        info=DeviceInfo(
            platform=row.platform,
            push_token=row.push_token,
            push_permission=row.push_permission,
            os_name=row.os_name,
            os_version=row.os_version,
            is_physical_device=row.is_physical_device,
            app_version=row.app_version,
            device_brand=row.device_brand,
            device_manufacturer=row.device_manufacturer,
            device_model=row.device_model,
            device_model_id=row.device_model_id,
            device_year_class=row.device_year_class,
            app_build=row.app_build,
            locale=row.locale,
            timezone=row.timezone,
            region=row.region,
        ),
        country=row.country,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _columns(device: Device) -> dict[str, Any]:
    info = device.info
    return {
        "user_id": device.user_id,
        "installation_id": device.installation_id,
        "platform": info.platform,
        "push_token": info.push_token,
        "push_permission": info.push_permission,
        "os_name": info.os_name,
        "os_version": info.os_version,
        "device_brand": info.device_brand,
        "device_manufacturer": info.device_manufacturer,
        "device_model": info.device_model,
        "device_model_id": info.device_model_id,
        "device_year_class": info.device_year_class,
        "is_physical_device": info.is_physical_device,
        "app_version": info.app_version,
        "app_build": info.app_build,
        "locale": info.locale,
        "timezone": info.timezone,
        "region": info.region,
        "country": device.country,
        "first_seen_at": device.first_seen_at,
        "last_seen_at": device.last_seen_at,
    }


class TortoiseDeviceRepository:
    """Accès persistant aux appareils."""

    async def find(self, user_id: UUID, installation_id: str) -> Device | None:
        """L'appareil de ce compte portant cet identifiant."""
        row = await DeviceModel.get_or_none(user_id=user_id, installation_id=installation_id)
        return None if row is None else _to_device(row)

    async def add(self, device: Device) -> None:
        """Enregistre un appareil."""
        await DeviceModel.create(id=device.id, **_columns(device))

    async def save(self, device: Device) -> None:
        """Écrit l'état complet d'un appareil."""
        await DeviceModel.filter(id=device.id).update(**_columns(device))

    async def delete(self, user_id: UUID, installation_id: str) -> bool:
        """Retire l'appareil ; `False` s'il n'existait pas."""
        deleted = await DeviceModel.filter(
            user_id=user_id, installation_id=installation_id
        ).delete()
        return deleted > 0

    async def push_targets_of_user(self, user_id: UUID) -> Sequence[Device]:
        """Les appareils joignables : jeton présent et permission accordée."""
        rows = await DeviceModel.filter(
            user_id=user_id, push_token__isnull=False, push_permission="granted"
        ).order_by("-last_seen_at")
        return [_to_device(row) for row in rows]

    async def revoke_token(self, token: str, *, at: datetime) -> int:
        """Détache ce jeton de tous les appareils qui le portent."""
        return await DeviceModel.filter(push_token=token).update(push_token=None, updated_at=at)

    async def main_device_of(self, user_id: UUID) -> Device | None:
        """L'appareil vu en dernier."""
        row = await DeviceModel.filter(user_id=user_id).order_by("-last_seen_at").first()
        return None if row is None else _to_device(row)

    async def platform_stats(self) -> dict[Platform, int]:
        """Combien d'appareils par plateforme."""
        counts: Counter[Platform] = Counter()
        for platform in Platform:
            counts[platform] = await DeviceModel.filter(platform=platform).count()
        return dict(counts)

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface tous les appareils d'un compte."""
        return await DeviceModel.filter(user_id=user_id).delete()


class TortoisePushTicketRepository:
    """Accès persistant aux tickets en attente de reçu."""

    async def add_many(self, tickets: Sequence[StoredTicket], *, at: datetime) -> None:
        """Mémorise des tickets acceptés."""
        _ = at  # `created_at` est posé par la base, à l'insertion.
        await PushTicketModel.bulk_create(
            [PushTicketModel(ticket_id=t.ticket_id, push_token=t.token) for t in tickets],
            ignore_conflicts=True,
        )

    async def pending(self, *, before: datetime, limit: int) -> Sequence[StoredTicket]:
        """Les tickets émis avant cet instant et jamais vérifiés."""
        rows = (
            await PushTicketModel.filter(checked_at__isnull=True, created_at__lt=before)
            .order_by("created_at")
            .limit(limit)
        )
        return [StoredTicket(ticket_id=row.ticket_id, token=row.push_token) for row in rows]

    async def mark_checked(self, ticket_ids: Sequence[str], *, at: datetime) -> None:
        """Note que ces tickets ont eu leur reçu."""
        await PushTicketModel.filter(ticket_id__in=list(ticket_ids)).update(checked_at=at)

    async def purge_checked_before(self, moment: datetime) -> int:
        """Supprime les tickets vérifiés depuis assez longtemps."""
        return await PushTicketModel.filter(checked_at__lt=moment).delete()
