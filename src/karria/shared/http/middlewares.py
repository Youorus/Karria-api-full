"""Middlewares ASGI transverses.

Choix technique
---------------
Ces middlewares sont écrits en **ASGI pur** plutôt qu'avec
`BaseHTTPMiddleware` de Starlette. `BaseHTTPMiddleware` enveloppe chaque
requête dans une tâche supplémentaire, ce qui casse la propagation des
`contextvars` vers les tâches de fond et perturbe le streaming — deux
comportements dont dépend précisément la corrélation des logs. Le surcoût
est d'une vingtaine de lignes, payées une fois.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders

from karria.shared.logging import (
    CORRELATION_ID_HEADER,
    bind_correlation_id,
    clear_context,
    get_logger,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger(__name__)

#: Chemins exclus du journal d'accès. Une sonde de santé interrogée toutes
#: les dix secondes produirait 8 640 lignes par jour et par instance, sans
#: aucune valeur de diagnostic.
_SILENT_PATHS: Final = frozenset({"/health", "/health/live", "/health/ready", "/metrics"})

_STATUS_SERVER_ERROR: Final = 500
_STATUS_CLIENT_ERROR: Final = 400


class RequestContextMiddleware:
    """Attribue un identifiant de corrélation et journalise chaque requête.

    L'identifiant est repris de l'en-tête entrant s'il existe — ce qui
    permet à une passerelle ou à un service appelant de propager sa propre
    trace — sinon il est généré. Il est systématiquement renvoyé au client.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(CORRELATION_ID_HEADER)
        correlation_id = incoming or uuid4().hex
        bind_correlation_id(correlation_id)

        started_at = time.perf_counter()
        # Valeur par défaut volontaire : si l'application s'effondre sans
        # jamais émettre de réponse, le journal doit refléter un échec.
        response_status = _STATUS_SERVER_ERROR

        async def send_with_correlation_id(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                MutableHeaders(scope=message)[CORRELATION_ID_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation_id)
        finally:
            path = scope.get("path", "")
            if path not in _SILENT_PATHS:
                self._log_access(
                    method=scope.get("method", ""),
                    path=path,
                    status=response_status,
                    duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                )
            clear_context()

    @staticmethod
    def _log_access(*, method: str, path: str, status: int, duration_ms: float) -> None:
        if status >= _STATUS_SERVER_ERROR:
            level = logger.error
        elif status >= _STATUS_CLIENT_ERROR:
            level = logger.warning
        else:
            level = logger.info
        level("http.access", method=method, path=path, status=status, duration_ms=duration_ms)


class SecurityHeadersMiddleware:
    """Applique les en-têtes de sécurité à toutes les réponses.

    Appliqués globalement et non route par route : une protection que l'on
    peut oublier d'activer n'est pas une protection.
    """

    _HEADERS: Final = {
        # Empêche le navigateur de deviner un type MIME (vecteur de XSS).
        "X-Content-Type-Options": "nosniff",
        # Interdit l'affichage en iframe (clickjacking).
        "X-Frame-Options": "DENY",
        # Ne divulgue pas l'URL complète aux domaines tiers.
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # Une API JSON n'a besoin d'aucune de ces capacités navigateur.
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        # HSTS uniquement derrière HTTPS : activé en local, il rendrait le
        # domaine inaccessible en HTTP pour la durée du max-age.
        self._headers = dict(self._HEADERS)
        if hsts:
            self._headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._headers.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


#: Corps maximal d'une requête, tous types confondus : au-dessus du plus gros
#: téléversement légitime (25 Mio, `shared/storage/uploads.py`).
MAX_BODY_BYTES: Final = 26 * 1024 * 1024
#: Corps maximal partout ailleurs que sur les routes de téléversement : le plus
#: gros corps légitime y est un CV (256 Kio).
MAX_JSON_BODY_BYTES: Final = 2 * 1024 * 1024
#: Les routes qui reçoivent des fichiers (ou les lots du collecteur) ont le plafond
#: général. Le choix se fait sur le **chemin**, jamais sur le `Content-Type`
#: annoncé : un client qui se déclarait « multipart » sur une route JSON obtenait
#: le grand plafond (revue de sécurité du 2026-11-20).
UPLOAD_PATH_PREFIXES: Final = (
    "/api/v1/cv/photo",
    "/api/v1/cv/import",
    "/api/v1/voice/",
    "/api/v1/support/",
    "/api/v1/internal/",
)
_TOO_LARGE_BODY: Final = (
    b'{"error":{"code":"invalid_input","message":"La requ\\u00eate est trop volumineuse.",'
    b'"details":{"reason":"body_too_large"}}}'
)


class _BodyTooLargeError(Exception):
    """Le corps reçu dépasse le plafond (levée dans `receive`, rattrapée par le middleware)."""


class BodySizeLimitMiddleware:
    """Refuse un corps de requête trop gros, **avant** l'authentification et avant de le lire.

    Sans lui, n'importe qui pouvait envoyer un JSON de plusieurs gigaoctets sur
    une route publique : FastAPI le lisait en mémoire avant toute garde (audit de
    sécurité du 2026-11-20). Deux gardes : l'en-tête `Content-Length` annoncé, et
    le compte des octets réellement reçus (un envoi par morceaux n'annonce rien).
    Les lectures bornées des routes (`read(N + 1)`) restent, plus strictes.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int = MAX_BODY_BYTES,
        max_json_bytes: int = MAX_JSON_BODY_BYTES,
    ) -> None:
        self.app = app
        self._max_bytes = max_bytes
        self._max_json_bytes = max_json_bytes

    def _limit(self, scope: Scope) -> int:
        if str(scope.get("path", "")).startswith(UPLOAD_PATH_PREFIXES):
            return self._max_bytes
        return min(self._max_json_bytes, self._max_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope)
        announced = Headers(scope=scope).get("content-length", "")
        if announced.isdigit() and int(announced) > limit:
            await self._refuse(send)
            return
        received = 0
        started = False

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracking_send)
        except _BodyTooLargeError:
            logger.warning("http.body_too_large", path=scope.get("path"), limit=limit)
            if not started:
                await self._refuse(send)

    @staticmethod
    async def _refuse(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})
