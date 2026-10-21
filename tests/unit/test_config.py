"""La configuration doit refuser de démarrer si elle est dangereuse."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from karria.shared.config import (
    AppSettings,
    DatabaseSettings,
    Environment,
    MailProvider,
    MailSettings,
    Provider,
    SecuritySettings,
    Settings,
    StorageSettings,
)
from karria.shared.config.settings import BASE_DIR

pytestmark = pytest.mark.unit


def _safe_storage() -> StorageSettings:
    """Stockage réel, tel qu'exigé sur un environnement déployé."""
    return StorageSettings(
        provider=Provider.SCALEWAY,
        access_key=SecretStr("SCW00000000000000000"),
        secret_key=SecretStr("00000000-0000-4000-8000-000000000000"),
    )


def _safe_security() -> SecuritySettings:
    return SecuritySettings(
        secret_key=SecretStr("un-secret-suffisamment-long-et-aleatoire"),
        allowed_origins=["https://karria.fr"],
        allowed_hosts=["api.karria.fr"],
        token_pepper=SecretStr("un-poivre-distinct-et-tout-aussi-aleatoire"),
    )


def _safe_mail() -> MailSettings:
    """Transport réel : en production, un code doit atteindre une boîte."""
    return MailSettings(
        provider=MailProvider.SMTP,
        host="smtp.exemple.fr",
        port=587,
        username="envoi@karria.fr",
        password=SecretStr("un-mot-de-passe-d-application"),
        from_address="envoi@karria.fr",
    )


class TestProductionInvariants:
    """Une configuration de production invalide doit échouer au démarrage."""

    def test_accepte_une_configuration_de_production_saine(self) -> None:
        settings = Settings(
            app=AppSettings(environment=Environment.PRODUCTION, debug=False),
            security=_safe_security(),
            storage=_safe_storage(),
            mail=_safe_mail(),
        )
        assert settings.app.environment.is_deployed

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("allowed_hosts", ["*"]),
            ("allowed_origins", ["*"]),
        ],
    )
    def test_refuse_les_jokers_reseau(self, field: str, value: list[str]) -> None:
        security = _safe_security().model_copy(update={field: value})
        with pytest.raises(ValidationError, match=field.upper()):
            Settings(
                app=AppSettings(environment=Environment.PRODUCTION),
                security=security,
                storage=_safe_storage(),
                mail=_safe_mail(),
            )

    def test_refuse_un_poivre_de_jetons_par_defaut(self) -> None:
        """Sans poivre, une fuite de la base casse les codes à six chiffres."""
        security = _safe_security().model_copy(update={"token_pepper": SecretStr("")})
        with pytest.raises(ValidationError, match="TOKEN_PEPPER"):
            Settings(
                app=AppSettings(environment=Environment.PRODUCTION),
                security=security,
                storage=_safe_storage(),
                mail=_safe_mail(),
            )

    def test_refuse_le_mode_debug(self) -> None:
        with pytest.raises(ValidationError, match="APP__DEBUG"):
            Settings(
                app=AppSettings(environment=Environment.PRODUCTION, debug=True),
                security=_safe_security(),
            )

    def test_refuse_le_secret_par_defaut(self) -> None:
        with pytest.raises(ValidationError, match="SECURITY__SECRET_KEY"):
            Settings(app=AppSettings(environment=Environment.PRODUCTION), storage=_safe_storage())

    def test_refuse_un_stockage_en_memoire(self) -> None:
        """Un stockage en mémoire perd tous les fichiers au redémarrage.

        Silencieusement, qui plus est : rien dans les journaux ne dirait que
        les CV ont disparu.
        """
        with pytest.raises(ValidationError, match="STORAGE__PROVIDER"):
            Settings(
                app=AppSettings(environment=Environment.PRODUCTION),
                security=_safe_security(),
                storage=StorageSettings(provider=Provider.MEMORY),
                mail=_safe_mail(),
            )

    def test_refuse_un_envoi_console_en_production(self) -> None:
        """Le code de connexion est la seule porte d'entrée de Karria.

        Affiché dans les journaux, il n'atteint personne : plus aucune
        inscription ne peut aboutir, sans qu'aucune erreur ne le signale.
        """
        with pytest.raises(ValidationError, match="MAIL__PROVIDER"):
            Settings(
                app=AppSettings(environment=Environment.PRODUCTION),
                security=_safe_security(),
                storage=_safe_storage(),
                mail=MailSettings(provider=MailProvider.CONSOLE),
            )

    def test_tolere_ces_reglages_en_local(self) -> None:
        """Les mêmes valeurs sont acceptables hors environnement déployé."""
        settings = Settings(app=AppSettings(environment=Environment.LOCAL, debug=True))
        assert not settings.app.environment.is_deployed


class TestDatabaseSettings:
    def test_refuse_un_pool_incoherent(self) -> None:
        with pytest.raises(ValidationError, match="MAX_POOL_SIZE"):
            DatabaseSettings(min_pool_size=10, max_pool_size=2)


class TestAppSettings:
    def test_l_adresse_publique_se_compose_cote_serveur(self) -> None:
        """Jamais déduite de `Host` : c'est ce qui ferme la redirection ouverte."""
        app = AppSettings(public_url="https://api.karria.fr/", api_prefix="/api/v1")

        assert app.public_api_url("/retour") == "https://api.karria.fr/api/v1/retour"


def test_le_fichier_env_est_cherche_a_la_racine_du_depot() -> None:
    """Un `.env` cherché au mauvais endroit est un `.env` jamais lu.

    Et jamais lu **en silence** : toute la configuration retombe alors sur
    ses valeurs par défaut, sans le moindre avertissement. Le repère le plus
    sûr est la présence du `pyproject.toml`, seul fichier garanti à la
    racine.
    """
    assert (BASE_DIR / "pyproject.toml").is_file()


class TestSocialSettings:
    def test_les_audiences_ignorent_les_identifiants_vides(self) -> None:
        from karria.shared.config import SocialSettings  # noqa: PLC0415

        social = SocialSettings(google_ios_client_id="ios", google_web_client_id="")

        assert social.google_audiences == ["ios"]
        assert social.google_enabled
        assert social.apple_audiences == ["com.karria.app"]
        assert social.apple_enabled

    def test_sans_identifiant_google_la_connexion_google_est_indisponible(self) -> None:
        from karria.shared.config import SocialSettings  # noqa: PLC0415

        social = SocialSettings(apple_client_id="")

        assert not social.google_enabled
        assert not social.apple_enabled

    def test_le_bloc_push_a_des_defauts_surs(self) -> None:
        from karria.shared.config import PushSettings  # noqa: PLC0415

        push = PushSettings()

        assert push.expo_api_url.startswith("https://exp.host")
        assert push.expo_access_token.get_secret_value() == ""
        assert push.request_timeout_seconds > 0
