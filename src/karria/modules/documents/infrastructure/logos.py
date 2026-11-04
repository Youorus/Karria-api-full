"""Le logo d'une entreprise pour une lettre : lu sur **notre** stockage public, jamais ailleurs.

Contrat (ADR-0030 § 3)
----------------------
- L'app passe `logo_url`, l'adresse publique que le fil d'offres lui a donnée
  (bucket `logos`, ADR-0021). On ne télécharge **que** depuis l'hôte public du
  stockage : une adresse étrangère est ignorée (rien à rendre), ce qui ferme
  la porte à un rendu qui irait chercher n'importe quoi sur Internet.
- Borné à 512 Ko, 5 s ; un échec rend `None` et la lettre se rend sans logo,
  avec l'avertissement `logo_unavailable`. Le réseau sortant passe par
  `urllib` dans un fil, comme le reste du socle.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from typing import Final
from urllib.parse import urlsplit

from karria.shared.logging import get_logger

logger = get_logger(__name__)

MAX_LOGO_BYTES: Final = 512 * 1024
TIMEOUT_SECONDS: Final = 5.0


class StorageLogoFetcher:
    """Implémente `LogoFetcher` : télécharge depuis l'hôte public du stockage seulement."""

    def __init__(self, public_base_url: str | None) -> None:
        self._host = urlsplit(public_base_url).netloc.lower() if public_base_url else ""

    def allowed(self, url: str) -> bool:
        """L'adresse est sur notre hôte public, en HTTPS (ou HTTP vers le même hôte en local)."""
        parts = urlsplit(url)
        return (
            bool(self._host)
            and parts.scheme in ("https", "http")
            and parts.netloc.lower() == self._host
        )

    async def fetch(self, url: str) -> bytes | None:
        if not self.allowed(url):
            logger.info("documents.logo_host_refused", host=urlsplit(url).netloc[:80])
            return None
        try:
            return await asyncio.to_thread(self._read, url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("documents.logo_unavailable", error=str(exc)[:200])
            return None

    @staticmethod
    def _read(url: str) -> bytes | None:
        request = urllib.request.Request(url, headers={"User-Agent": "Karria/1.0"})  # noqa: S310 - hôte vérifié
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            data: bytes = response.read(MAX_LOGO_BYTES + 1)
        if len(data) > MAX_LOGO_BYTES or not data:
            return None
        return data


class NoLogoFetcher:
    """Aucun logo, jamais : le repli des tests et d'un stockage sans adresse publique."""

    async def fetch(self, url: str) -> bytes | None:
        del url
        return None
