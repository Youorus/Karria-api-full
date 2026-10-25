"""Les logos : les aller chercher poliment, les mettre au carré, les ranger par condensat.

Trois adaptateurs, un par port :

- `HttpLogoFetcher` — httpx, délais courts, taille bornée, redirections
  bornées, **jamais** une adresse privée (une URL de logo vient d'un tiers :
  `http://169.254.169.254/` ne doit pas atteindre le réseau du serveur),
  et un débit par site (`PER_HOST_CONCURRENCY`, `PER_HOST_INTERVAL`).
- `PillowLogoProcessor` — `shared.images.square_icon`, puis le condensat.
- `StorageLogoStore` — le bucket public `logos`, `<condensat>/<taille>.webp`.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import time
import urllib.parse
from collections.abc import Callable
from typing import Final

import httpx

from karria.modules.offers.application import FetchedImage, ProcessedLogo
from karria.modules.offers.domain.companies import (
    LOGO_SIZES,
    ImageTraits,
    icon_links,
    logo_object_key,
)
from karria.shared.images import ImageError, square_icon
from karria.shared.logging import get_logger
from karria.shared.storage import Bucket, ObjectStorage

logger = get_logger(__name__)

USER_AGENT: Final = "Mozilla/5.0 (compatible; KarriaLogoBot/1.0; +https://karria.fr)"
#: Une image de logo plus lourde n'en est pas un (ou c'est une photo).
MAX_IMAGE_BYTES: Final = 2 * 1024 * 1024
#: Ce qu'on lit d'une page d'accueil pour y trouver ses icônes (le `<head>`).
MAX_HTML_BYTES: Final = 512 * 1024
CONNECT_TIMEOUT: Final = 5.0
READ_TIMEOUT: Final = 10.0
MAX_REDIRECTS: Final = 4
#: Politesse : au plus deux requêtes en même temps vers un même site, et un
#: quart de seconde entre deux départs — un CDN ne le remarque pas, un petit
#: site non plus.
PER_HOST_CONCURRENCY: Final = 2
PER_HOST_INTERVAL: Final = 0.25
#: Statuts qui disent « réessaie plus tard ».
_TRANSIENT_STATUSES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})

Resolver = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:  # pragma: no cover - réseau réel
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def is_public_address(address: str) -> bool:
    """Une adresse IP joignable sur Internet (ni privée, ni locale, ni réservée)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


class _HostGate:
    """Concurrence et cadence par site."""

    def __init__(self, interval: float, concurrency: int) -> None:
        self._interval = interval
        self._concurrency = concurrency
        self._gates: dict[str, asyncio.Semaphore] = {}
        self._next: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def enter(self, host: str) -> asyncio.Semaphore:
        gate = self._gates.setdefault(host, asyncio.Semaphore(self._concurrency))
        await gate.acquire()
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next.get(host, now))
            self._next[host] = start + self._interval
        if start > now:
            await asyncio.sleep(start - now)
        return gate


class HttpLogoFetcher:
    """Implémente `LogoFetcher` avec httpx."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        resolver: Resolver = _resolve,
        interval: float = PER_HOST_INTERVAL,
        concurrency: int = PER_HOST_CONCURRENCY,
    ) -> None:
        self._client = client
        self._resolver = resolver
        self._hosts = _HostGate(interval, concurrency)

    @staticmethod
    def build_client() -> httpx.AsyncClient:
        """Le client partagé d'un passage (connexions réutilisées)."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.5"},
            follow_redirects=False,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    async def fetch_image(self, url: str) -> FetchedImage:
        """Télécharge ; `permanent` dit s'il est inutile de réessayer."""
        status, payload, _ = await self._get(url, MAX_IMAGE_BYTES, image=True)
        if payload is not None:
            return FetchedImage(payload=payload)
        return FetchedImage(payload=None, permanent=status not in _TRANSIENT_STATUSES)

    async def site_icons(self, domain: str) -> list[str]:
        """Les icônes déclarées par `https://<domaine>/` (après redirections)."""
        _, payload, final_url = await self._get(f"https://{domain}/", MAX_HTML_BYTES, image=False)
        if payload is None:
            return []
        return icon_links(payload.decode("utf-8", errors="replace"), final_url)

    async def _get(self, url: str, limit: int, *, image: bool) -> tuple[int, bytes | None, str]:
        """(statut, octets ou `None`, adresse finale). Statut 0 : refus local ou réseau."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            parts = urllib.parse.urlsplit(current)
            host = (parts.hostname or "").lower()
            if parts.scheme not in {"http", "https"} or not host:
                return 0, None, current
            if not await self._safe(host):
                logger.info("offers.logo_unsafe_host", host=host)
                return 0, None, current
            gate = await self._hosts.enter(host)
            try:
                status, payload, location = await self._once(current, limit, image=image)
            finally:
                gate.release()
            if location is None:
                return status, payload, current
            current = urllib.parse.urljoin(current, location)
        return 0, None, current

    async def _once(  # noqa: PLR0911 - une sortie par issue HTTP, lisible ainsi
        self, url: str, limit: int, *, image: bool
    ) -> tuple[int, bytes | None, str | None]:
        try:
            async with self._client.stream("GET", url) as response:
                if response.is_redirect and "location" in response.headers:
                    return response.status_code, None, response.headers["location"]
                if response.status_code != 200:  # noqa: PLR2004
                    return response.status_code, None, None
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if image and kind.startswith(("text/", "application/json")):
                    return 415, None, None
                declared = response.headers.get("content-length", "")
                if image and declared.isdigit() and int(declared) > limit:
                    return 413, None, None
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        if image:
                            return 413, None, None
                        break
                return 200, bytes(body[:limit]), None
        except httpx.TimeoutException:
            return 408, None, None
        except httpx.HTTPError:
            return 503, None, None

    async def _safe(self, host: str) -> bool:
        try:
            addresses = await asyncio.to_thread(self._resolver, host)
        except OSError:
            return False
        return bool(addresses) and all(is_public_address(a) for a in addresses)


class PillowLogoProcessor:
    """Implémente `LogoProcessor` sur `shared.images.square_icon`."""

    def process(self, payload: bytes) -> ProcessedLogo | None:
        """Carré, mesuré, décliné ; le condensat est celui de la plus grande taille."""
        try:
            icon = square_icon(payload, LOGO_SIZES)
        except ImageError:
            return None
        traits = ImageTraits(
            width=icon.width, height=icon.height, contrast=icon.contrast, coverage=icon.coverage
        )
        largest = icon.renditions.get(max(LOGO_SIZES), b"")
        digest = hashlib.sha256(largest).hexdigest()[:32]
        return ProcessedLogo(digest=digest, traits=traits, renditions=icon.renditions)


class StorageLogoStore:
    """Implémente `LogoStore` sur le bucket public `logos`."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    async def has(self, digest: str) -> bool:
        """La plus grande taille, écrite en dernier, fait foi."""
        return await self._storage.exists(Bucket.LOGOS, logo_object_key(digest, max(LOGO_SIZES)))

    async def put(self, logo: ProcessedLogo) -> None:
        """Les petites tailles d'abord : la présence de la grande dit que tout est là."""
        for size in sorted(logo.renditions):
            await self._storage.put(
                Bucket.LOGOS,
                logo_object_key(logo.digest, size),
                logo.renditions[size],
                content_type="image/webp",
            )
