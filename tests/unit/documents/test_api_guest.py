"""La galerie sans compte (ADR-0025) : lecture seule, comptée par origine ; le reste exige la session."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from documents.fakes import InMemoryTemplateRepository, make_spec, make_template, make_version
from karria.modules.documents.api import dependencies as deps
from karria.modules.documents.domain import GUEST_RATE_LIMIT
from karria.shared.auth import ANONYMOUS_RATE_LIMITED

pytestmark = pytest.mark.unit


@pytest.fixture
async def gallery(app: FastAPI) -> AsyncIterator[InMemoryTemplateRepository]:
    """Un modèle publié, dans un dépôt en mémoire."""
    templates = InMemoryTemplateRepository()
    template = make_template(name="meridian")
    version = make_version(template.id, spec=make_spec())
    await templates.save_version(version)
    await templates.save(
        replace(template, published_version_id=version.id, published_version=version.version)
    )
    app.dependency_overrides[deps.get_template_repository] = lambda: templates
    yield templates
    app.dependency_overrides.pop(deps.get_template_repository, None)


class TestGalerieSansSession:
    async def test_la_galerie_le_detail_et_les_avis_se_lisent(
        self, client: AsyncClient, gallery: InMemoryTemplateRepository
    ) -> None:
        listing = await client.get("/api/v1/templates/")
        assert listing.status_code == 200, listing.text
        assert len(listing.json()) == 1
        template_id = listing.json()[0]["id"]

        detail = await client.get(f"/api/v1/templates/{template_id}")
        reviews = await client.get(f"/api/v1/templates/{template_id}/reviews")

        assert detail.status_code == 200
        assert detail.json()["id"] == template_id
        assert reviews.status_code == 200
        assert reviews.json()["items"] == []

    async def test_compter_une_vue_et_posseder_exigent_la_session(
        self, client: AsyncClient, gallery: InMemoryTemplateRepository
    ) -> None:
        template_id = str(next(iter(gallery.templates)))

        view = await client.post(f"/api/v1/templates/{template_id}/view")
        owned = await client.get("/api/v1/user-templates/me")
        export = await client.post("/api/v1/cv/me/generated-cvs", json={"template_id": template_id})

        assert view.status_code == 401
        assert owned.status_code == 401
        assert export.status_code == 401

    async def test_au_dela_du_quota_l_origine_attend(
        self, client: AsyncClient, gallery: InMemoryTemplateRepository
    ) -> None:
        ip = "198.51.100.42"
        for _ in range(GUEST_RATE_LIMIT):
            assert deps._guest_limiter.hit(f"documents|{ip}") is None

        throttled = await client.get("/api/v1/templates/", headers={"X-Forwarded-For": ip})

        assert throttled.status_code == 429
        assert throttled.json()["error"]["details"]["reason"] == ANONYMOUS_RATE_LIMITED
