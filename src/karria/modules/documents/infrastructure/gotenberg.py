"""Client Gotenberg : HTML → PDF (Chromium), DOCX → PDF (LibreOffice).

Contrat
-------
- Gotenberg 8.38.1, auto-hébergé dans la composition `karria-donnees`, réseau
  interne seulement (ADR-0020 § 9). Aucune donnée ne sort du serveur.
- Les polices et les images partent **avec chaque requête** : l'image
  officielle suffit, sans polices installées. Le DOCX porte ses polices
  incorporées, que LibreOffice lit.
- Chromium n'imprime qu'après l'ajustement de la page (`window.fitDone`).
- Une panne, un délai dépassé ou un 5xx lèvent `RenderingUnavailableError`
  (503 `rendering_unavailable`, se retente).
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request
import uuid
from typing import Final

from karria.modules.documents.domain.errors import RenderingUnavailableError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

CHROMIUM_HTML: Final = "/forms/chromium/convert/html"
LIBREOFFICE: Final = "/forms/libreoffice/convert"


def multipart(files: dict[str, bytes], fields: dict[str, str]) -> tuple[bytes, str]:
    """Un corps `multipart/form-data` (champs puis fichiers) et son type."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    for name, data in files.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class GotenbergClient:
    """Les deux conversions dont les modèles de CV ont besoin."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def html_to_pdf(self, html: str, assets: dict[str, bytes]) -> bytes:
        """Imprime une page HTML (et ses fichiers) en PDF, sans marges, fonds compris."""
        fields = {
            "preferCssPageSize": "true",
            "printBackground": "true",
            "marginTop": "0",
            "marginBottom": "0",
            "marginLeft": "0",
            "marginRight": "0",
            "skipNetworkIdleEvent": "true",
            "waitForExpression": "window.fitDone === true",
        }
        return await self._post(
            CHROMIUM_HTML, {"index.html": html.encode("utf-8"), **assets}, fields
        )

    async def docx_to_pdf(self, docx: bytes) -> bytes:
        """Convertit un DOCX en PDF (LibreOffice) — pour mesurer et prévisualiser le DOCX."""
        return await self._post(LIBREOFFICE, {"cv.docx": docx}, {})

    async def _post(self, path: str, files: dict[str, bytes], fields: dict[str, str]) -> bytes:
        body, content_type = multipart(files, fields)
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(self._send, path, body, content_type)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("documents.rendering_failed", path=path, error=str(exc)[:300])
            raise RenderingUnavailableError from exc
        logger.info(
            "documents.rendered",
            path=path,
            ms=round((time.monotonic() - started) * 1000),
            bytes=len(data),
        )
        return data

    def _send(self, path: str, body: bytes, content_type: str) -> bytes:
        request = urllib.request.Request(  # noqa: S310 - URL interne de configuration
            self._base + path, data=body, headers={"Content-Type": content_type}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            payload: bytes = response.read()
            return payload
