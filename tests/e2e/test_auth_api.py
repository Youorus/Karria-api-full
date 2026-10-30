"""Parcours HTTP complet de l'authentification.

Traverse toute la pile — validation, routage, gestionnaires d'erreurs,
couture `resolve_principal` branchée par `bootstrap/wiring.py` — sur des
dépôts en mémoire. Les jetons rendus sont de **vrais** jetons : une route
protégée appelée avec le `access_token` obtenu ici est authentifiée par le
même chemin qu'en production.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from doubles import CollectingBus, IdentityEnv, StubSocialVerifier
from karria.modules.identity.api import dependencies as deps
from karria.modules.identity.application import AccountDeleted, MainDeviceSnapshot, SocialLogin
from karria.modules.identity.domain import (
    REFRESH_REUSE_GRACE,
    SocialProvider,
    VerifiedSocialProfile,
)
from karria.shared.config import AppSettings, Environment, Settings, SocialSettings

pytestmark = pytest.mark.e2e

CONSENT = {"terms_version": "2026-10", "privacy_policy_version": "2026-10"}
EMAIL = "marc@exemple.fr"


async def _login(client: AsyncClient, env: IdentityEnv, email: str = EMAIL) -> dict[str, Any]:
    started = await client.post("/api/v1/auth/email/start", json={"email": email, **CONSENT})
    assert started.status_code == 200, started.text
    verified = await client.post(
        "/api/v1/auth/email/verify",
        json={
            "challenge_id": started.json()["challenge_id"],
            "code": env.sender.last_code_for(email),
        },
    )
    assert verified.status_code == 200, verified.text
    body: dict[str, Any] = verified.json()
    return body


class TestEmailLogin:
    async def test_le_parcours_complet_cree_le_compte_et_connecte(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        started = await client.post(
            "/api/v1/auth/email/start",
            json={"email": "Marc@Exemple.fr", **CONSENT},
            headers={"Accept-Language": "en-GB,en;q=0.9"},
        )

        assert started.status_code == 200
        body = started.json()
        assert body["sent_to"] == "m•••@exemple.fr"
        assert body["expires_in"] == 600
        assert body["seconds_before_resend"] == 30
        assert identity_env.sender.sent[0][2].value == "en"

        verified = await client.post(
            "/api/v1/auth/email/verify",
            json={
                "challenge_id": body["challenge_id"],
                "code": identity_env.sender.last_code_for(EMAIL),
            },
        )

        assert verified.status_code == 200
        login = verified.json()
        assert set(login) == {"session", "user", "created_account"}
        assert set(login["session"]) == {"access_token", "refresh_token"}
        assert login["user"]["email"] == EMAIL
        assert login["created_account"] is True

        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login['session']['access_token']}"},
        )
        assert me.status_code == 200
        assert {k: me.json()[k] for k in ("id", "email")} == login["user"]
        # ADR-0025 : `/auth/me` dit où en est le consentement à l'IA.
        assert me.json()["ai_consent"]["required"] is True
        assert me.json()["ai_consent"]["disclosure"]["providers"] == [
            {"id": "gemini", "name": "Google Gemini"}
        ]

    async def test_une_adresse_invalide_repond_422_sous_le_champ(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/auth/email/start", json={"email": "nope", **CONSENT})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_input"
        assert error["details"] == {"reason": "user_email_invalid", "field": "email"}

    async def test_sans_consentement_la_requete_est_invalide(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/auth/email/start", json={"email": EMAIL})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"

    async def test_un_code_faux_puis_brule(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        started = await client.post("/api/v1/auth/email/start", json={"email": EMAIL, **CONSENT})
        challenge_id = started.json()["challenge_id"]

        for remaining in (4, 3, 2, 1):
            wrong = await client.post(
                "/api/v1/auth/email/verify", json={"challenge_id": challenge_id, "code": "000000"}
            )
            assert wrong.status_code == 422
            assert wrong.json()["error"]["details"] == {
                "reason": "auth_code_invalid",
                "field": "code",
                "remaining_attempts": remaining,
            }

        burnt = await client.post(
            "/api/v1/auth/email/verify", json={"challenge_id": challenge_id, "code": "000000"}
        )
        assert burnt.status_code == 429
        assert burnt.json()["error"]["details"]["reason"] == "auth_code_exhausted"

        expired = await client.post(
            "/api/v1/auth/email/verify",
            json={"challenge_id": challenge_id, "code": identity_env.sender.last_code_for(EMAIL)},
        )
        assert expired.status_code == 410
        assert expired.json()["error"]["details"]["reason"] == "auth_login_challenge_expired"

    async def test_le_renvoi_respecte_le_delai(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        started = await client.post("/api/v1/auth/email/start", json={"email": EMAIL, **CONSENT})
        challenge_id = started.json()["challenge_id"]

        too_soon = await client.post(
            "/api/v1/auth/email/resend", json={"challenge_id": challenge_id}
        )
        assert too_soon.status_code == 429
        assert too_soon.json()["error"]["details"]["reason"] == "auth_login_code_resend_too_soon"
        assert too_soon.json()["error"]["details"]["retry_after"] == 30

        identity_env.clock.advance(timedelta(seconds=30))
        resent = await client.post("/api/v1/auth/email/resend", json={"challenge_id": challenge_id})
        assert resent.status_code == 200
        assert resent.json()["challenge_id"] == challenge_id
        assert len(identity_env.sender.sent) == 2

        unknown = await client.post(
            "/api/v1/auth/email/resend", json={"challenge_id": str(uuid4())}
        )
        assert unknown.status_code == 410

    async def test_le_limiteur_repond_429_avec_retry_after(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        identity_env.throttle.blocked.add(f"{EMAIL}|127.0.0.1")
        response = await client.post("/api/v1/auth/email/start", json={"email": EMAIL, **CONSENT})
        assert response.status_code == 429
        assert response.json()["error"]["details"] == {
            "reason": "auth_too_many_attempts",
            "retry_after": 42,
        }

    async def test_la_cle_de_limitation_prend_lorigine_derriere_le_mandataire(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        identity_env.throttle.blocked.add(f"{EMAIL}|203.0.113.9")
        response = await client.post(
            "/api/v1/auth/email/start",
            json={"email": EMAIL, **CONSENT},
            headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
        )
        assert response.status_code == 429


class TestSession:
    async def test_le_rafraichissement_tourne_et_lancien_acces_meurt(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        login = await _login(client, identity_env)
        tokens = login["session"]

        refreshed = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert refreshed.status_code == 200
        new = refreshed.json()
        assert set(new) == {"access_token", "refresh_token"}
        assert new["access_token"] != tokens["access_token"]
        old_me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert old_me.status_code == 401
        new_me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"}
        )
        assert new_me.status_code == 200

    async def test_le_rejeu_tardif_ferme_tout_et_repond_401(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        tokens = (await _login(client, identity_env))["session"]
        refreshed = (
            await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
        ).json()
        identity_env.clock.advance(REFRESH_REUSE_GRACE + timedelta(seconds=1))

        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401
        assert replay.json()["error"]["details"]["reason"] == "auth_session_not_found"

        victim = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refreshed["refresh_token"]}
        )
        assert victim.status_code == 401

    async def test_la_deconnexion_repond_204_et_invalide_la_session(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        tokens = (await _login(client, identity_env))["session"]

        logout = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert logout.status_code == 204

        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert me.status_code == 401
        again = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert again.status_code == 204

    @pytest.mark.parametrize(
        "header",
        [
            {},
            {"Authorization": "Basic abc"},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer inconnu"},
        ],
    )
    async def test_sans_jeton_valide_me_repond_401(
        self, client: AsyncClient, identity_env: IdentityEnv, header: dict[str, str]
    ) -> None:
        _ = identity_env
        response = await client.get("/api/v1/auth/me", headers=header)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    async def test_un_compte_disparu_repond_401(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        login = await _login(client, identity_env)
        identity_env.users.rows.clear()
        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login['session']['access_token']}"},
        )
        assert me.status_code == 401
        assert me.json()["error"]["details"]["reason"] == "user_not_found"


GOOGLE_PROFILE = VerifiedSocialProfile(
    provider=SocialProvider.GOOGLE, subject="g-1", email=EMAIL, email_verified=True
)


def _override_social(
    app: FastAPI, env: IdentityEnv, provider: SocialProvider, profile: VerifiedSocialProfile | None
) -> StubSocialVerifier:
    verifier = StubSocialVerifier(provider, profile)

    def factory() -> SocialLogin:
        return SocialLogin(
            verifier=verifier,
            identities=env.identities,
            users=env.users,
            sessions=env.sessions,
            tokens=env.tokens,
            clock=env.clock,
            uow=env.uow,
        )

    target = deps.get_google_login if provider is SocialProvider.GOOGLE else deps.get_apple_login
    app.dependency_overrides[target] = factory
    return verifier


class TestSocialLogin:
    async def test_google_connecte_et_rattache_a_un_compte_ne_par_code(
        self, app: FastAPI, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        by_code = await _login(client, identity_env)
        verifier = _override_social(app, identity_env, SocialProvider.GOOGLE, GOOGLE_PROFILE)

        response = await client.post(
            "/api/v1/auth/social/google", json={"id_token": "jeton", "nonce": "n", **CONSENT}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["created_account"] is False
        assert body["user"]["id"] == by_code["user"]["id"]
        assert verifier.calls == [("jeton", "n")]

    async def test_apple_cree_le_compte_avec_nonce_facultatif(
        self, app: FastAPI, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        profile = VerifiedSocialProfile(
            provider=SocialProvider.APPLE,
            subject="a-1",
            email="x@privaterelay.appleid.com",
            email_verified=True,
        )
        verifier = _override_social(app, identity_env, SocialProvider.APPLE, profile)

        response = await client.post(
            "/api/v1/auth/social/apple", json={"identity_token": "jeton", "nonce": None, **CONSENT}
        )

        assert response.status_code == 200
        assert response.json()["created_account"] is True
        assert verifier.calls == [("jeton", None)]

    async def test_un_jeton_refuse_repond_401(
        self, app: FastAPI, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        _override_social(app, identity_env, SocialProvider.GOOGLE, None)
        response = await client.post(
            "/api/v1/auth/social/google", json={"id_token": "forge", **CONSENT}
        )
        assert response.status_code == 401
        assert response.json()["error"]["details"]["reason"] == "auth_social_token_invalid"

    async def test_une_adresse_non_attestee_repond_409(
        self, app: FastAPI, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        profile = VerifiedSocialProfile(
            provider=SocialProvider.GOOGLE, subject="g", email="x@y.fr", email_verified=False
        )
        _override_social(app, identity_env, SocialProvider.GOOGLE, profile)
        response = await client.post(
            "/api/v1/auth/social/google", json={"id_token": "t", **CONSENT}
        )
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "auth_social_email_unverified"

    async def test_google_non_configure_repond_503(
        self, settings_without_google: FastAPI, identity_env: IdentityEnv
    ) -> None:
        _ = identity_env
        from httpx import ASGITransport  # noqa: PLC0415

        async with AsyncClient(
            transport=ASGITransport(app=settings_without_google), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/auth/social/google", json={"id_token": "t", **CONSENT}
            )
        assert response.status_code == 503
        assert response.json()["error"]["details"]["reason"] == "auth_social_provider_unavailable"


@pytest.fixture
def settings_without_google(events: Any, storage: Any) -> FastAPI:
    """Une application dont la configuration ne déclare aucune audience Google."""
    from karria.bootstrap.app import create_app  # noqa: PLC0415

    settings = Settings(
        app=AppSettings(environment=Environment.TEST),
        social=SocialSettings(
            google_ios_client_id="", google_web_client_id="", google_android_client_id=""
        ),
    )
    application = create_app(settings)
    application.state.event_bus = events
    application.state.object_storage = storage
    from karria.shared.config import get_settings  # noqa: PLC0415

    application.dependency_overrides[get_settings] = lambda: settings
    return application


class TestDeleteAccount:
    BODY: ClassVar[dict[str, Any]] = {
        "reason": "found_job",
        "details": "  J'ai trouvé, merci. Écrivez-moi : moi@exemple.fr  ",
    }

    async def test_le_compte_part_la_session_tombe_et_la_trace_est_anonyme(
        self, client: AsyncClient, identity_env: IdentityEnv, events: CollectingBus
    ) -> None:
        login = await _login(client, identity_env)
        tokens = login["session"]
        identity_env.main_device.snapshot = MainDeviceSnapshot(
            platform="android", app_version="2.3.0"
        )

        deleted = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            json=self.BODY,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

        assert deleted.status_code == 204, deleted.text
        assert deleted.content == b""

        me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert me.status_code == 401
        refreshed = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refreshed.status_code == 401

        [record] = identity_env.departures.rows
        assert record.reason.value == "found_job"
        assert record.details == "J'ai trouvé, merci. Écrivez-moi : [e-mail]"
        assert (record.platform, record.app_version) == ("android", "2.3.0")
        [event] = events.of_type(AccountDeleted)
        assert str(event.user_id) == login["user"]["id"]

        # La même adresse revient : c'est une première venue.
        again = await _login(client, identity_env)
        assert again["created_account"] is True
        assert again["user"]["id"] != login["user"]["id"]

    async def test_sans_session_la_route_refuse(self, client: AsyncClient) -> None:
        response = await client.request("DELETE", "/api/v1/auth/me", json=self.BODY)
        assert response.status_code == 401

    async def test_une_raison_hors_contrat_est_refusee(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        login = await _login(client, identity_env)

        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            json={"reason": "bored", "details": None},
            headers={"Authorization": f"Bearer {login['session']['access_token']}"},
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_input"
        assert error["details"] == {"reason": "account_deletion_reason_invalid", "field": "reason"}
        assert login["user"]["id"] in {str(u) for u in identity_env.users.rows}

    async def test_un_mot_qui_nest_pas_un_texte_est_refuse_sous_le_champ(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        login = await _login(client, identity_env)

        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            json={"reason": "other", "details": 12},
            headers={"Authorization": f"Bearer {login['session']['access_token']}"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"] == {
            "reason": "account_deletion_details_invalid",
            "field": "details",
        }

    async def test_un_mot_trop_long_est_refuse_sous_le_champ(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        login = await _login(client, identity_env)

        response = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            json={"reason": "other", "details": "x" * 1001},
            headers={"Authorization": f"Bearer {login['session']['access_token']}"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"] == {
            "reason": "account_deletion_details_too_long",
            "field": "details",
            "max_length": 1000,
        }
        assert identity_env.departures.rows == []
