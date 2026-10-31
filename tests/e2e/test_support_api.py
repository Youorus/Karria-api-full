"""Parcours HTTP complet de « Parler à l'équipe » : écrire, lire, répondre, le direct."""

from __future__ import annotations

import time
import warnings
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

with warnings.catch_warnings():
    # Starlette 1.x préfère `httpx2` pour son client de test ; le nôtre ne sert
    # qu'au WebSocket, et `httpx` fait l'affaire. L'avertissement est émis à
    # l'import, hors de portée d'un `filterwarnings` de test.
    from starlette.exceptions import StarletteDeprecationWarning

    warnings.simplefilter("ignore", StarletteDeprecationWarning)
    from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from doubles import SupportEnv, sample_image
from karria.shared.auth import Principal, get_token_resolver
from karria.shared.config import AppSettings, Environment, Settings, SupportSettings, get_settings

pytestmark = pytest.mark.e2e

API = "/api/v1"
TEAM = {"Authorization": "Bearer jeton-equipe"}


@pytest.fixture
def team_token(app: FastAPI) -> None:
    """Pose le jeton d'équipe dans la configuration de l'application de test."""
    settings = Settings(
        app=AppSettings(environment=Environment.TEST, debug=True),
        support=SupportSettings(admin_token="jeton-equipe"),  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_settings] = lambda: settings


