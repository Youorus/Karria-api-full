"""Parcours HTTP complet des appareils : déclarer, détacher, essayer."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from doubles import DevicesEnv
from karria.modules.devices.domain import PushError
from karria.shared.config import AppSettings, Environment, Settings, get_settings

pytestmark = pytest.mark.e2e

BODY: dict[str, Any] = {
    "platform": "ios",
    "push_token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
    "push_permission": "granted",
    "os_name": "iOS",
    "os_version": "18.1",
    "device_brand": "Apple",
    "device_manufacturer": "Apple",
    "device_model": "iPhone 15",
    "device_model_id": "iPhone15,2",
    "device_year_class": 2023,
    "is_physical_device": True,
    "app_version": "2.0.0",
    "app_build": "42",
    "locale": "fr-FR",
    "timezone": "Europe/Paris",
    "region": "FR",
}


class TestRegister:
    async def test_declare_puis_redeclare_lappareil(
        self, client: AsyncClient, authenticate: Any, devices_env: DevicesEnv
    ) -> None:
        principal = authenticate()

        first = await client.put(
            "/api/v1/devices/inst-1", json=BODY, headers={"CF-IPCountry": "fr"}
        )
        assert first.status_code == 200
        assert first.json() == {"installation_id": "inst-1", "push_enabled": True}

        again = await client.put(
            "/api/v1/devices/inst-1",
            json={**BODY, "push_permission": "denied"},
            headers={"CF-IPCountry": "fr"},
        )
        assert again.json() == {"installation_id": "inst-1", "push_enabled": False}

        stored = await devices_env.devices.find(principal.subject_id, "inst-1")
        assert stored is not None
        assert stored.country == "FR"
        assert stored.info.device_model_id == "iPhone15,2"
        assert len(devices_env.devices.rows) == 1

    async def test_exige_la_session(self, client: AsyncClient) -> None:
        response = await client.put("/api/v1/devices/inst-1", json=BODY)
        assert response.status_code == 401

    @pytest.mark.parametrize(
        ("installation_id", "body"),
        [
            ("inst-1", {**BODY, "push_token": "fcm:nope"}),
            ("inst-1", {**BODY, "platform": "web"}),
            ("inst-1", {k: v for k, v in BODY.items() if k != "app_version"}),
            ("espace interdit", BODY),
            ("x" * 129, BODY),
        ],
    )
    async def test_une_declaration_hors_contrat_repond_422(
        self, client: AsyncClient, authenticate: Any, installation_id: str, body: dict[str, Any]
    ) -> None:
        authenticate()
        response = await client.put(f"/api/v1/devices/{installation_id}", json=body)
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_input"
        # Le champ fautif à plat : c'est ce que l'app lit pour afficher
        # l'erreur sous le bon champ, comme le simulateur le fait.
        assert error["details"]["reason"] == "validation_error"
        assert isinstance(error["details"]["field"], str)


class TestUnregister:
    async def test_detache_lappareil_et_reste_silencieux(
        self, client: AsyncClient, authenticate: Any, devices_env: DevicesEnv
    ) -> None:
        authenticate()
        await client.put("/api/v1/devices/inst-1", json=BODY)
        await client.put("/api/v1/devices/inst-2", json=BODY)

        gone = await client.delete("/api/v1/devices/inst-1")
        assert gone.status_code == 204
        again = await client.delete("/api/v1/devices/inst-1")
        assert again.status_code == 204
        assert [d.installation_id for d in devices_env.devices.rows.values()] == ["inst-2"]

    async def test_ne_detache_pas_lappareil_dun_autre_compte(
        self, client: AsyncClient, authenticate: Any, devices_env: DevicesEnv
    ) -> None:
        authenticate(uuid4())
        await client.put("/api/v1/devices/partage", json=BODY)
        authenticate(uuid4())
        await client.delete("/api/v1/devices/partage")
        assert len(devices_env.devices.rows) == 1


class TestTestNotification:
    async def test_envoie_a_ses_propres_appareils(
        self, client: AsyncClient, authenticate: Any, devices_env: DevicesEnv
    ) -> None:
        authenticate()
        await client.put("/api/v1/devices/a", json=BODY)
        await client.put("/api/v1/devices/b", json={**BODY, "push_token": "ExponentPushToken[b]"})
        devices_env.gateway.errors["ExponentPushToken[b]"] = PushError.DEVICE_NOT_REGISTERED

        response = await client.post("/api/v1/devices/me/test-notification")

        assert response.status_code == 200
        assert response.json() == {"targeted": 2, "accepted": 1, "rejected": 1}
        assert devices_env.gateway.batches[0][0].notification.title == "Karria vous entend"

    async def test_exige_la_session(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/devices/me/test-notification")
        assert response.status_code == 401

    async def test_nexiste_pas_en_environnement_deploye(
        self, app: FastAPI, client: AsyncClient, authenticate: Any
    ) -> None:
        authenticate()
        # `model_construct` : on veut l'environnement, pas une configuration
        # de recette complète (secrets, stockage, courrier) pour un seul test.
        staging = Settings.model_construct(app=AppSettings(environment=Environment.STAGING))
        app.dependency_overrides[get_settings] = lambda: staging

        response = await client.post("/api/v1/devices/me/test-notification")

        assert response.status_code == 404
