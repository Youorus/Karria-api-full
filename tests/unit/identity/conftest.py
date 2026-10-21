"""Assemblage des use cases d'`identity` sur les doublures.

Le câblage est exprimé ici une fois : un test décrit un comportement, il
n'a pas à savoir de quels ports dépend le use case qu'il exerce.
"""

from __future__ import annotations

import pytest

from doubles import IdentityEnv
from karria.modules.identity.application import (
    DeleteAccount,
    GetCurrentUser,
    Logout,
    RefreshSession,
    ResendEmailLoginCode,
    ResolveSession,
    StartEmailLogin,
    VerifyEmailLoginCode,
)


@pytest.fixture
def env() -> IdentityEnv:
    return IdentityEnv.build()


@pytest.fixture
def start(env: IdentityEnv) -> StartEmailLogin:
    return StartEmailLogin(
        users=env.users,
        challenges=env.challenges,
        sender=env.sender,
        tokens=env.tokens,
        throttle=env.throttle,
        clock=env.clock,
        uow=env.uow,
    )


@pytest.fixture
def resend(env: IdentityEnv) -> ResendEmailLoginCode:
    return ResendEmailLoginCode(
        challenges=env.challenges,
        sender=env.sender,
        tokens=env.tokens,
        throttle=env.throttle,
        clock=env.clock,
        uow=env.uow,
    )


@pytest.fixture
def verify(env: IdentityEnv) -> VerifyEmailLoginCode:
    return VerifyEmailLoginCode(
        users=env.users,
        challenges=env.challenges,
        sessions=env.sessions,
        tokens=env.tokens,
        throttle=env.throttle,
        clock=env.clock,
        uow=env.uow,
        events=env.events,
    )


@pytest.fixture
def refresh(env: IdentityEnv) -> RefreshSession:
    return RefreshSession(sessions=env.sessions, tokens=env.tokens, clock=env.clock, uow=env.uow)


@pytest.fixture
def resolve(env: IdentityEnv) -> ResolveSession:
    return ResolveSession(sessions=env.sessions, tokens=env.tokens, clock=env.clock)


@pytest.fixture
def logout(env: IdentityEnv) -> Logout:
    return Logout(sessions=env.sessions, tokens=env.tokens, clock=env.clock, uow=env.uow)


@pytest.fixture
def me(env: IdentityEnv) -> GetCurrentUser:
    return GetCurrentUser(users=env.users)


@pytest.fixture
def delete_account(env: IdentityEnv) -> DeleteAccount:
    return DeleteAccount(
        users=env.users,
        identities=env.identities,
        sessions=env.sessions,
        challenges=env.challenges,
        departures=env.departures,
        main_device=env.main_device,
        clock=env.clock,
        uow=env.uow,
        events=env.events,
    )
