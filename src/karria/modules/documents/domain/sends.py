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
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
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
#: Envois par jour civil vers une **même adresse tierce**, par compte, puis
#: tous comptes confondus (plus large : plusieurs personnes peuvent candidater
#: au même recruteur le même jour, pas trente).
DAILY_SENDS_PER_RECIPIENT: Final = 2
DAILY_SENDS_PER_RECIPIENT_GLOBAL: Final = 10
#: Longueur maximale d'un nom affiché (signature, nom de pièce sans extension).
MAX_DISPLAY_NAME_LENGTH: Final = 60
#: Caractères interdits dans une adresse, au-delà de la forme : ce que le
#: parseur RFC 5322 réinterprète (groupes, commentaires, chevrons, guillemets).
_ADDRESS_FORBIDDEN: Final = re.compile(r'[<>(),;:"\[\]\\]')
#: Ce qu'un nom affiché peut contenir : lettres Unicode, espace, tiret, apostrophe, point.
_NAME_KEEP: Final = re.compile(r"[^\W\d_]|[ \-'.\u2019]")
#: Une suite chiffre-point ou un jeton qui ressemble à une adresse IP : refusé d'emblée.
_DIGIT_DOT: Final = re.compile(r"\d\.|\.\d")
_IP_LIKE: Final = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")
#: Un point suivi d'autre chose qu'une espace ou la fin (« acme.fr ») — sauf
#: l'initiale (« J. Dupont »), qui est un point **suivi** d'une espace.
_DOT_INSIDE: Final = re.compile(r"\.(?! |$)")
#: Ce qui ressemble à un lien, pour `looks_like_link`.
_LINK_LIKE: Final = re.compile(r"(https?|www\.|\.[^\W\d_]{2,})", re.IGNORECASE)


def looks_like_link(text: str) -> bool:
    """« acme.fr », « www.x », « http… », « 10.0.0.1 » : un nom qui est un lien n'est pas un nom."""
    return bool(_LINK_LIKE.search(text) or _IP_LIKE.search(text) or _DIGIT_DOT.search(text))


def safe_display_name(name: str | None) -> str:
    """Le nom tel qu'il peut signer un message : lettres seulement, borné, jamais un lien.

    Normalisé NFKC d'abord (une lettre pleine chasse ou composée devient sa
    forme usuelle, un homoglyphe ne passe pas pour autre chose), puis réduit
    aux lettres Unicode, espaces, tirets, apostrophes et points d'initiale.
    Refusé — `""` — s'il ressemble à un lien, à une adresse IP, à une suite
    chiffre-point, ou si un point n'est pas une initiale. L'appelant choisit
    alors un repli neutre (« Une personne », « CV.pdf »).
    """
    text = unicodedata.normalize("NFKC", name or "")
    if looks_like_link(text):
        return ""
    cleaned = "".join(_NAME_KEEP.findall(text))
    cleaned = re.sub(r" {2,}", " ", cleaned)[:MAX_DISPLAY_NAME_LENGTH].strip(" -.")
    if not cleaned or _DOT_INSIDE.search(cleaned):
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

    Au-delà de la forme, elle doit être ce que le parseur RFC 5322 en lirait,
    **exactement** et **une seule** : ni caractère de contrôle, ni chevrons,
    guillemets, parenthèses, virgule, point-virgule, deux-points, crochets ou
    barre oblique inverse — tout ce qui ferait lire un autre destinataire ou
    un nom affiché.

    Raises:
        RecipientInvalidError: Forme inacceptable.
    """
    cleaned = address.strip()
    if (
        not cleaned
        or len(cleaned) > MAX_ADDRESS_LENGTH
        or not _ADDRESS.match(cleaned)
        or _ADDRESS_FORBIDDEN.search(cleaned)
        or any(ord(c) < 32 or ord(c) == 127 for c in cleaned)  # noqa: PLR2004
        or parseaddr(cleaned, strict=True) != ("", cleaned)
    ):
        raise RecipientInvalidError()
    local, _, domain = cleaned.rpartition("@")
    return f"{local}@{domain.lower()}"


def canonical_address(address: str) -> str:
    """La clé sur laquelle on compte un destinataire : minuscules, étiquette `+` retirée.

    `RH@Acme.fr`, `rh+karria@acme.fr` et `rh@acme.fr` sont la même boîte ;
    les compter séparément permettrait de contourner le plafond.
    """
    local, _, domain = address.strip().rpartition("@")
    return f"{local.split('+', 1)[0].lower()}@{domain.lower()}"


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


def ensure_recipient_quota(by_account_today: int, by_all_today: int, now: datetime) -> None:
    """Refuse le troisième envoi du jour d'un compte vers une même adresse tierce,
    et le onzième tous comptes confondus.

    Raises:
        SendQuotaExceededError: `scope` `recipient` ou `recipient_global` (journal
            seulement : la réponse au client est générique).
    """
    if by_account_today >= DAILY_SENDS_PER_RECIPIENT:
        raise SendQuotaExceededError(retry_after=seconds_until_tomorrow(now), scope="recipient")
    if by_all_today >= DAILY_SENDS_PER_RECIPIENT_GLOBAL:
        raise SendQuotaExceededError(
            retry_after=seconds_until_tomorrow(now), scope="recipient_global"
        )
