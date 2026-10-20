"""Parcours HTTP complet des sondes de santé.

Ces tests traversent toute la pile — middlewares, gestionnaires d'erreurs,
routage, sérialisation — sans jamais ouvrir de socket ni de connexion à la
base. Les sondes sont substituées par `dependency_overrides`, la couture
prévue à cet effet dans `api/dependencies.py`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from karria.modules.health.api.dependencies import get_check_system_health
from karria.modules.health.application import CheckSystemHealth
from karria.modules.health.domain import ComponentHealth
from karria.shared.logging import CORRELATION_ID_HEADER

pytestmark = pytest.mark.e2e


class StubProbe:
    def __init__(self, name: str, *, healthy: bool, critical: bool = True) -> None:
        self.name = name
        self.critical = critical
        self._healthy = healthy

    async def check(self) -> ComponentHealth:
        if self._healthy:
            return ComponentHealth.healthy(self.name, critical=self.critical)
        return ComponentHealth.unhealthy(self.name, "indisponible", critical=self.critical)


def _override_probes(app: FastAPI, *probes: StubProbe) -> None:
    app.dependency_overrides[get_check_system_health] = lambda: CheckSystemHealth(
        probes=list(probes)
    )


class TestLiveness:
    async def test_repond_sans_toucher_aux_dependances(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


class TestReadiness:
    async def test_renvoie_200_quand_tout_va_bien(self, app: FastAPI, client: AsyncClient) -> None:
        _override_probes(app, StubProbe("db", healthy=True))

        response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    async def test_renvoie_503_si_une_dependance_critique_est_hs(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        _override_probes(app, StubProbe("db", healthy=False))

        response = await client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["status"] == "unhealthy"

    async def test_renvoie_200_si_seule_une_dependance_accessoire_est_hs(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        """Dégradé n'est pas hors service : l'instance garde son trafic."""
        _override_probes(
            app,
            StubProbe("db", healthy=True),
            StubProbe("cache", healthy=False, critical=False),
        )

        response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"


class TestTransverse:
    async def test_chaque_reponse_porte_un_identifiant_de_correlation(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/health/live")

        assert response.headers[CORRELATION_ID_HEADER]

    async def test_lidentifiant_entrant_est_propage(self, client: AsyncClient) -> None:
        """Permet à une passerelle ou à un appelant de propager sa trace."""
        response = await client.get(
            "/health/live", headers={CORRELATION_ID_HEADER: "trace-amont-42"}
        )

        assert response.headers[CORRELATION_ID_HEADER] == "trace-amont-42"

    async def test_les_entetes_de_securite_sont_appliques(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"

    async def test_une_route_inconnue_respecte_le_format_derreur(self, client: AsyncClient) -> None:
        """Même un 404 de routage sort au format standard de l'API."""
        response = await client.get("/route-qui-nexiste-pas")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert body["correlation_id"]
