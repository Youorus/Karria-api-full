"""La version minimale de l'app : la règle, les réglages, la route publique."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from karria.modules.app.domain import ReleaseStatus, evaluate_release
from karria.shared.config import AppReleaseSettings, Settings, get_settings

pytestmark = pytest.mark.unit


class TestRegle:
    def test_sans_seuil_tout_build_est_a_jour(self) -> None:
        verdict = evaluate_release(1, minimum_build=0, latest_build=0)
        assert verdict.status is ReleaseStatus.OK
        assert (verdict.minimum_build, verdict.latest_build) == (0, 0)

    def test_sous_le_minimum_la_mise_a_jour_est_imposee(self) -> None:
        assert (
            evaluate_release(4, minimum_build=5, latest_build=9).status
            is ReleaseStatus.UPDATE_REQUIRED
        )

    def test_entre_les_deux_elle_est_proposee(self) -> None:
        assert (
            evaluate_release(5, minimum_build=5, latest_build=9).status
            is ReleaseStatus.UPDATE_AVAILABLE
        )

    def test_au_dernier_build_ou_au_dela_tout_va_bien(self) -> None:
        assert evaluate_release(9, minimum_build=5, latest_build=9).status is ReleaseStatus.OK
        assert evaluate_release(12, minimum_build=5, latest_build=9).status is ReleaseStatus.OK

    def test_un_minimum_au_dessus_du_dernier_ne_promet_pas_un_build_inexistant(self) -> None:
        verdict = evaluate_release(6, minimum_build=7, latest_build=3)
        assert verdict.status is ReleaseStatus.UPDATE_REQUIRED
        assert verdict.latest_build == 7


class TestReglages:
    def test_les_valeurs_par_defaut_sont_neutres(self) -> None:
        release = AppReleaseSettings()
        for platform in ("ios", "android"):
            thresholds = release.for_platform(platform)
            assert (thresholds.minimum_build, thresholds.latest_build) == (0, 0)
            assert thresholds.store_url == ""

    def test_chaque_plateforme_a_ses_seuils(self) -> None:
        release = AppReleaseSettings(
            ios_minimum_build=3,
            ios_latest_build=5,
            ios_store_url="https://apps.apple.com/x",
            android_latest_build=8,
        )
        assert release.for_platform("ios").store_url == "https://apps.apple.com/x"
        assert release.for_platform("android").latest_build == 8
        assert release.for_platform("android").minimum_build == 0


@pytest.fixture
def configured(app: FastAPI, settings: Settings) -> Iterator[None]:
    tuned = settings.model_copy(
        update={
            "app_release": AppReleaseSettings(
                ios_minimum_build=10, ios_latest_build=12, ios_store_url="https://apps.apple.com/k"
            )
        }
    )
    app.dependency_overrides[get_settings] = lambda: tuned
    yield
    app.dependency_overrides.pop(get_settings, None)


class TestRoute:
    async def test_sans_session_ni_reglage_la_route_repond_ok(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/app/release", params={"platform": "ios", "build": 1})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "ok",
            "latest_build": 0,
            "minimum_build": 0,
            "latest_version": "",
            "store_url": "",
        }

    async def test_les_seuils_de_l_environnement_decident(
        self, client: AsyncClient, configured: None
    ) -> None:
        required = await client.get("/api/v1/app/release", params={"platform": "ios", "build": 9})
        available = await client.get("/api/v1/app/release", params={"platform": "ios", "build": 11})
        android = await client.get(
            "/api/v1/app/release", params={"platform": "android", "build": 1}
        )
        assert required.json()["status"] == "update_required"
        assert required.json()["store_url"] == "https://apps.apple.com/k"
        assert available.json()["status"] == "update_available"
        assert android.json()["status"] == "ok"

    async def test_une_plateforme_inconnue_ou_un_build_negatif_sont_refuses(
        self, client: AsyncClient
    ) -> None:
        assert (
            await client.get("/api/v1/app/release", params={"platform": "web", "build": 1})
        ).status_code == 422
        assert (
            await client.get("/api/v1/app/release", params={"platform": "ios", "build": -1})
        ).status_code == 422
        assert (await client.get("/api/v1/app/release")).status_code == 422
