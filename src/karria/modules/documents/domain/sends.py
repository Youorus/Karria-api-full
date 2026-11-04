"""L'envoi des documents par e-mail : le journal, le quota, l'adresse (ADR-0031).

Contrat
-------
- **Cinq envois par jour civil (UTC) et par compte**, comptés sur les envois
  **demandés** (le journal), pas sur les envois remis : un échec consomme sa
  place, sinon une adresse injoignable permettrait des tentatives sans fin.
- L'adresse est validée ici, sans réseau : forme, longueur, un domaine avec
  un point. Une adresse acceptée peut encore être refusée par le serveur
  receveur ; le journal le dira (`failed`).
- Jamais de texte libre : le corps du message est un gabarit du socle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from karria.modules.documents.domain.errors import RecipientInvalidError, SendQuotaExceededError

#: Envois demandés par compte et par jour civil (UTC).
DAILY_SEND_LIMIT: Final = 5
#: Documents distincts dans un seul e-mail (CV + lettre, et un peu de marge).
MAX_DOCUMENTS_PER_SEND: Final = 4
#: Taille maximale d'une adresse (RFC 5321 : 254).
MAX_ADDRESS_LENGTH: Final = 254
_ADDRESS: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]{2,}$")


class SendStatus(StrEnum):
    """Où en est un envoi."""

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DocumentSend:
    """Une demande d'envoi : ses documents, son destinataire, son état."""

    id: UUID
    user_id: UUID
    generated_ids: tuple[UUID, ...]
    to: str
    status: SendStatus
    created_at: datetime
    sent_at: datetime | None = None
    error: str | None = None

    def succeeded(self, at: datetime) -> DocumentSend:
        """Le même envoi, remis."""
        return replace(self, status=SendStatus.SENT, sent_at=at, error=None)

    def failed(self, reason: str) -> DocumentSend:
        """Le même envoi, en échec ; la raison est technique, bornée, jamais rendue à l'app."""
        return replace(self, status=SendStatus.FAILED, error=reason[:200])


def normalize_address(address: str) -> str:
    """L'adresse telle qu'elle est gardée et envoyée.

    Raises:
        RecipientInvalidError: Forme inacceptable.
    """
    cleaned = address.strip()
    if not cleaned or len(cleaned) > MAX_ADDRESS_LENGTH or not _ADDRESS.match(cleaned):
        raise RecipientInvalidError()
    local, _, domain = cleaned.rpartition("@")
    return f"{local}@{domain.lower()}"


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Le jour civil UTC qui contient `now` : [début, lendemain[."""
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def ensure_quota(sent_today: int, now: datetime) -> None:
    """Refuse le sixième envoi du jour, en disant quand réessayer.

    Raises:
        SendQuotaExceededError: Quota atteint ; `retry_after` jusqu'à minuit UTC.
    """
    if sent_today < DAILY_SEND_LIMIT:
        return
    _, tomorrow = day_bounds(now)
    raise SendQuotaExceededError(
        retry_after=max(int((tomorrow - now.astimezone(UTC)).total_seconds()), 1)
    )
