"""Le client de l'API, côté collecteur : louer des cibles, remettre des lots.

La bibliothèque standard suffit (deux routes) ; l'appel part dans un fil.
Une panne de l'API se reprend trois fois, puis lève : le passage s'arrête,
la cible louée redeviendra due d'elle-même à la fin du bail.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Final

from karria.collector.contract import HANDOFF_VERSION
from karria.collector.sources import Target

ATTEMPTS: Final = 3
_RETRYABLE: Final = frozenset({429, 500, 502, 503, 504})


class ApiError(Exception):
    """L'API a refusé, ou n'a pas répondu après les reprises."""

    def __init__(self, status: int | None, detail: str) -> None:
        super().__init__(f"API {status}: {detail}")
        self.status = status


class CollectorApi:
    """Les deux routes internes."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds

    async def lease_targets(self, limit: int) -> list[Target]:
        """`GET /internal/offers/targets`."""
        body = await self._call("GET", f"/internal/offers/targets?limit={limit}", None, {})
        payload = json.loads(body or b"{}")
        return [
            Target(
                key=str(t["key"]),
                search_term=str(t["search_term"]),
                location=str(t["location"]),
                distance_km=int(t["distance_km"]) if t.get("distance_km") else None,
                last_found=int(t.get("last_found") or 0),
            )
            for t in payload.get("targets", [])
        ]

    async def push_batch(self, payload: bytes, *, target_key: str | None) -> str:
        """`POST /internal/offers/batches` ; rend la clé du lot atterri."""
        headers = {"Content-Type": "application/gzip", "X-Handoff-Version": str(HANDOFF_VERSION)}
        if target_key:
            headers["X-Target-Key"] = target_key
        body = await self._call("POST", "/internal/offers/batches", payload, headers)
        return str(json.loads(body)["key"])

    async def _call(
        self, method: str, path: str, data: bytes | None, headers: dict[str, str]
    ) -> bytes:
        last: ApiError | None = None
        for attempt in range(ATTEMPTS):
            try:
                return await asyncio.to_thread(self._send, method, path, data, headers)
            except ApiError as exc:
                last = exc
                if exc.status is not None and exc.status not in _RETRYABLE:
                    raise
                await asyncio.sleep(2**attempt)
        assert last is not None  # noqa: S101 - la boucle a tourné au moins une fois
        raise last

    def _send(self, method: str, path: str, data: bytes | None, headers: dict[str, str]) -> bytes:
        request = urllib.request.Request(  # noqa: S310 - URL de l'API, issue de la configuration
            self._base + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self._token}", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body: bytes = response.read()
                return body
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, exc.read()[:300].decode("utf-8", "replace")) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(None, str(exc)) from exc
