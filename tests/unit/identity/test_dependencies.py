"""Le câblage d'`identity` : les fabriques des connexions sociales."""

from __future__ import annotations

import pytest

from doubles import IdentityEnv, StaticJwksSource
from karria.modules.identity.api.dependencies import get_apple_login, get_google_login
from karria.modules.identity.application import SocialLogin
from karria.modules.identity.domain import SocialProviderUnavailableError
from karria.shared.config import AppSettings, Environment, Settings, SocialSettings

pytestmark = pytest.mark.unit


def _settings(social: SocialSettings) -> Settings:
    return Settings(app=AppSettings(environment=Environment.TEST), social=social)


def _assemble(factory, settings: Settings) -> SocialLogin:  # noqa: ANN001
    env = IdentityEnv.build()
    return factory(
        settings=settings,
        keys=StaticJwksSource({"keys": []}),
        identities=env.identities,
        users=env.users,
        sessions=env.sessions,
        tokens=env.tokens,
        clock=env.clock,
        uow=env.uow,
        events=env.events,
    )


class TestGoogle:
    def test_sassemble_avec_une_audience(self) -> None:
        login = _assemble(get_google_login, _settings(SocialSettings(google_ios_client_id="ios")))
        assert isinstance(login, SocialLogin)

    def test_refuse_sans_audience(self) -> None:
        with pytest.raises(SocialProviderUnavailableError) as exc:
            _assemble(get_google_login, _settings(SocialSettings()))
        assert exc.value.details["reason"] == "auth_social_provider_unavailable"
        assert exc.value.details["dependency"] == "connexion google"


class TestApple:
    def test_sassemble_avec_le_bundle_par_defaut(self) -> None:
        login = _assemble(get_apple_login, _settings(SocialSettings()))
        assert isinstance(login, SocialLogin)

    def test_refuse_sans_audience(self) -> None:
        with pytest.raises(SocialProviderUnavailableError):
            _assemble(get_apple_login, _settings(SocialSettings(apple_client_id="")))
