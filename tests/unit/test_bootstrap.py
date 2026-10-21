"""L'assemblage : le registre, la fabrique et ce qu'ils garantissent.

Ces tests protègent la promesse de l'ADR-0002 — ajouter un module coûte
une ligne, et cette ligne alimente à la fois les routes, les modèles et
les abonnements — sans monter de base ni de serveur.
"""

from __future__ import annotations

import pytest

from karria.bootstrap.app import create_app
from karria.bootstrap.modules import (
    ENABLED_MODULES,
    all_model_modules,
    all_permissions,
    register_event_subscriptions,
)
from karria.shared.config import AppSettings, Environment, Settings
from karria.shared.events import EventBus

pytestmark = pytest.mark.unit


def test_le_registre_expose_les_routes_de_chaque_module() -> None:
    """Un module enregistré est servi, sans qu'on touche à `main.py`."""
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

    chemins = set(app.openapi()["paths"])

    assert "/health/live" in chemins
    assert "/health/ready" in chemins
    assert "/api/v1/auth/email/start" in chemins
    assert "/api/v1/auth/social/apple" in chemins
    assert "/api/v1/devices/{installation_id}" in chemins


def test_les_sondes_de_sante_echappent_au_prefixe_versionne() -> None:
    """Elles s'adressent à l'orchestrateur, pas aux clients du produit."""
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

    chemins = set(app.openapi()["paths"])

    assert not any(chemin.startswith("/api/v1/health") for chemin in chemins)


def test_les_modeles_pour_aerich_sont_derives_du_registre() -> None:
    """Aucune liste de modèles tenue à la main : `identity` et `devices` y figurent."""
    assert all_model_modules() == [
        module.models_module for module in ENABLED_MODULES if module.models_module
    ]
    assert "karria.modules.identity.infrastructure.models" in all_model_modules()
    assert "karria.modules.devices.infrastructure.models" in all_model_modules()


def test_les_permissions_ne_se_dupliquent_pas_entre_modules() -> None:
    """Deux modules qui déclarent la même chaîne se disputeraient un droit."""
    permissions = all_permissions()

    assert len(permissions) == len(set(permissions))


def test_les_abonnements_du_registre_ne_levent_pas() -> None:
    """Un module sans abonnement est un cas nominal, pas une erreur."""
    bus = EventBus()

    register_event_subscriptions(bus)

    assert bus.subscriptions() == {}


def test_la_documentation_est_fermee_en_production() -> None:
    """Le schéma décrit toute la surface de l'API : il ne s'expose pas publiquement."""
    from pydantic import SecretStr  # noqa: PLC0415

    from karria.shared.config import (  # noqa: PLC0415
        MailProvider,
        MailSettings,
        Provider,
        SecuritySettings,
        StorageSettings,
    )

    settings = Settings(
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
    )

    app = create_app(settings)

    assert app.docs_url is None
    assert app.openapi_url is None
