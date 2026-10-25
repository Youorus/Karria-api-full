"""Appareils et notifications push : les règles pures.

Ce qu'est un appareil
---------------------
Une **installation** de l'app, identifiée par l'app elle-même
(`installation_id`, tiré une fois et conservé dans son stockage), rattachée
au compte connecté. Même téléphone, deux comptes : deux appareils. Même
compte, réinstallation : un nouvel appareil. C'est le couple
`(user_id, installation_id)` qui est unique, et c'est lui que
`PUT /devices/{installation_id}` met à jour à chaque démarrage.

Ce que ce fichier ne contient pas
---------------------------------
Aucune adresse IP — le pays, s'il est connu, vient d'un en-tête posé par le
mandataire de confiance, jamais d'une géolocalisation faite ici. Aucun appel
à Expo : le domaine décide **quoi** envoyer et **que faire** d'un ticket ;
l'envoi est un acte technique derrière un port.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from karria.shared.push import MAX_BODY_LENGTH, MAX_TITLE_LENGTH, PushMessage

#: Format d'un jeton Expo. `ExponentPushToken[...]` est la forme actuelle ;
#: `ExpoPushToken[...]` l'ancienne, encore émise par certains SDK.
EXPO_PUSH_TOKEN_PATTERN: Final = re.compile(r"^Expo(?:nent)?PushToken\[[A-Za-z0-9_\-]{1,200}\]$")

#: Plafond d'un envoi groupé chez Expo. Au-delà, l'API refuse le lot entier.
MAX_PUSH_BATCH_SIZE: Final = 100


def is_expo_push_token(value: str) -> bool:
    """Le jeton a-t-il la forme qu'Expo produit ?

    Un jeton d'une autre forme — un jeton FCM brut, par exemple — serait
    refusé par Expo à l'envoi, mais seulement à l'envoi : le contrôler à
    l'enregistrement remonte l'erreur là où l'app peut la corriger.
    """
    return EXPO_PUSH_TOKEN_PATTERN.match(value) is not None


class Platform(StrEnum):
    """Le système de l'appareil."""

    IOS = "ios"
    ANDROID = "android"


class PushPermission(StrEnum):
    """Ce que la personne a répondu à la demande de notifications."""

    GRANTED = "granted"
    DENIED = "denied"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Ce que l'app déclare d'elle-même et de l'appareil, à chaque démarrage.

    Déclaratif et indicatif : rien ici n'est prouvé, rien ne décide d'un
    droit. Cela sert au support (« quelle version avez-vous ? ») et aux
    statistiques de plateformes.
    """

    platform: Platform
    push_token: str | None
    push_permission: PushPermission
    os_name: str
    os_version: str
    is_physical_device: bool
    app_version: str
    device_brand: str | None = None
    device_manufacturer: str | None = None
    device_model: str | None = None
    device_model_id: str | None = None
    device_year_class: int | None = None
    app_build: str | None = None
    locale: str | None = None
    timezone: str | None = None
    region: str | None = None


@dataclass(frozen=True, slots=True)
class Device:
    """Une installation de l'app rattachée à un compte.

    Attributes:
        country: Pays d'où la dernière requête est venue, selon le
            mandataire de confiance (`CF-IPCountry`). `None` si l'en-tête
            manque. Jamais une adresse IP.
        first_seen_at: Première déclaration de cette installation.
        last_seen_at: Dernière déclaration — l'app appelle à chaque
            démarrage, c'est donc « dernière ouverture » à peu de chose près.
    """

    id: UUID
    user_id: UUID
    installation_id: str
    info: DeviceInfo
    country: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime

    @property
    def push_enabled(self) -> bool:
        """Peut-on joindre cet appareil ?

        Il faut **les deux** : un jeton, et la permission accordée. Un jeton
        sans permission existe — iOS en délivre un avant la demande — mais
        le système n'affichera rien.
        """
        return self.info.push_token is not None and (
            self.info.push_permission is PushPermission.GRANTED
        )

    def declared(self, info: DeviceInfo, *, country: str | None, at: datetime) -> Device:
        """Prend en compte une nouvelle déclaration de l'app."""
        return replace(self, info=info, country=country, last_seen_at=at, updated_at=at)

    def token_revoked(self, *, at: datetime) -> Device:
        """Détache le jeton : Expo l'a déclaré mort, ou l'app s'est déconnectée.

        L'appareil reste connu — il reviendra avec un nouveau jeton au
        prochain démarrage — mais plus rien ne lui est envoyé d'ici là.
        """
        return replace(self, info=replace(self.info, push_token=None), updated_at=at)


