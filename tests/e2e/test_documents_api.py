"""Parcours HTTP du module `documents` : `karria_app/docs/contrat-api-modeles.md`.

Toute la pile est traversée — authentification, validation, routes, cas
d'usage, format d'erreur — avec des dépôts en mémoire et un moteur de
documents factice substitués par `dependency_overrides`. Aucune base, aucun
Gotenberg.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from tests.unit.documents.fakes import (
    NOW,
    FakeCvSource,
    FakeEngine,
    FakeTemplateJobs,
    InMemoryGeneratedCvRepository,
    InMemoryTemplateRepository,
    InMemoryUserTemplateRepository,
    make_generated,
    make_spec,
    make_template,
    make_version,
)

from doubles import FakeClock
from karria.modules.documents.api.dependencies import (
    get_clock,
    get_cv_source,
    get_engine,
    get_generated_cv_repository,
    get_jobs,
    get_template_repository,
    get_user_template_repository,
)
from karria.modules.documents.domain.templates import ExplanationStatus, VersionStatus
from karria.shared.auth import Principal
from karria.shared.config import DocumentsSettings, Settings, get_settings
from karria.shared.database import NullUnitOfWork
from karria.shared.http.dependencies import get_unit_of_work
from karria.shared.storage import Bucket, InMemoryObjectStorage

pytestmark = pytest.mark.e2e

API = "/api/v1"
TOKEN = "jeton-admin"  # noqa: S105 - jeton de test


class Fakes:
    """Les doublures d'un test."""

    def __init__(self) -> None:
        self.templates = InMemoryTemplateRepository()
        self.owned = InMemoryUserTemplateRepository()
        self.generated = InMemoryGeneratedCvRepository()
        self.cvs = FakeCvSource()
        self.engine = FakeEngine()
        self.jobs = FakeTemplateJobs()


@pytest.fixture
def fakes(app: FastAPI, ai_consented: None) -> Iterator[Fakes]:
    doubles = Fakes()
    overrides = {
        get_template_repository: lambda: doubles.templates,
        get_user_template_repository: lambda: doubles.owned,
        get_generated_cv_repository: lambda: doubles.generated,
        get_cv_source: lambda: doubles.cvs,
        get_engine: lambda: doubles.engine,
        get_jobs: lambda: doubles.jobs,
        get_clock: lambda: FakeClock(NOW),
        get_unit_of_work: NullUnitOfWork,
    }
    app.dependency_overrides.update(overrides)
    yield doubles
    for key in overrides:
        app.dependency_overrides.pop(key, None)


@pytest.fixture
def me(authenticate: Callable[..., Principal]) -> Principal:
    return authenticate()


@pytest.fixture
def admin(app: FastAPI, settings: Settings) -> Iterator[dict[str, str]]:
    configured = settings.model_copy(
        update={"documents": DocumentsSettings(admin_token=SecretStr(TOKEN))}
    )
    app.dependency_overrides[get_settings] = lambda: configured
    yield {"Authorization": f"Bearer {TOKEN}"}
    app.dependency_overrides.pop(get_settings, None)


def reason(response: object) -> str:
    return response.json()["error"]["details"]["reason"]  # type: ignore[attr-defined, no-any-return]


# ---------------------------------------------------------------------- galerie


