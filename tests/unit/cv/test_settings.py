"""La configuration Gemini : un réglage d'environnement, qui avertit sans bloquer."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from karria.shared.config import (
    AppSettings,
    Environment,
    GeminiSettings,
    MailProvider,
    MailSettings,
    Provider,
    SecuritySettings,
    Settings,
    SocialSettings,
    StorageSettings,
)

pytestmark = pytest.mark.unit


def production_settings(gemini: GeminiSettings) -> Settings:
    return Settings(
        app=AppSettings(environment=Environment.PRODUCTION),
        security=SecuritySettings(
            secret_key=SecretStr("un-secret-suffisamment-long-et-aleatoire"),
            allowed_origins=["https://karria.fr"],
            allowed_hosts=["api.karria.fr"],
            token_pepper=SecretStr("un-poivre-distinct"),
        ),
        storage=StorageSettings(
            provider=Provider.SCALEWAY, access_key=SecretStr("k"), secret_key=SecretStr("s")
        ),
        mail=MailSettings(
            provider=MailProvider.SMTP,
            host="smtp.exemple.fr",
            username="envoi@karria.fr",
            password=SecretStr("x"),
            from_address="envoi@karria.fr",
        ),
        social=SocialSettings(google_web_client_id="web-client"),
        gemini=gemini,
    )


def test_les_valeurs_par_defaut_sont_celles_relevees_chez_le_fournisseur() -> None:
    settings = GeminiSettings()

    assert settings.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert settings.text_model == "gemini-3.8-flash"
    assert settings.image_model == "gemini-3.1-flash-image"
    assert settings.request_timeout_seconds == 60.0
    assert settings.image_timeout_seconds == 90.0
    assert not settings.enabled


def test_une_cle_absente_en_production_avertit_sans_empecher_le_demarrage() -> None:
    """Seule la création du CV en est privée : un correctif urgent ne doit pas attendre la clé."""
    settings = production_settings(GeminiSettings())

    assert not settings.gemini.enabled


def test_une_cle_presente_active_le_fournisseur() -> None:
    settings = production_settings(GeminiSettings(api_key=SecretStr("k")))

    assert settings.gemini.enabled
