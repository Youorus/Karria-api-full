"""Un corps de requête trop gros est refusé avant d'être lu (audit de sécurité du 2026-11-20)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from karria.shared.http.middlewares import BodySizeLimitMiddleware

pytestmark = pytest.mark.unit

Message = dict[str, Any]


async def echo_app(scope: Message, receive: Any, send: Any) -> None:
    """Lit tout le corps, comme FastAPI, et répond sa taille."""
    size = 0
    while True:
        message = await receive()
        size += len(message.get("body", b""))
        if not message.get("more_body"):
            break
    body = json.dumps({"size": size}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


async def call(
    *,
    chunks: list[bytes],
    headers: dict[str, str],
    path: str = "/api/v1/auth/email/start",
    app: Any = echo_app,
    max_bytes: int = 1000,
    max_json_bytes: int = 100,
) -> tuple[int, bytes, int]:
    """(statut, corps de la réponse, morceaux réellement lus par l'application)."""
    pending = list(chunks)
    read = 0

    async def receive() -> Message:
        nonlocal read
        read += 1
        chunk = pending.pop(0) if pending else b""
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    middleware = BodySizeLimitMiddleware(app, max_bytes=max_bytes, max_json_bytes=max_json_bytes)
    await middleware(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body, read


JSON = {"content-type": "application/json"}


class TestPlafond:
    async def test_un_corps_ordinaire_passe(self) -> None:
        status, body, _ = await call(chunks=[b"x" * 80], headers={**JSON, "content-length": "80"})
        assert status == 200
        assert json.loads(body) == {"size": 80}

    async def test_un_corps_annonce_trop_gros_nest_jamais_lu(self) -> None:
        status, body, read = await call(
            chunks=[b"x" * 5000], headers={**JSON, "content-length": "5000"}
        )
        assert status == 413
        assert read == 0
        assert json.loads(body)["error"]["details"]["reason"] == "body_too_large"

    async def test_un_envoi_par_morceaux_sans_longueur_est_coupe_en_route(self) -> None:
        status, _, read = await call(chunks=[b"x" * 60] * 50, headers=JSON)
        assert status == 413
        assert read == 2

    async def test_une_longueur_mensongere_ne_trompe_pas_le_compte(self) -> None:
        status, _, _ = await call(chunks=[b"x" * 60] * 5, headers={**JSON, "content-length": "10"})
        assert status == 413

    async def test_un_televersement_a_le_plafond_general(self) -> None:
        upload = {"content-type": "multipart/form-data; boundary=x"}
        status, body, _ = await call(chunks=[b"x" * 500], headers=upload)
        assert status == 200
        assert json.loads(body) == {"size": 500}
        status, _, _ = await call(chunks=[b"x" * 600] * 2, headers=upload)
        assert status == 413

    async def test_les_lots_du_collecteur_gardent_le_plafond_general(self) -> None:
        status, _, _ = await call(
            chunks=[b"x" * 500], headers=JSON, path="/api/v1/internal/offers/batches"
        )
        assert status == 200

    async def test_sans_type_de_contenu_cest_le_plafond_json(self) -> None:
        status, _, _ = await call(chunks=[b"x" * 500], headers={})
        assert status == 413

    async def test_une_reponse_deja_commencee_nest_pas_doublee(self) -> None:
        async def early(scope: Message, receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await receive()
            await receive()

        status, _, _ = await call(chunks=[b"x" * 60] * 5, headers=JSON, app=early)
        assert status == 202

    async def test_ce_qui_nest_pas_http_passe_tel_quel(self) -> None:
        seen: list[str] = []

        async def app(scope: Message, receive: Any, send: Any) -> None:
            seen.append(scope["type"])

        await BodySizeLimitMiddleware(app)({"type": "websocket"}, None, None)  # type: ignore[arg-type]
        assert seen == ["websocket"]
