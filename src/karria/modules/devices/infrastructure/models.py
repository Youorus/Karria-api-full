"""Modèles de persistance du module `devices`.

Aucune adresse IP, nulle part : le pays vient d'un en-tête du mandataire
de confiance, et c'est tout ce qu'on garde de l'origine d'une requête.
"""

from __future__ import annotations

from tortoise import fields

from karria.modules.devices.domain import Platform, PushPermission
from karria.shared.database import BaseDBModel

#: Longueur généreuse d'un jeton Expo : `ExponentPushToken[` + 22 caractères
#: usuels + `]`, avec de la marge pour une forme future.
PUSH_TOKEN_MAX_LENGTH = 256
#: L'identifiant d'installation est tiré par l'app (UUID ou équivalent).
INSTALLATION_ID_MAX_LENGTH = 128
#: Les libellés déclaratifs (modèle, marque, version…) sont bornés court :
#: ils servent au support, pas à l'exhaustivité.
LABEL_MAX_LENGTH = 100


class DeviceModel(BaseDBModel):
    """Une installation de l'app, rattachée à un compte."""

    user_id = fields.UUIDField(
        db_index=True,
        description="Compte connecté lors de la déclaration. Pas de clé étrangère : `devices` "
        "ne lit pas la table d'`identity` (règle 4).",
    )
    installation_id = fields.CharField(max_length=INSTALLATION_ID_MAX_LENGTH)
    platform = fields.CharEnumField(Platform, max_length=16)
    push_token = fields.CharField(
        max_length=PUSH_TOKEN_MAX_LENGTH,
        null=True,
        db_index=True,
        description="Jeton Expo. Nul tant que l'app n'en a pas, ou dès qu'Expo l'a déclaré mort.",
    )
    push_permission = fields.CharEnumField(PushPermission, max_length=16)
    os_name = fields.CharField(max_length=LABEL_MAX_LENGTH)
    os_version = fields.CharField(max_length=LABEL_MAX_LENGTH)
    device_brand = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    device_manufacturer = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    device_model = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    device_model_id = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    device_year_class = fields.IntField(null=True)
    is_physical_device = fields.BooleanField()
    app_version = fields.CharField(max_length=LABEL_MAX_LENGTH)
    app_build = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    locale = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    timezone = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    region = fields.CharField(max_length=LABEL_MAX_LENGTH, null=True)
    country = fields.CharField(
        max_length=2, null=True, description="ISO 3166-1, d'après `CF-IPCountry`. Jamais une IP."
    )
    first_seen_at = fields.DatetimeField()
    last_seen_at = fields.DatetimeField(db_index=True)

    class Meta:
        table = "devices_installations"
        table_description = "Installations de l'app par compte, et leur jeton push."
        unique_together = (("user_id", "installation_id"),)


class PushTicketModel(BaseDBModel):
    """Un ticket Expo dont on attend le reçu."""

    ticket_id = fields.CharField(max_length=64, unique=True)
    push_token = fields.CharField(max_length=PUSH_TOKEN_MAX_LENGTH)
    checked_at = fields.DatetimeField(null=True, db_index=True)

    class Meta:
        table = "devices_push_tickets"
        table_description = "Tickets Expo en attente de reçu, pour détacher les jetons morts."