class TestGalerie:
    async def test_sans_session_la_galerie_se_lit_mais_rien_ne_s_ecrit(
        self, client: AsyncClient, fakes: Fakes
    ) -> None:
        # ADR-0025 : un modèle de CV n'est pas un contenu de compte.
        listing = await client.get(f"{API}/templates/")
        owned = await client.get(f"{API}/user-templates/me")

        assert listing.status_code == 200
        assert owned.status_code == 401

    async def test_la_galerie_presente_les_modeles_publies_avec_leurs_apercus_signes(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, version = fakes.templates.publish(spec=make_spec(spaced=True, photo=True))
        fakes.templates.templates[template.id] = replace(
            fakes.templates.templates[template.id], category="classique", price=None
        )
        version_store = fakes.templates.versions
        version_store[version.id] = replace(version, thumbnail_blurhash="LKO2")

        response = await client.get(f"{API}/templates/")

        assert response.status_code == 200, response.text
        [item] = response.json()
        assert item["id"] == str(template.id)
        assert item["category"] == "classique"
        assert item["description"] is None
        assert item["price"] is None
        assert item["signed_thumbnail_url"].startswith("https://storage.local/")
        assert item["thumbnail_blurhash"] == "LKO2"
        assert item["formats"] == ["pdf", "docx"]
        assert item["ats_safe_available"] is True
        assert item["ats"]["readiness"] == "warning"
        assert set(item["ats"]["warnings"]) == {"spaced_titles", "photo"}
        assert item["pages"] is None

    async def test_la_galerie_filtre_par_categorie_et_prix(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        fakes.templates.publish(name="libre")
        pro, _ = fakes.templates.publish(name="pro", premium=True)

        response = await client.get(f"{API}/templates/", params={"premium": "true"})

        assert [t["id"] for t in response.json()] == [str(pro.id)]

    async def test_un_modele_sans_version_publiee_se_presente_sans_apercu(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        from decimal import Decimal  # noqa: PLC0415

        template = replace(make_template(), price=Decimal("4.99"))
        await fakes.templates.save(template)
        fakes.templates.hide_unpublished = False

        [item] = (await client.get(f"{API}/templates/")).json()

        assert item["signed_thumbnail_url"] is None
        assert item["thumbnail_blurhash"] is None
        assert item["price"] == 4.99
        assert item["ats"] == {"readiness": "good", "warnings": []}
        assert item["fields_added"] == []
        assert item["ats_safe_available"] is False

    async def test_le_detail_d_un_modele_porte_les_pages_du_carrousel(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(spec=make_spec(synthesized_headline=True))

        response = await client.get(f"{API}/templates/{template.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["fields_added"] == ["headline"]
        assert body["pages"] == [
            {
                "kind": "cv",
                "label": "CV",
                "signed_thumbnail_url": body["signed_thumbnail_url"],
                "signed_full_url": body["signed_full_url"],
            }
        ]

    async def test_un_modele_inconnu_repond_template_not_found(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.get(f"{API}/templates/{uuid4()}")

        assert response.status_code == 404
        assert reason(response) == "template_not_found"

    async def test_une_vue_est_comptee(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        response = await client.post(f"{API}/templates/{template.id}/view")

        assert response.status_code == 204
        assert fakes.templates.templates[template.id].views_count == 1

    async def test_les_avis_ont_la_forme_v1_et_sont_vides(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        response = await client.get(f"{API}/templates/{template.id}/reviews")

        assert response.json() == {"items": [], "total": 0, "rating_avg": 0.0, "rating_count": 0}


# ---------------------------------------------------------------------- aperçu


class TestApercu:
    async def test_l_apercu_rend_les_pages_signees_et_la_mise_en_page(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()
        fakes.cvs.put(me.subject_id)

        response = await client.post(
            f"{API}/templates/{template.id}/preview", json={"ats_safe": True}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["fit"] == {"spacing": 0.95, "font_scale": 1.0, "pages": 1}
        [page] = body["pages"]
        assert page["width"] == 1240
        assert page["height"] == 1754
        assert "temp" in page["signed_url"]

    async def test_l_apercu_sans_corps_prend_la_variante_d_origine(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(spec=make_spec(spaced=True))
        fakes.cvs.put(me.subject_id)

        response = await client.post(f"{API}/templates/{template.id}/preview")

        assert response.status_code == 200
        spec = fakes.engine.called("render_pdf")[0]["spec"]
        assert spec["styles"]["section_title"]["letter_spacing_pt"] == 2.0

    async def test_sans_cv_l_apercu_repond_cv_missing(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        response = await client.post(f"{API}/templates/{template.id}/preview")

        assert response.status_code == 409
        assert reason(response) == "cv_missing"

    async def test_un_modele_pro_non_possede_repond_template_locked(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(premium=True)
        fakes.cvs.put(me.subject_id)

        response = await client.post(f"{API}/templates/{template.id}/preview")

        assert response.status_code == 403
        assert reason(response) == "template_locked"
        assert response.json()["error"]["details"]["lock"] == "premium_not_owned"


# ---------------------------------------------------------------------- possession et choix


class TestPossessionEtChoix:
    async def test_un_modele_gratuit_s_ajoute_puis_se_liste(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        added = await client.post(
            f"{API}/user-templates/me", json={"template_id": str(template.id)}
        )
        listed = await client.get(f"{API}/user-templates/me")

        assert added.status_code == 201, added.text
        assert added.json()["template"] == {"id": str(template.id), "label": None, "version": None}
        assert added.json()["source"] == "free"
        assert added.json()["is_active"] is True
        assert listed.json() == [added.json()]

    async def test_un_modele_pro_ne_s_ajoute_pas_gratuitement(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(premium=True)

        response = await client.post(
            f"{API}/user-templates/me", json={"template_id": str(template.id)}
        )

        assert response.status_code == 422
        assert reason(response) == "template_not_free"

    async def test_sans_choix_le_modele_choisi_repond_204(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.get(f"{API}/user-templates/me/selected")

        assert response.status_code == 204
        assert response.content == b""

    async def test_le_modele_choisi_se_garde_et_se_relit(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        chosen = await client.put(
            f"{API}/user-templates/me/selected",
            json={"template_id": str(template.id), "ats_safe": True},
        )
        read = await client.get(f"{API}/user-templates/me/selected")

        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["template"] == {
            "id": str(template.id),
            "label": template.label,
            "version": 1,
        }
        assert chosen.json()["ats_safe"] is True
        assert read.json() == chosen.json()

    async def test_un_choix_dont_le_modele_a_disparu_se_relit_sans_libelle(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()
        await client.put(
            f"{API}/user-templates/me/selected", json={"template_id": str(template.id)}
        )
        del fakes.templates.templates[template.id]

        read = await client.get(f"{API}/user-templates/me/selected")

        assert read.json()["template"] == {"id": str(template.id), "label": None, "version": None}

    async def test_un_modele_pro_non_possede_ne_se_choisit_pas(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(premium=True)

        response = await client.put(
            f"{API}/user-templates/me/selected", json={"template_id": str(template.id)}
        )

        assert response.status_code == 403
        assert reason(response) == "template_locked"


# ---------------------------------------------------------------------- exports


class TestExports:
    async def test_l_export_rend_les_deux_fichiers_signes_et_le_score(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        template, _ = fakes.templates.publish()
        fakes.cvs.put(me.subject_id)

        response = await client.post(
            f"{API}/cv/me/generated-cvs", json={"template_id": str(template.id)}
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert "filename=CV%20-%20Awa%20Diallo.pdf" in body["pdf_url"]
        assert "filename=CV%20-%20Awa%20Diallo.docx" in body["docx_url"]
        assert body["template"]["label"] == template.label
        assert body["offer_id"] is None
        assert body["ats"]["format"] == "pdf"
        assert body["ats"]["explanation"] is None
        assert body["ats"]["explanation_status"] == "pending"
        assert set(body["ats"]["docx"]) == {"score", "level", "format", "checks"}
        assert fakes.jobs.explanations == [UUID(body["id"])]
        assert sum(1 for b, _ in storage.objects if b is Bucket.DOCUMENTS) == 2

    async def test_l_export_pdf_seul_n_a_ni_docx_ni_score_docx(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()
        fakes.cvs.put(me.subject_id)

        body = (
            await client.post(
                f"{API}/cv/me/generated-cvs",
                json={"template_id": str(template.id), "formats": ["pdf"]},
            )
        ).json()

        assert body["docx_url"] is None
        assert "docx" not in body["ats"]

    async def test_sans_modele_ni_choix_l_export_repond_template_not_found(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(f"{API}/cv/me/generated-cvs", json={})

        assert response.status_code == 404
        assert reason(response) == "template_not_found"

    async def test_sans_cv_l_export_repond_cv_missing(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        response = await client.post(
            f"{API}/cv/me/generated-cvs", json={"template_id": str(template.id)}
        )

        assert response.status_code == 409
        assert reason(response) == "cv_missing"

    async def test_un_modele_pro_non_possede_ne_s_exporte_pas(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(premium=True)
        fakes.cvs.put(me.subject_id)

        response = await client.post(
            f"{API}/cv/me/generated-cvs", json={"template_id": str(template.id)}
        )

        assert response.status_code == 403
        assert reason(response) == "template_locked"

    async def test_l_adaptation_a_une_offre_repond_tailoring_not_available(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(f"{API}/cv/me/generated-cvs", json={"offer_id": str(uuid4())})

        assert response.status_code == 422
        assert reason(response) == "tailoring_not_available"

    async def test_un_format_inconnu_repond_invalid_format(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(
            f"{API}/cv/me/generated-cvs", json={"template_id": str(uuid4()), "formats": ["odt"]}
        )

        assert response.status_code == 422
        assert reason(response) == "invalid_format"
        assert response.json()["error"]["details"]["formats"] == ["odt"]

    async def test_l_historique_se_pagine_et_se_relit(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        a = make_generated(
            me.subject_id,
            docx_key="g/cv.docx",
            ats_docx={
                "score": 80,
                "level": "fair",
                "format": "docx",
                "checks": [],
                "keywords": None,
            },
        )
        b = make_generated(me.subject_id, pdf_key=None, status=ExplanationStatus.READY)
        for g in (a, b, make_generated(uuid4())):
            await fakes.generated.add(g)

        page = await client.get(f"{API}/cv/me/generated-cvs", params={"limit": 1, "offset": 0})
        one = await client.get(f"{API}/cv/me/generated-cvs/{a.id}")

        assert page.status_code == 200
        assert page.json()["total"] == 2
        assert len(page.json()["items"]) == 1
        assert one.json()["ats"]["docx"] == {
            "score": 80,
            "level": "fair",
            "format": "docx",
            "checks": [],
        }
        assert one.json()["fit"] == {"spacing": 1.0, "font_scale": 1.0, "pages": 1}

    async def test_une_limite_hors_bornes_est_refusee(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.get(f"{API}/cv/me/generated-cvs", params={"limit": 0})

        assert response.status_code == 422

    async def test_l_export_d_une_autre_personne_repond_404(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        other = make_generated(uuid4())
        await fakes.generated.add(other)

        read = await client.get(f"{API}/cv/me/generated-cvs/{other.id}")
        deleted = await client.delete(f"{API}/cv/me/generated-cvs/{other.id}")

        assert read.status_code == 404
        assert reason(read) == "generated_cv_not_found"
        assert deleted.status_code == 404

    async def test_la_suppression_efface_l_export(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        mine = make_generated(me.subject_id, pdf_key="g/cv.pdf")
        await fakes.generated.add(mine)
        await storage.put(Bucket.DOCUMENTS, "g/cv.pdf", b"%PDF", content_type="application/pdf")

        response = await client.delete(f"{API}/cv/me/generated-cvs/{mine.id}")

        assert response.status_code == 204
        assert fakes.generated.items == {}
        assert storage.objects == {}


# ---------------------------------------------------------------------- administration


class TestAdministration:
    async def test_sans_jeton_les_routes_internes_repondent_401(
        self, client: AsyncClient, fakes: Fakes, admin: dict[str, str]
    ) -> None:
        missing = await client.get(f"{API}/internal/templates")
        wrong = await client.get(
            f"{API}/internal/templates", headers={"Authorization": "Bearer faux"}
        )

        assert missing.status_code == 401
        assert reason(missing) == "admin_token_invalid"
        assert wrong.status_code == 401

    async def test_sans_jeton_configure_personne_n_entre(
        self, client: AsyncClient, fakes: Fakes
    ) -> None:
        response = await client.get(
            f"{API}/internal/templates", headers={"Authorization": f"Bearer {TOKEN}"}
        )

        assert response.status_code == 401

    async def test_le_depot_d_un_pdf_cree_une_version_en_analyse(
        self, client: AsyncClient, fakes: Fakes, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{API}/internal/templates",
            headers=admin,
            files={"file": ("cv.pdf", b"%PDF-1.7 source", "application/pdf")},
            data={"name": "Meridian Pro", "label": "Méridien", "is_premium": "true"},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["template_name"] == "meridian-pro"
        assert body["status"] == "processing"
        assert body["signed_source_url"].endswith(
            f"v1/source.pdf?expires={body['signed_source_url'].rsplit('=', 1)[1]}"
        )
        assert body["signed_sample_render_url"] is None
        assert body["signed_preview_url"] is None
        assert fakes.jobs.ingestions == [UUID(body["id"])]
        template = fakes.templates.templates[UUID(body["template_id"])]
        assert template.is_premium is True
        assert template.label == "Méridien"

    async def test_un_fichier_qui_n_est_pas_un_pdf_est_refuse(
        self, client: AsyncClient, fakes: Fakes, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{API}/internal/templates",
            headers=admin,
            files={"file": ("cv.png", b"\x89PNG", "image/png")},
            data={"name": "Meridian"},
        )

        assert response.status_code == 422
        assert reason(response) == "template_unreadable"

    async def test_un_nom_trop_court_est_refuse(
        self, client: AsyncClient, fakes: Fakes, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{API}/internal/templates",
            headers=admin,
            files={"file": ("cv.pdf", b"%PDF", "application/pdf")},
            data={"name": "M"},
        )

        assert response.status_code == 422

    async def test_la_revue_liste_chaque_version_de_chaque_modele(
        self, client: AsyncClient, fakes: Fakes, admin: dict[str, str]
    ) -> None:
        template, published = fakes.templates.publish()
        failed = replace(
            make_version(template.id, number=2, status=VersionStatus.FAILED),
            error="aucune rubrique",
        )
        await fakes.templates.save_version(failed)
        await fakes.templates.save(make_template(name="vide"))

        response = await client.get(f"{API}/internal/templates", headers=admin)

        assert response.status_code == 200
        body = response.json()
        assert [v["id"] for v in body] == [str(published.id), str(failed.id)]
        assert body[0]["signed_sample_render_url"].split("?")[0].endswith("v1/sample_render.pdf")
        assert body[0]["signed_sample_docx_url"].split("?")[0].endswith("v1/sample_docx.pdf")
        assert body[0]["signed_preview_url"] is not None
        assert body[1]["status"] == "failed"
        assert body[1]["error"] == "aucune rubrique"
        assert body[1]["signed_sample_render_url"] is None
