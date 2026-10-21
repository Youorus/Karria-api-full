"""Lecture des clés publiques des fournisseurs — implémentation de `JwksSource`.

Pourquoi un cache
-----------------
Sans lui, chaque connexion Apple ou Google ferait un appel réseau de plus,
et une panne du point de distribution des clés couperait toutes les
connexions. Les clés tournent rarement : une heure de cache est le
compromis usuel. Un `kid` inconnu déclenche une relecture forcée
(`refresh`) — c'est ainsi qu'une rotation est suivie sans attendre.

Le client HTTP est celui du socle (`shared/http/outbound.py`) : délai
d'attente et reprise sur 5xx y sont déjà réglés.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from karria.modules.identity.domain import InvalidSocialTokenError
from karria.shared.http.outbound import JsonClient, OutboundError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Durée de conservation d'un document JWKS, en secondes.
JWKS_TTL_SECONDS: Final = 3600


@dataclass(slots=True)
class _Cached:
    fetched_at: float
    document: dict[str, Any]


class HttpJwksSource:
    """Télécharge et met en cache les JWKS, par URL."""

    def __init__(
        self,
        *,
        ttl_seconds: float = JWKS_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        client_factory: Callable[[str], JsonClient] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._monotonic = monotonic
        self._client_factory = client_factory or _default_client
        self._cache: dict[str, _Cached] = {}

    async def fetch(self, url: str) -> dict[str, Any]:
        """Rend le document, depuis le cache s'il est encore frais."""
        cached = self._cache.get(url)
        if cached is not None and self._monotonic() - cached.fetched_at < self._ttl:
            return cached.document
        return await self.refresh(url)

    async def refresh(self, url: str) -> dict[str, Any]:
        """Relit le document, quoi qu'il y ait en cache.

        Raises:
            InvalidSocialTokenError: Les clés sont injoignables. Pour
                l'appelant, le jeton ne peut pas être prouvé — et dire plus
                renseignerait sur l'infrastructure. Le détail est journalisé.
        """
        try:
            response = await self._client_factory(url).get_json("")
            document = response.mapping
        except OutboundError as exc:
            logger.error("social.jwks_unavailable", url=url, detail=exc.detail)
            raise InvalidSocialTokenError from exc
        self._cache[url] = _Cached(fetched_at=self._monotonic(), document=document)
        logger.info("social.jwks_refreshed", url=url, keys=len(document.get("keys", [])))
        return document


def _default_client(url: str) -> JsonClient:
    """Un client du socle pointé sur l'URL exacte du JWKS.

    `JsonClient` compose `base_url + chemin` ; on lui donne l'URL entière en
    base et un chemin vide pour ne pas ajouter de barre finale, que certains
    serveurs refusent.
    """
    parts = urlsplit(url)
    return JsonClient(base_url=f"{parts.scheme}://{parts.netloc}{parts.path}")
