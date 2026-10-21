"""La connexion par Apple ou Google : rattacher, créer, refuser.

Le vérificateur est une doublure : ce fichier teste la **décision**, pas la
cryptographie (voir `test_social_verifiers.py`).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from doubles import SAMPLE_CONSENT as CONSENT
from doubles import IdentityEnv, StubSocialVerifier
from karria.modules.identity.application import SocialLogin, UserRegistered, UserSignedIn
from karria.modules.identity.domain import (
    InvalidSocialTokenError,
    SocialEmailMissingError,
    SocialEmailUnverifiedError,
    SocialProvider,
    VerifiedSocialProfile,
)
from karria.shared.errors import ErrorCode

pytestmark = pytest.mark.unit

GOOGLE_PROFILE = VerifiedSocialProfile(
    provider=SocialProvider.GOOGLE,
    subject="115544567408637458330",
    email="Marc@Gmail.com",
    email_verified=True,
)
APPLE_FIRST = VerifiedSocialProfile(
    provider=SocialProvider.APPLE,
    subject="001234.abcdef.1234",
    email="xyz@privaterelay.appleid.com",
    email_verified=True,
)
APPLE_AGAIN = VerifiedSocialProfile(
    provider=SocialProvider.APPLE, subject="001234.abcdef.1234", email=None, email_verified=True
)


def _login(env: IdentityEnv, verifier: StubSocialVerifier) -> SocialLogin:
    return SocialLogin(
        verifier=verifier,
        identities=env.identities,
        users=env.users,
        sessions=env.sessions,
        tokens=env.tokens,
        clock=env.clock,
        uow=env.uow,
        events=env.events,
    )


@pytest.fixture
def env() -> IdentityEnv:
    return IdentityEnv.build()


class TestPremiereVenue:
    async def test_cree_le_compte_et_le_rattache(self, env: IdentityEnv) -> None:
        login = _login(env, StubSocialVerifier(SocialProvider.GOOGLE, GOOGLE_PROFILE))

        outcome = await login.execute(token="jeton", nonce="n", consent=CONSENT, user_agent="ua")

        assert outcome.created_account
        assert outcome.user.email.value == "marc@gmail.com"
        assert outcome.user.consent.terms_version == "2026-10"
        identity = await env.identities.find(SocialProvider.GOOGLE, GOOGLE_PROFILE.subject)
        assert identity is not None
        assert identity.user_id == outcome.user.id
        assert identity.email == "Marc@Gmail.com"
        assert env.sessions.rows[outcome.session.session_id].user_agent == "ua"
        assert len(env.events.of_type(UserRegistered)) == 1
        assert env.events.of_type(UserSignedIn)[0].created_account

    async def test_rattache_un_compte_existant_a_la_meme_adresse(self, env: IdentityEnv) -> None:
        # Compte né par code e-mail, puis Google avec la même adresse.
        first = await _login(env, StubSocialVerifier(SocialProvider.APPLE, APPLE_FIRST)).execute(
            token="t", nonce=None, consent=CONSENT
        )
        google_same_email = VerifiedSocialProfile(
            provider=SocialProvider.GOOGLE,
            subject="g-1",
            email="XYZ@privaterelay.appleid.com",
            email_verified=True,
        )

        outcome = await _login(
            env, StubSocialVerifier(SocialProvider.GOOGLE, google_same_email)
        ).execute(token="t", nonce=None, consent=CONSENT)

        assert not outcome.created_account
        assert outcome.user.id == first.user.id
        assert len(env.users.rows) == 1
        assert len(await env.identities.list_for(first.user.id)) == 2

    async def test_refuse_sans_adresse(self, env: IdentityEnv) -> None:
        login = _login(env, StubSocialVerifier(SocialProvider.APPLE, APPLE_AGAIN))

        with pytest.raises(SocialEmailMissingError) as exc:
            await login.execute(token="t", nonce=None, consent=CONSENT)
        assert exc.value.details["reason"] == "auth_social_email_missing"
        assert env.users.rows == {}

    async def test_refuse_une_adresse_non_attestee(self, env: IdentityEnv) -> None:
        unverified = VerifiedSocialProfile(
            provider=SocialProvider.GOOGLE, subject="g", email="x@y.fr", email_verified=False
        )
        login = _login(env, StubSocialVerifier(SocialProvider.GOOGLE, unverified))

        with pytest.raises(SocialEmailUnverifiedError) as exc:
            await login.execute(token="t", nonce=None, consent=CONSENT)
        assert exc.value.code is ErrorCode.RULE_VIOLATION
        assert exc.value.details["reason"] == "auth_social_email_unverified"
        assert env.users.rows == {}
        assert env.identities.rows == {}

    async def test_un_jeton_refuse_ne_touche_a_rien(self, env: IdentityEnv) -> None:
        login = _login(env, StubSocialVerifier(SocialProvider.GOOGLE, None))

        with pytest.raises(InvalidSocialTokenError) as exc:
            await login.execute(token="forge", nonce=None, consent=CONSENT)
        assert exc.value.code is ErrorCode.UNAUTHENTICATED
        assert env.sessions.rows == {}


class TestRetour:
    async def test_reconnait_par_le_sub_meme_sans_adresse(self, env: IdentityEnv) -> None:
        first = await _login(env, StubSocialVerifier(SocialProvider.APPLE, APPLE_FIRST)).execute(
            token="t", nonce=None, consent=CONSENT
        )
        env.clock.advance(timedelta(days=3))
        newer = CONSENT.__class__(terms_version="2027-01", privacy_policy_version="2026-10")

        again = await _login(env, StubSocialVerifier(SocialProvider.APPLE, APPLE_AGAIN)).execute(
            token="t2", nonce=None, consent=newer
        )

        assert not again.created_account
        assert again.user.id == first.user.id
        assert again.user.consent.terms_version == "2027-01"
        assert again.user.last_login_at == env.clock.now()
        identity = await env.identities.find(SocialProvider.APPLE, APPLE_FIRST.subject)
        assert identity is not None
        assert identity.last_used_at == env.clock.now()
        assert len(env.sessions.rows) == 2
        assert [e.created_account for e in env.events.of_type(UserSignedIn)] == [True, False]

    async def test_un_rattachement_orphelin_est_traite_comme_une_premiere_venue(
        self, env: IdentityEnv
    ) -> None:
        first = await _login(
            env, StubSocialVerifier(SocialProvider.GOOGLE, GOOGLE_PROFILE)
        ).execute(token="t", nonce=None, consent=CONSENT)
        del env.users.rows[first.user.id]  # le compte a été supprimé

        again = await _login(
            env, StubSocialVerifier(SocialProvider.GOOGLE, GOOGLE_PROFILE)
        ).execute(token="t", nonce=None, consent=CONSENT)

        assert again.created_account
        assert again.user.id != first.user.id
        assert again.user.id in env.users.rows


class TestPlafondDeSessions:
    async def test_la_onzieme_session_ferme_la_plus_ancienne(self, env: IdentityEnv) -> None:
        login = _login(env, StubSocialVerifier(SocialProvider.GOOGLE, GOOGLE_PROFILE))
        sessions = []
        for _ in range(11):
            env.clock.advance(timedelta(minutes=1))
            sessions.append((await login.execute(token="t", nonce=None, consent=CONSENT)).session)

        active = await env.sessions.active_of_user(sessions[0].user_id)
        assert len(active) == 10
        oldest = env.sessions.rows[sessions[0].session_id]
        assert not oldest.is_active
        assert oldest.end_reason is not None
        assert uuid4() not in env.sessions.rows