# ---------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PushNotification:
    """Ce qu'on veut dire, indépendamment de qui le reçoit.

    `data` est ce que l'app lit à l'ouverture de la notification — un
    écran, un identifiant. Des chaînes uniquement : c'est ce que les deux
    plateformes garantissent de transmettre intact. `channel` (canal
    Android), `category` (catégorie iOS) et `badge` voyagent hors de `data`.

    Les émetteurs ne la composent pas à la main : ils écrivent un
    `PushMessage` (contrat de `shared/push`) et `from_message` le traduit.
    """

    title: str
    body: str
    data: Mapping[str, str]
    channel: str | None = None
    category: str | None = None
    badge: int | None = None

    @classmethod
    def from_message(cls, message: PushMessage) -> PushNotification:
        """La notification que décrit un message du contrat."""
        return cls(
            title=message.title,
            body=message.body,
            data=message.data,
            channel=message.channel.value,
            category=message.category,
            badge=message.badge,
        )

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title) > MAX_TITLE_LENGTH:
            msg = "Le titre d'une notification est obligatoire et borné."
            raise ValueError(msg)
        if not self.body.strip() or len(self.body) > MAX_BODY_LENGTH:
            msg = "Le corps d'une notification est obligatoire et borné."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PushEnvelope:
    """Une notification adressée à un jeton précis."""

    token: str
    notification: PushNotification


class PushError(StrEnum):
    """Les erreurs qu'Expo rapporte, et qui décident de la suite.

    Une seule mérite une action immédiate : `DEVICE_NOT_REGISTERED`, qui dit
    que le jeton est mort — l'app est désinstallée, ou le système a
    révoqué le jeton. Continuer à lui envoyer ferait bannir l'expéditeur.
    """

    DEVICE_NOT_REGISTERED = "DeviceNotRegistered"
    MESSAGE_TOO_BIG = "MessageTooBig"
    MESSAGE_RATE_EXCEEDED = "MessageRateExceeded"
    MISMATCH_SENDER_ID = "MismatchSenderId"
    INVALID_CREDENTIALS = "InvalidCredentials"
    UNKNOWN = "Unknown"

    @classmethod
    def parse(cls, raw: object) -> PushError:
        """Lit une erreur Expo ; ce qu'on ne connaît pas devient `UNKNOWN`."""
        try:
            return cls(str(raw))
        except ValueError:
            return cls.UNKNOWN

    @property
    def revokes_token(self) -> bool:
        """Faut-il détacher le jeton de l'appareil ?"""
        return self is PushError.DEVICE_NOT_REGISTERED


@dataclass(frozen=True, slots=True)
class PushTicket:
    """La réponse immédiate d'Expo à un envoi.

    `ok` avec un `ticket_id` : Expo a **accepté** le message, pas encore
    livré. La livraison se lit plus tard dans le reçu. Sans `ok`, `error`
    dit pourquoi — et s'il faut révoquer le jeton.
    """

    token: str
    ok: bool
    ticket_id: str | None = None
    error: PushError | None = None


@dataclass(frozen=True, slots=True)
class PushReceipt:
    """Ce que le service de notifications du système a répondu, après coup."""

    ticket_id: str
    ok: bool
    error: PushError | None = None


def batched(envelopes: Sequence[PushEnvelope]) -> list[list[PushEnvelope]]:
    """Découpe en lots acceptables par Expo."""
    return [
        list(envelopes[start : start + MAX_PUSH_BATCH_SIZE])
        for start in range(0, len(envelopes), MAX_PUSH_BATCH_SIZE)
    ]
