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
- **Anti-hameçonnage** (revue de sécurité du 2026-11-05) : un message signé
  Karria part vers des tiers, et le nom de la personne comme les noms de
  fichiers viennent de son CV. L'objet est **fixe** ; le nom qui signe et
  les noms de pièces sont réduits à un jeu strict de caractères et
  **refusés** s'ils ressemblent à une adresse ou un lien (`http`, `www`,
  un point suivi de lettres). Vers une adresse **tierce**, deux envois par
  jour par destinataire et un plafond par origine ; vers sa propre adresse,
  seul le quota du compte compte.
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
#: Envois par jour civil vers une **même adresse tierce**, tous comptes confondus.
DAILY_SENDS_PER_RECIPIENT: Final = 2
#: Longueur maximale d'un nom affiché (signature, nom de pièce sans extension).
MAX_DISPLAY_NAME_LENGTH: Final = 60
#: Ce qu'un nom affiché peut contenir : lettres, chiffres, espace, tiret, apostrophe, point.
_DISPLAY_FORBIDDEN: Final = re.compile(r"[^\w \-'.]")
#: Ce qui ressemble à un lien ou un domaine : refusé dans un nom.
_LINK_LIKE: Final = re.compile(r"(https?|www\.|\.[a-zA-Z]{2,})", re.IGNORECASE)


def looks_like_link(text: str) -> bool:
    """« acme.fr », « www.x », « http… » : un nom qui est un lien n'est pas un nom."""
    return bool(_LINK_LIKE.search(text))


def safe_display_name(name: str | None) -> str:
    """Le nom tel qu'il peut signer un message : jeu strict, borné, jamais un lien.

    Rend `""` si rien de sûr ne reste ; l'appelant choisit alors un repli
    neutre (« Une personne », « CV.pdf »).
    """
    cleaned = _DISPLAY_FORBIDDEN.sub("", name or "")[:MAX_DISPLAY_NAME_LENGTH].strip(" .-")
    if not cleaned or looks_like_link(cleaned):
        return ""
    return cleaned


def safe_attachment_name(base: str, extension: str, fallback: str) -> str:
    """« CV - Awa Diallo » + `pdf` → « CV - Awa Diallo.pdf », ou le repli si le nom est douteux."""
    safe = safe_display_name(base)
    return f"{safe or fallback}.{extension}"


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


def seconds_until_tomorrow(now: datetime) -> int:
    """Le délai jusqu'à minuit UTC, au moins une seconde."""
    _, tomorrow = day_bounds(now)
    return max(int((tomorrow - now.astimezone(UTC)).total_seconds()), 1)


def ensure_quota(sent_today: int, now: datetime) -> None:
    """Refuse le sixième envoi du jour, en disant quand réessayer.

    Raises:
        SendQuotaExceededError: Quota atteint ; `retry_after` jusqu'à minuit UTC.
    """
    if sent_today < DAILY_SEND_LIMIT:
        return
    raise SendQuotaExceededError(retry_after=seconds_until_tomorrow(now), scope="account")


def ensure_recipient_quota(sent_to_recipient_today: int, now: datetime) -> None:
    """Refuse le troisième envoi du jour vers une même adresse tierce.

    Raises:
        SendQuotaExceededError: `scope: recipient`, `retry_after` jusqu'à minuit UTC.
    """
    if sent_to_recipient_today < DAILY_SENDS_PER_RECIPIENT:
        return
    raise SendQuotaExceededError(retry_after=seconds_until_tomorrow(now), scope="recipient")
