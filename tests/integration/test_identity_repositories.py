"""Les dépôts d'`identity` contre PostgreSQL et le schéma migré."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from karria.modules.identity.domain import (
    Consent,
    EmailAddress,
    ProviderIdentity,
    SessionEndReason,
    SessionNotFoundError,
    SocialProvider,
    User,
    open_challenge,
    open_session,
)
from karria.modules.identity.infrastructure import (
    TortoiseLoginChallengeRepository,
    TortoiseProviderIdentityRepository,
    TortoiseSessionRepository,
    TortoiseUserRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def _user(email: str = "marc@exemple.fr") -> User:
    return User(
        id=uuid4(),
        email=EmailAddress(email),
        email_verified_at=NOW,
        consent=Consent("2026-10", "2026-10", NOW),
        created_at=NOW,
        updated_at=NOW,
    )


class TestUsers:
    async def test_cree_retrouve_et_met_a_jour(self) -> None:
        repo = TortoiseUserRepository()
        user = _user()
        await repo.add(user)

        found = await repo.find_by_email(EmailAddress("marc@exemple.fr"))
        assert found is not None
        assert found.id == user.id
        assert found.consent.terms_version == "2026-10"
        assert await repo.find_by_id(uuid4()) is None

        await repo.save(
            user.with_consent(
                terms_version="2027", privacy_policy_version="2026-10", at=NOW
            ).signed_in(at=NOW)
        )
        again = await repo.find_by_id(user.id)
        assert again is not None
        assert again.consent.terms_version == "2027"
        assert again.last_login_at == NOW

    async def test_ladresse_est_unique(self) -> None:
        from tortoise.exceptions import IntegrityError  # noqa: PLC0415

        repo = TortoiseUserRepository()
        await repo.add(_user())
        with pytest.raises(IntegrityError):
            await repo.add(_user())


class TestProviderIdentities:
    async def test_rattache_retrouve_et_touche(self) -> None:
        users, repo = TortoiseUserRepository(), TortoiseProviderIdentityRepository()
        user = _user()
        await users.add(user)
        identity = ProviderIdentity(
            id=uuid4(),
            user_id=user.id,
            provider=SocialProvider.APPLE,
            subject="001.abc",
            email=None,
            created_at=NOW,
            last_used_at=NOW,
        )
        await repo.add(identity)

        found = await repo.find(SocialProvider.APPLE, "001.abc")
        assert found is not None
        assert found.user_id == user.id
        assert await repo.find(SocialProvider.GOOGLE, "001.abc") is None

        later = NOW + timedelta(days=1)
        await repo.touch(identity.id, later)
        assert [i.last_used_at for i in await repo.list_for(user.id)] == [later]


class TestSessions:
    async def test_le_cycle_complet(self) -> None:
        users, repo = TortoiseUserRepository(), TortoiseSessionRepository()
        user = _user()
        await users.add(user)
        session = open_session(
            session_id=uuid4(),
            user_id=user.id,
            access_token_hash="a" * 64,
            refresh_token_hash="r" * 64,
            at=NOW,
            user_agent="ua",
        )
        await repo.add(session)

        assert (await repo.find_by_access_fingerprint("a" * 64)) is not None
        assert (await repo.find_by_refresh_fingerprint("r" * 64)) is not None
        assert await repo.find_by_previous_refresh("r" * 64) is None

        rotated = session.rotated(access_token_hash="b" * 64, refresh_token_hash="s" * 64, at=NOW)
        await repo.save(rotated)
        previous = await repo.find_by_previous_refresh("r" * 64)
        assert previous is not None
        assert previous.rotation_count == 1
        assert await repo.find_by_refresh_fingerprint("r" * 64) is None

        assert len(await repo.active_of_user(user.id)) == 1
        assert (
            await repo.end_all_of_user(
                user.id, reason=SessionEndReason.TOKEN_REUSE_DETECTED, at=NOW
            )
            == 1
        )
        assert await repo.active_of_user(user.id) == []
        ended = await repo.find_by_access_fingerprint("b" * 64)
        assert ended is not None
        assert ended.end_reason is SessionEndReason.TOKEN_REUSE_DETECTED

        assert await repo.purge_ended_before(NOW + timedelta(days=400)) == 1

    async def test_sauver_une_session_disparue_leve(self) -> None:
        repo = TortoiseSessionRepository()
        ghost = open_session(
            session_id=uuid4(),
            user_id=uuid4(),
            access_token_hash="x" * 64,
            refresh_token_hash="y" * 64,
            at=NOW,
            user_agent=None,
        )
        with pytest.raises(SessionNotFoundError):
            await repo.save(ghost)


class TestLoginChallenges:
    async def test_ouvre_met_a_jour_et_purge(self) -> None:
        repo = TortoiseLoginChallengeRepository()
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="marc@exemple.fr",
            user_id=None,
            code_hash="h" * 64,
            terms_version="2026-10",
            privacy_policy_version="2026-10",
            at=NOW,
        )
        await repo.add(challenge)

        found = await repo.find(challenge.id)
        assert found is not None
        assert found.code_sends == 1
        assert await repo.find(uuid4()) is None

        await repo.save(
            found.failed_attempt(at=NOW).completed(user_id=uuid4(), created_account=True, at=NOW)
        )
        done = await repo.find(challenge.id)
        assert done is not None
        assert done.created_account
        assert done.attempts == 1

        assert await repo.purge_before(NOW + timedelta(hours=1)) == 1
