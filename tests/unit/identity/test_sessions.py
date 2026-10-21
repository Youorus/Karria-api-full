"""Rafraîchir, reconnaître, se déconnecter — et détecter le vol."""

from __future__ import annotations

from datetime import timedelta

import pytest

from doubles import SAMPLE_CONSENT as CONSENT
from doubles import IdentityEnv
from karria.modules.identity.application import (
    GetCurrentUser,
    IssuedSession,
    Logout,
    RefreshSession,
    ResolveSession,
    StartEmailLogin,
    VerifyEmailLoginCode,
)
from karria.modules.identity.domain import (
    ACCESS_TOKEN_LIFETIME,
    REFRESH_REUSE_GRACE,
    REFRESH_TOKEN_LIFETIME,
    SessionEndReason,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRevokedError,
    UserNotFoundError,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def issued(
    start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
) -> IssuedSession:
    opened = await start.execute(email="marc@exemple.fr", consent=CONSENT, throttle_key="k")
    outcome = await verify.execute(
        challenge_id=opened.challenge_id,
        code=env.sender.last_code_for("marc@exemple.fr"),
        throttle_key="k",
    )
    return outcome.session


class TestRefresh:
    async def test_fait_tourner_les_jetons(
        self,
        issued: IssuedSession,
        refresh: RefreshSession,
        resolve: ResolveSession,
        env: IdentityEnv,
    ) -> None:
        env.clock.advance(timedelta(minutes=20))

        renewed = await refresh.execute(issued.refresh_token)

        assert renewed.session_id == issued.session_id
        assert renewed.access_token != issued.access_token
        assert renewed.refresh_token != issued.refresh_token
        assert await resolve.execute(issued.access_token) is None
        caller = await resolve.execute(renewed.access_token)
        assert caller is not None
        assert caller.user_id == issued.user_id
        session = env.sessions.rows[issued.session_id]
        assert session.rotation_count == 1
        assert session.refresh_expires_at == env.clock.now() + REFRESH_TOKEN_LIFETIME

    async def test_un_jeton_inconnu_est_refuse(self, refresh: RefreshSession) -> None:
        with pytest.raises(SessionNotFoundError) as exc:
            await refresh.execute("n-importe-quoi")
        assert exc.value.details["reason"] == "auth_session_not_found"

    async def test_le_rejeu_dans_la_minute_est_refuse_sans_tout_fermer(
        self, issued: IssuedSession, refresh: RefreshSession, env: IdentityEnv
    ) -> None:
        renewed = await refresh.execute(issued.refresh_token)
        env.clock.advance(REFRESH_REUSE_GRACE)

        with pytest.raises(SessionNotFoundError):
            await refresh.execute(issued.refresh_token)

        assert env.sessions.rows[renewed.session_id].is_active
        # Le couple neuf reste utilisable.
        await refresh.execute(renewed.refresh_token)

    async def test_le_rejeu_tardif_ferme_toutes_les_sessions(
        self, issued: IssuedSession, refresh: RefreshSession, env: IdentityEnv
    ) -> None:
        renewed = await refresh.execute(issued.refresh_token)
        env.clock.advance(REFRESH_REUSE_GRACE + timedelta(seconds=1))

        with pytest.raises(SessionNotFoundError):
            await refresh.execute(issued.refresh_token)

        session = env.sessions.rows[renewed.session_id]
        assert not session.is_active
        assert session.end_reason is SessionEndReason.TOKEN_REUSE_DETECTED
        with pytest.raises(SessionNotFoundError):
            await refresh.execute(renewed.refresh_token)

    async def test_une_session_perimee_est_refusee(
        self, issued: IssuedSession, refresh: RefreshSession, env: IdentityEnv
    ) -> None:
        env.clock.advance(REFRESH_TOKEN_LIFETIME)
        with pytest.raises(SessionExpiredError):
            await refresh.execute(issued.refresh_token)

    async def test_une_session_close_dit_pourquoi(
        self, issued: IssuedSession, refresh: RefreshSession, env: IdentityEnv
    ) -> None:
        # Fermée par ailleurs, sans passer par la lecture « active ».
        session = env.sessions.rows[issued.session_id]
        env.sessions.rows[session.id] = session.ended(
            reason=SessionEndReason.LOGGED_OUT, at=env.clock.now()
        )

        # Le dépôt ne rend plus la session par son jeton courant ; le
        # rejeu éventuel non plus : c'est un jeton inconnu.
        with pytest.raises(SessionNotFoundError):
            await refresh.execute(issued.refresh_token)

    async def test_ensure_refreshable_distingue_revoquee(self, env: IdentityEnv) -> None:
        from uuid import uuid4  # noqa: PLC0415

        from karria.modules.identity.domain import open_session  # noqa: PLC0415

        session = open_session(
            session_id=uuid4(),
            user_id=uuid4(),
            access_token_hash="a",
            refresh_token_hash="r",
            at=env.clock.now(),
            user_agent=None,
        ).ended(reason=SessionEndReason.LOGGED_OUT, at=env.clock.now())
        with pytest.raises(SessionRevokedError):
            session.ensure_refreshable_at(env.clock.now())


class TestResolve:
    async def test_reconnait_lappelant_et_note_lusage_de_temps_en_temps(
        self, issued: IssuedSession, resolve: ResolveSession, env: IdentityEnv
    ) -> None:
        opened_at = env.clock.now()
        env.clock.advance(timedelta(minutes=1))
        caller = await resolve.execute(issued.access_token)
        assert caller is not None
        assert caller.session_id == issued.session_id
        assert env.sessions.rows[issued.session_id].last_used_at == opened_at

        env.clock.advance(timedelta(minutes=6))
        await resolve.execute(issued.access_token)
        assert env.sessions.rows[issued.session_id].last_used_at == env.clock.now()

    async def test_un_jeton_dacces_perime_nest_personne(
        self, issued: IssuedSession, resolve: ResolveSession, env: IdentityEnv
    ) -> None:
        env.clock.advance(ACCESS_TOKEN_LIFETIME)
        assert await resolve.execute(issued.access_token) is None

    async def test_un_jeton_inconnu_nest_personne(self, resolve: ResolveSession) -> None:
        assert await resolve.execute("inconnu") is None


class TestLogout:
    async def test_ferme_la_session_et_rien_dautre(
        self,
        issued: IssuedSession,
        logout: Logout,
        resolve: ResolveSession,
        refresh: RefreshSession,
        env: IdentityEnv,
    ) -> None:
        env.clock.advance(timedelta(minutes=1))
        other = await refresh.execute((await refresh.execute(issued.refresh_token)).refresh_token)
        _ = other

        await logout.execute(issued.refresh_token)  # déjà tourné : silencieux

        session = env.sessions.rows[issued.session_id]
        assert session.is_active  # le jeton présenté n'était plus le courant

    async def test_ferme_la_session_courante(
        self, issued: IssuedSession, logout: Logout, resolve: ResolveSession, env: IdentityEnv
    ) -> None:
        await logout.execute(issued.refresh_token)

        session = env.sessions.rows[issued.session_id]
        assert not session.is_active
        assert session.end_reason is SessionEndReason.LOGGED_OUT
        assert await resolve.execute(issued.access_token) is None
        await logout.execute(issued.refresh_token)  # deux fois : pas d'erreur


class TestGetCurrentUser:
    async def test_rend_le_compte(self, issued: IssuedSession, me: GetCurrentUser) -> None:
        user = await me.execute(issued.user_id)
        assert user.email.value == "marc@exemple.fr"

    async def test_un_compte_disparu_invalide_la_session(
        self, issued: IssuedSession, me: GetCurrentUser, env: IdentityEnv
    ) -> None:
        del env.users.rows[issued.user_id]
        with pytest.raises(UserNotFoundError) as exc:
            await me.execute(issued.user_id)
        assert exc.value.details["reason"] == "user_not_found"


class TestPurges:
    async def test_les_doublures_purgent(self, issued: IssuedSession, env: IdentityEnv) -> None:
        """Couvre les méthodes de purge des dépôts en mémoire, exercées par les scripts."""
        env.clock.advance(REFRESH_TOKEN_LIFETIME + timedelta(days=1))
        assert await env.sessions.purge_ended_before(env.clock.now()) == 1
        assert await env.challenges.purge_before(env.clock.now()) == 1