class TestUserSide:
    async def test_le_fil_s_ouvre_vide_puis_garde_les_messages(
        self, client: AsyncClient, authenticate: Any, support_env: SupportEnv
    ) -> None:
        authenticate()
        first = await client.get(f"{API}/support/me")
        assert first.status_code == 200
        assert first.json()["messages"] == []
        assert first.json()["has_unread"] is False

        posted = await client.post(f"{API}/support/me/messages", json={"body": "Bonjour l'équipe"})
        assert posted.status_code == 201
        assert posted.json()["author"] == "user"
        assert posted.json()["kind"] == "text"
        assert posted.json()["attachment"] is None

        again = await client.get(f"{API}/support/me")
        assert [m["body"] for m in again.json()["messages"]] == ["Bonjour l'équipe"]
        assert len(support_env.alerter.alerts) == 1

    async def test_exige_la_session(self, client: AsyncClient) -> None:
        assert (await client.get(f"{API}/support/me")).status_code == 401
        assert (await client.get(f"{API}/support/me/unread")).status_code == 401
        assert (await client.post(f"{API}/support/me/read")).status_code == 401

    async def test_un_texte_vide_est_un_422(
        self, client: AsyncClient, authenticate: Any, support_env: SupportEnv
    ) -> None:
        authenticate()
        response = await client.post(f"{API}/support/me/messages", json={"body": "   "})
        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "support_message_empty"

    async def test_une_image_et_un_vocal_rendent_une_url_signee(
        self, client: AsyncClient, authenticate: Any, support_env: SupportEnv
    ) -> None:
        authenticate()
        image = await client.post(
            f"{API}/support/me/messages/media",
            files={"file": ("capture.png", sample_image(fmt="PNG"), "image/png")},
        )
        assert image.status_code == 201
        assert image.json()["kind"] == "image"
        assert image.json()["attachment"]["url"].startswith("https://storage.local/")
        assert image.json()["attachment"]["duration_ms"] is None

        voice = await client.post(
            f"{API}/support/me/messages/media",
            files={"file": ("vocal.m4a", b"\x00" * 1024, "audio/m4a")},
            data={"duration_ms": "4200"},
        )
        assert voice.status_code == 201
        assert voice.json()["kind"] == "voice"
        assert voice.json()["attachment"]["duration_ms"] == 4200

    async def test_un_fichier_inconnu_est_un_422(
        self, client: AsyncClient, authenticate: Any, support_env: SupportEnv
    ) -> None:
        authenticate()
        response = await client.post(
            f"{API}/support/me/messages/media",
            files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "support_media_unsupported"

    async def test_le_non_lu_suit_la_reponse_puis_la_lecture(
        self, client: AsyncClient, authenticate: Any, support_env: SupportEnv, team_token: None
    ) -> None:
        me = authenticate()
        posted = await client.post(f"{API}/support/me/messages", json={"body": "Bonjour"})
        conversation_id = (await client.get(f"{API}/support/me")).json()["id"]
        _ = posted
        support_env.clock.advance(timedelta(minutes=5))
        reply = await client.post(
            f"{API}/internal/support/conversations/{conversation_id}/messages",
            json={"body": "On regarde."},
            headers=TEAM,
        )
        assert reply.status_code == 201
        assert (await client.get(f"{API}/support/me/unread")).json() == {"has_unread": True}
        assert [u for u, _ in support_env.notifier.sent] == [me.subject_id]

        assert (await client.post(f"{API}/support/me/read")).status_code == 204
        assert (await client.get(f"{API}/support/me/unread")).json() == {"has_unread": False}


class TestTeamSide:
    async def test_les_routes_internes_exigent_le_jeton(
        self, client: AsyncClient, support_env: SupportEnv, team_token: None
    ) -> None:
        assert (await client.get(f"{API}/internal/support/conversations")).status_code == 401
        bad = await client.get(
            f"{API}/internal/support/conversations", headers={"Authorization": "Bearer faux"}
        )
        assert bad.status_code == 401
        assert bad.json()["error"]["details"]["reason"] == "admin_token_invalid"

    async def test_sans_jeton_configure_tout_est_ferme(
        self, client: AsyncClient, support_env: SupportEnv
    ) -> None:
        response = await client.get(f"{API}/internal/support/conversations", headers=TEAM)
        assert response.status_code == 401

    async def test_la_liste_le_detail_et_la_reponse(
        self, client: AsyncClient, authenticate: Any, support_env: SupportEnv, team_token: None
    ) -> None:
        authenticate()
        await client.post(f"{API}/support/me/messages", json={"body": "Une question"})

        liste = await client.get(
            f"{API}/internal/support/conversations?awaiting_only=true", headers=TEAM
        )
        assert liste.status_code == 200
        assert liste.json()["total"] == 1
        ligne = liste.json()["items"][0]
        assert ligne["user_email"] == "awa@exemple.fr"
        assert ligne["awaits_team"] is True
        assert ligne["last_message_preview"] == "Une question"

        detail = await client.get(
            f"{API}/internal/support/conversations/{ligne['id']}", headers=TEAM
        )
        assert detail.status_code == 200
        assert [m["body"] for m in detail.json()["messages"]] == ["Une question"]

        image = await client.post(
            f"{API}/internal/support/conversations/{ligne['id']}/messages/media",
            files={"file": ("rep.jpg", sample_image(), "image/jpeg")},
            headers=TEAM,
        )
        assert image.status_code == 201
        assert image.json()["author"] == "team"

        plus_rien = await client.get(
            f"{API}/internal/support/conversations?awaiting_only=true", headers=TEAM
        )
        assert plus_rien.json()["total"] == 0

    async def test_un_fil_inconnu_est_un_404(
        self, client: AsyncClient, support_env: SupportEnv, team_token: None
    ) -> None:
        response = await client.get(f"{API}/internal/support/conversations/{uuid4()}", headers=TEAM)
        assert response.status_code == 404

    async def test_la_console_est_une_page_html_publique_qui_ne_sait_rien(
        self, client: AsyncClient
    ) -> None:
        response = await client.get(f"{API}/internal/support/console")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "SUPPORT__ADMIN_TOKEN" in response.text
        assert "jeton-equipe" not in response.text


class TestStream:
    """Le flux WebSocket, exercé par le client de test synchrone de Starlette."""

    @pytest.fixture
    def ws_client(self, app: FastAPI, support_env: SupportEnv) -> TestClient:
        principal = Principal(subject_id=uuid4())

        class Resolver:
            async def resolve(self, token: str) -> Principal | None:
                return principal if token == "bon" else None  # noqa: S105 - jeton factice

        def resolver() -> Resolver:
            return Resolver()

        app.dependency_overrides[get_token_resolver] = resolver
        app.state.principal = principal
        return TestClient(app)

    def test_refuse_un_jeton_inconnu(self, ws_client: TestClient) -> None:
        """Accepté puis fermé en 4401 : refusé avant, le client ne verrait qu'un HTTP 403."""
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            ws_client.websocket_connect(
                f"{API}/support/me/stream", headers={"Authorization": "Bearer mauvais"}
            ) as ws,
        ):
            ws.receive_text()
        assert exc.value.code == 4401

    def test_une_reponse_de_l_equipe_arrive_en_direct_et_le_read_pose_la_lecture(
        self, app: FastAPI, ws_client: TestClient, support_env: SupportEnv, team_token: None
    ) -> None:
        with ws_client.websocket_connect(
            f"{API}/support/me/stream", headers={"Authorization": "Bearer bon"}
        ) as ws:
            conversation = next(iter(support_env.conversations.rows.values()))
            support_env.clock.advance(timedelta(seconds=1))
            reply = ws_client.post(
                f"{API}/internal/support/conversations/{conversation.id}/messages",
                json={"body": "Je te réponds en direct."},
                headers=TEAM,
            )
            assert reply.status_code == 201

            event = ws.receive_json()
            assert event["type"] == "message"
            assert event["message"]["author"] == "team"
            assert event["message"]["body"] == "Je te réponds en direct."

            ws.send_json({"type": "read"})
            # Le `read` est traité par la tâche d'écoute, dans le fil du serveur :
            # on lui laisse jusqu'à une seconde.
            for _ in range(100):
                if support_env.conversations.rows[conversation.id].user_read_at is not None:
                    break
                time.sleep(0.01)
            updated = support_env.conversations.rows[conversation.id]
            assert updated.user_read_at is not None
            assert not updated.has_unread_for_user
