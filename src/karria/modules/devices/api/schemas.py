"""Contrat HTTP du module `devices`.

Reproduit `docs/contrat-api-appareils.md` de `karria_app`. La validation est
**stricte** : longueurs, énumérations, format du jeton Expo. Une déclaration
hors cadre répond `422` plutôt que d'être tronquée en silence — ce qui
remonte l'erreur là où l'app peut la corriger.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from karria.modules.devices.domain import (
    Device,
    DeviceInfo,
    Platform,
    PushPermission,
    is_expo_push_token,
)

#: Même bornes que les colonnes : une valeur acceptée ici tient en base.
_LABEL = 100
_TOKEN = 256


class DeviceRegistrationRequest(BaseModel):
    """`PUT /devices/{installation_id}`."""

    platform: Platform
    push_token: str | None = Field(
        default=None,
        max_length=_TOKEN,
        description="Jeton Expo (`ExponentPushToken[…]`), ou `null` si l'app n'en a pas.",
    )
    push_permission: PushPermission
    os_name: str = Field(min_length=1, max_length=_LABEL)
    os_version: str = Field(min_length=1, max_length=_LABEL)
    device_brand: str | None = Field(default=None, max_length=_LABEL)
    device_manufacturer: str | None = Field(default=None, max_length=_LABEL)
    device_model: str | None = Field(default=None, max_length=_LABEL)
    device_model_id: str | None = Field(default=None, max_length=_LABEL)
    device_year_class: int | None = Field(default=None, ge=1990, le=2100)
    is_physical_device: bool
    app_version: str = Field(min_length=1, max_length=_LABEL)
    app_build: str | None = Field(default=None, max_length=_LABEL)
    locale: str | None = Field(default=None, max_length=_LABEL)
    timezone: str | None = Field(default=None, max_length=_LABEL)
    region: str | None = Field(default=None, max_length=_LABEL)

    @field_validator("push_token")
    @classmethod
    def _expo_format(cls, value: str | None) -> str | None:
        """Un jeton présent doit être un jeton Expo ; une chaîne vide vaut absence."""
        if value is None or value == "":
            return None
        if not is_expo_push_token(value):
            msg = "Le jeton doit avoir la forme ExponentPushToken[…]."
            raise ValueError(msg)
        return value

    def to_info(self) -> DeviceInfo:
        """Projette la requête vers le domaine."""
        return DeviceInfo(
            platform=self.platform,
            push_token=self.push_token,
            push_permission=self.push_permission,
            os_name=self.os_name,
            os_version=self.os_version,
            is_physical_device=self.is_physical_device,
            app_version=self.app_version,
            device_brand=self.device_brand,
            device_manufacturer=self.device_manufacturer,
            device_model=self.device_model,
            device_model_id=self.device_model_id,
            device_year_class=self.device_year_class,
            app_build=self.app_build,
            locale=self.locale,
            timezone=self.timezone,
            region=self.region,
        )


class DeviceResponse(BaseModel):
    """Ce que l'app lit après une déclaration."""

    installation_id: str
    push_enabled: bool = Field(
        description="Vrai si le serveur peut joindre cet appareil : jeton présent et permission accordée."
    )

    @classmethod
    def from_domain(cls, device: Device) -> DeviceResponse:
        """Projette un appareil."""
        return cls(installation_id=device.installation_id, push_enabled=device.push_enabled)


class TestNotificationResponse(BaseModel):
    """Résultat de l'envoi d'essai à soi-même."""

    targeted: int = Field(description="Appareils visés.")
    accepted: int = Field(description="Enveloppes acceptées par Expo.")
    rejected: int = Field(description="Enveloppes refusées, jetons morts compris.")
