"""Ports du module `devices`.

Chacun décrit un besoin. L'envoi réel vers Expo, la base : derrière.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from karria.modules.devices.domain import (
    Device,
    Platform,
    PushEnvelope,
    PushReceipt,
    PushTicket,
)


class DeviceRepository(Protocol):
    """Port : accès persistant aux appareils."""

    async def find(self, user_id: UUID, installation_id: str) -> Device | None:
        """L'appareil de ce compte portant cet identifiant d'installation."""
        ...

    async def add(self, device: Device) -> None:
        """Enregistre un appareil."""
        ...

    async def save(self, device: Device) -> None:
        """Écrit l'état complet d'un appareil existant."""
        ...

    async def delete(self, user_id: UUID, installation_id: str) -> bool:
        """Retire l'appareil. Rend `False` s'il n'existait pas."""
        ...

    async def push_targets_of_user(self, user_id: UUID) -> Sequence[Device]:
        """Les appareils joignables d'un compte : jeton présent, permission accordée."""
        ...

    async def revoke_token(self, token: str, *, at: datetime) -> int:
        """Détache ce jeton de **tous** les appareils qui le portent.

        Tous, et pas seulement ceux du compte visé : un jeton suit
        l'installation, et l'installation a pu changer de compte.

        Returns:
            Le nombre d'appareils touchés.
        """
        ...

    async def main_device_of(self, user_id: UUID) -> Device | None:
        """L'appareil vu en dernier — « le téléphone de la personne »."""
        ...

    async def platform_stats(self) -> dict[Platform, int]:
        """Combien d'appareils par plateforme, toutes installations confondues."""
        ...

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface **tous** les appareils d'un compte, jetons compris.

        Returns:
            Le nombre d'appareils effacés.
        """
        ...


@dataclass(frozen=True, slots=True)
class StoredTicket:
    """Un ticket Expo en attente de son reçu."""

    ticket_id: str
    token: str


class PushTicketRepository(Protocol):
    """Port : les tickets dont on attend le reçu.

    Expo accepte un message et rend un ticket ; la livraison se lit dans
    le reçu, disponible quelques minutes plus tard. Sans mémoire des
    tickets, les jetons morts rapportés **par le reçu** ne seraient jamais
    révoqués.
    """

    async def add_many(self, tickets: Sequence[StoredTicket], *, at: datetime) -> None:
        """Mémorise des tickets acceptés."""
        ...

    async def pending(self, *, before: datetime, limit: int) -> Sequence[StoredTicket]:
        """Les tickets émis avant cet instant et jamais vérifiés, les plus anciens d'abord."""
        ...

    async def mark_checked(self, ticket_ids: Sequence[str], *, at: datetime) -> None:
        """Note que ces tickets ont eu leur reçu, quel qu'il soit."""
        ...

    async def purge_checked_before(self, moment: datetime) -> int:
        """Supprime les tickets vérifiés depuis assez longtemps."""
        ...


class PushGateway(Protocol):
    """Port : le service qui achemine les notifications jusqu'au système.

    Expo aujourd'hui. Le port ne le nomme pas : l'application choisit
    *quoi* envoyer, le fournisseur se contente d'acheminer.
    """

    async def send(self, envelopes: Sequence[PushEnvelope]) -> Sequence[PushTicket]:
        """Soumet un lot (≤ 100). Rend un ticket par enveloppe, dans l'ordre.

        Raises:
            DependencyUnavailableError: Le service n'a pas répondu.
        """
        ...

    async def receipts(self, ticket_ids: Sequence[str]) -> Sequence[PushReceipt]:
        """Interroge les reçus. Un ticket sans reçu n'apparaît pas encore.

        Raises:
            DependencyUnavailableError: Le service n'a pas répondu.
        """
        ...
