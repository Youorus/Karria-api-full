"""Parcours HTTP de `/documents` : rendu ancré, export de lettre, envoi par e-mail (`docs/contrat-api-documents.md`)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from tests.unit.documents.fakes import (
    CV_DATA,
    NOW,
    FakeAccountDirectory,
    FakeCvSource,
    FakeEngine,
    FakeLogoFetcher,
    FakeTemplateJobs,
    InMemoryGeneratedCvRepository,
    InMemorySendRepository,
    InMemoryTemplateRepository,
    InMemoryUserTemplateRepository,
    make_generated,
    make_letter_spec,
)

from doubles import FakeClock
from karria.modules.documents.api import dependencies as deps
from karria.modules.documents.domain.sends import DAILY_SEND_LIMIT
from karria.modules.documents.domain.templates import DocumentKind
from karria.shared.auth import Principal
from karria.shared.database import NullUnitOfWork
from karria.shared.http import SlidingWindowLimiter
from karria.shared.http.dependencies import get_unit_of_work
from karria.shared.storage import Bucket, InMemoryObjectStorage

pytestmark = pytest.mark.e2e

API = "/api/v1"
LETTER: dict[str, Any] = {
    "recipient": {"company": "Acme", "city": "Lyon"},
    "subject": "Candidature au poste de vendeuse",
    "paragraphs": [
        {"kind": "interet_entreprise", "text": "Acme m'attire."},
        {"kind": "competences", "text": "Je sais vendre."},
    ],
    "closing": "Cordialement.",
    "sender": {"first_name": "Awa", "last_name": "Diallo", "city": "Montreuil"},
}


class Fakes:
    def __init__(self) -> None:
        self.templates = InMemoryTemplateRepository()
        self.owned = InMemoryUserTemplateRepository()
        self.generated = InMemoryGeneratedCvRepository()
        self.sends = InMemorySendRepository()
        self.accounts = FakeAccountDirectory()
        self.cvs = FakeCvSource()
        self.engine = FakeEngine(
            html='<!doctype html><html><body data-chemin="identity.headline">x</body></html>'
        )
        self.jobs = FakeTemplateJobs()
        self.logos = FakeLogoFetcher()


@pytest.fixture
def fakes(app: FastAPI) -> Iterator[Fakes]:
    doubles = Fakes()
    overrides = {
        deps.get_template_repository: lambda: doubles.templates,
        deps.get_user_template_repository: lambda: doubles.owned,
        deps.get_generated_cv_repository: lambda: doubles.generated,
        deps.get_send_repository: lambda: doubles.sends,
        deps.get_account_directory: lambda: doubles.accounts,
        deps.get_cv_source: lambda: doubles.cvs,
        deps.get_engine: lambda: doubles.engine,
        deps.get_jobs: lambda: doubles.jobs,
        deps.get_logo_fetcher: lambda: doubles.logos,
        deps.get_clock: lambda: FakeClock(NOW),
        get_unit_of_work: NullUnitOfWork,
    }
    app.dependency_overrides.update(overrides)
    yield doubles
    for key in overrides:
        app.dependency_overrides.pop(key, None)


@pytest.fixture
def me(authenticate: Callable[..., Principal]) -> Principal:
    return authenticate()


def reason(response: object) -> str:
    return response.json()["error"]["details"]["reason"]  # type: ignore[attr-defined, no-any-return]


class TestRendu:
    async def test_sans_session_rien_ne_se_rend(self, client: AsyncClient, fakes: Fakes) -> None:
        template, _ = fakes.templates.publish()
        response = await client.post(f"{API}/documents/{template.id}/render", json={"cv": CV_DATA})
        assert response.status_code == 401

    async def test_le_cv_recu_rend_le_html_ancre_les_pages_et_les_avertissements(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        response = await client.post(f"{API}/documents/{template.id}/render", json={"cv": CV_DATA})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["html"].startswith("<!doctype html>")
        assert 'data-chemin="identity.headline"' in body["html"]
        assert body["pages"] == 1
        assert body["warnings"] == []
        [call] = fakes.engine.called("render_html")
        assert call["anchored"] is True
        assert call["view"]["header"]["headline"] == "Vendeuse"

    async def test_un_cv_hors_bornes_est_refuse_par_le_schema(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()
        cv = {**CV_DATA, "experiences": [CV_DATA["experiences"][0]] * 31}
        response = await client.post(f"{API}/documents/{template.id}/render", json={"cv": cv})
        assert response.status_code == 422

    async def test_sans_corps_rien_a_rendre(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()
        response = await client.post(f"{API}/documents/{template.id}/render")
        assert response.status_code == 422
        assert reason(response) == "nothing_to_render"

    async def test_l_exemple_du_modele_se_rend_sans_ancre(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(premium=True)
        fakes.engine.html = "<!doctype html><html>exemple</html>"

        response = await client.post(f"{API}/documents/{template.id}/render?sample=true")

        assert response.status_code == 200, response.text
        assert response.json()["html"] == "<!doctype html><html>exemple</html>"
        assert fakes.engine.called("render_html")[0]["anchored"] is False

    async def test_un_modele_premium_non_possede_repond_template_not_owned(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(premium=True)
        response = await client.post(f"{API}/documents/{template.id}/render", json={"cv": CV_DATA})
        assert response.status_code == 403
        assert reason(response) == "template_not_owned"

    async def test_un_modele_inconnu_repond_404(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(f"{API}/documents/{uuid4()}/render", json={"cv": CV_DATA})
        assert response.status_code == 404
        assert reason(response) == "template_not_found"

    async def test_la_lettre_se_rend_avec_ses_avertissements(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()
        letter = {**LETTER, "recipient": {"company": "Acme", "logo_url": "https://x/l.png"}}

        response = await client.post(
            f"{API}/documents/{template.id}/render", json={"letter": letter}
        )

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == ["logo_unavailable"]
        assert response.json()["letter_style_served"] == "plain"
        assert fakes.engine.called("render_html")[0]["view"]["kind"] == "letter"

    async def test_la_lettre_se_rend_dans_le_style_demande_et_dit_celui_servi(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, version = fakes.templates.publish(letter_spec=make_letter_spec())

        default = await client.post(
            f"{API}/documents/{template.id}/render", json={"letter": LETTER}
        )
        plain = await client.post(
            f"{API}/documents/{template.id}/render",
            json={"letter": LETTER, "letter_style": "plain"},
        )
        cv = await client.post(f"{API}/documents/{template.id}/render", json={"cv": CV_DATA})
        wrong = await client.post(
            f"{API}/documents/{template.id}/render",
            json={"letter": LETTER, "letter_style": "fancy"},
        )

        assert default.json()["letter_style_served"] == "template"
        assert plain.json()["letter_style_served"] == "plain"
        assert cv.json()["letter_style_served"] is None
        assert wrong.status_code == 422
        first, second, _ = fakes.engine.called("render_html")
        assert first["spec"] == version.letter_spec
        assert second["spec"]["source"] == {"producer": "karria"}

    async def test_au_dela_du_quota_de_rendus_la_route_repond_429(
        self, client: AsyncClient, fakes: Fakes, me: Principal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            deps, "_render_limiter", SlidingWindowLimiter(max_hits=2, window_seconds=60)
        )
        template, _ = fakes.templates.publish()
        for _ in range(2):
            ok = await client.post(f"{API}/documents/{template.id}/render", json={"cv": CV_DATA})
            assert ok.status_code == 200

        response = await client.post(f"{API}/documents/{template.id}/render", json={"cv": CV_DATA})

        assert response.status_code == 429
        assert reason(response) == "render_rate_limited"
        assert response.json()["error"]["details"]["retry_after"] >= 1


class TestLettres:
    async def test_l_export_rend_une_lettre_signee_et_nommee(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        template, _ = fakes.templates.publish()
        dossier = str(uuid4())

        response = await client.post(
            f"{API}/documents/letters/export",
            json={"template_id": str(template.id), "letter": LETTER, "dossier_id": dossier},
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["kind"] == "letter"
        assert body["dossier_id"] == dossier
        assert body["company_name"] == "Acme"
        assert "filename=Lettre%20de%20motivation%20-%20Acme.pdf" in body["pdf_url"]
        assert "filename=Lettre%20de%20motivation%20-%20Acme.docx" in body["docx_url"]
        assert body["ats"] == {"explanation": None, "explanation_status": "unavailable"}
        assert body["letter_style"] == "plain"
        assert sum(1 for b, _ in storage.objects if b is Bucket.DOCUMENTS) == 2

    async def test_l_export_garde_le_style_de_lettre_servi(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish(letter_spec=make_letter_spec())

        default = await client.post(
            f"{API}/documents/letters/export",
            json={"template_id": str(template.id), "letter": LETTER},
        )
        plain = await client.post(
            f"{API}/documents/letters/export",
            json={"template_id": str(template.id), "letter": LETTER, "letter_style": "plain"},
        )

        assert default.json()["letter_style"] == "template"
        assert plain.json()["letter_style"] == "plain"
        relu = await client.get(f"{API}/cv/me/generated-cvs/{plain.json()['id']}")
        assert relu.json()["letter_style"] == "plain"

    async def test_la_liste_des_exports_filtre_par_genre_et_dossier(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        dossier = uuid4()
        cv = make_generated(me.subject_id)
        letter = make_generated(me.subject_id, kind=DocumentKind.LETTER, dossier_id=dossier)
        await fakes.generated.add(cv)
        await fakes.generated.add(letter)

        everything = await client.get(f"{API}/cv/me/generated-cvs")
        letters = await client.get(f"{API}/cv/me/generated-cvs", params={"kind": "letter"})
        folder = await client.get(f"{API}/cv/me/generated-cvs", params={"dossier_id": str(dossier)})
        wrong = await client.get(f"{API}/cv/me/generated-cvs", params={"kind": "poeme"})

        assert everything.json()["total"] == 2
        assert [i["id"] for i in letters.json()["items"]] == [str(letter.id)]
        assert [i["kind"] for i in folder.json()["items"]] == ["letter"]
        assert wrong.status_code == 422

    async def test_l_export_de_cv_accepte_un_dossier(
        self, client: AsyncClient, fakes: Fakes, me: Principal, ai_consented: None
    ) -> None:
        template, _ = fakes.templates.publish()
        fakes.cvs.put(me.subject_id)
        dossier = str(uuid4())

        response = await client.post(
            f"{API}/cv/me/generated-cvs",
            json={"template_id": str(template.id), "dossier_id": dossier},
        )

        assert response.status_code == 201, response.text
        assert response.json()["kind"] == "cv"
        assert response.json()["dossier_id"] == dossier


class TestEnvoi:
    async def _export(self, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage) -> UUID:
        generated = make_generated(me.subject_id, pdf_key=f"generated-cvs/{uuid4()}/cv.pdf")
        await storage.put(
            Bucket.DOCUMENTS, generated.pdf_key or "", b"%PDF", content_type="application/pdf"
        )
        await fakes.generated.add(generated)
        return generated.id

    async def test_un_document_part_vers_l_adresse_donnee_et_l_envoi_se_relit(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        generated_id = await self._export(fakes, me, storage)

        response = await client.post(
            f"{API}/documents/exports/{generated_id}/send", json={"to": "rh@acme.fr"}
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "queued"
        assert body["to"] == "rh@acme.fr"
        assert body["generated_ids"] == [str(generated_id)]
        assert body["sent_at"] is None
        assert fakes.jobs.sends == [UUID(body["id"])]

        read = await client.get(f"{API}/documents/sends/{body['id']}")
        assert read.status_code == 200
        assert read.json()["id"] == body["id"]

    async def test_plusieurs_documents_dans_un_seul_envoi_a_mon_adresse(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        first = await self._export(fakes, me, storage)
        second = await self._export(fakes, me, storage)
        fakes.accounts.emails[me.subject_id] = "moi@exemple.fr"

        response = await client.post(
            f"{API}/documents/exports/send",
            json={"generated_ids": [str(first), str(second)], "to": None},
        )

        assert response.status_code == 202, response.text
        assert response.json()["to"] == "moi@exemple.fr"
        assert len(response.json()["generated_ids"]) == 2

    async def test_sans_adresse_de_compte_le_refus_est_explicite(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        generated_id = await self._export(fakes, me, storage)
        response = await client.post(f"{API}/documents/exports/{generated_id}/send")
        assert response.status_code == 409
        assert reason(response) == "account_email_unknown"

    async def test_une_adresse_invalide_et_un_export_d_autrui(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        generated_id = await self._export(fakes, me, storage)
        other = make_generated(uuid4())
        await fakes.generated.add(other)

        invalid = await client.post(
            f"{API}/documents/exports/{generated_id}/send", json={"to": "nope"}
        )
        foreign = await client.post(
            f"{API}/documents/exports/{other.id}/send", json={"to": "a@b.fr"}
        )
        unknown = await client.get(f"{API}/documents/sends/{uuid4()}")

        assert invalid.status_code == 422
        assert reason(invalid) == "recipient_invalid"
        assert foreign.status_code == 404
        assert reason(foreign) == "generated_cv_not_found"
        assert unknown.status_code == 404
        assert reason(unknown) == "send_not_found"

    async def test_le_sixieme_envoi_du_jour_repond_send_quota_exceeded(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        generated_id = await self._export(fakes, me, storage)
        fakes.accounts.emails[me.subject_id] = "moi@exemple.fr"
        for _ in range(DAILY_SEND_LIMIT):
            ok = await client.post(f"{API}/documents/exports/{generated_id}/send")
            assert ok.status_code == 202

        response = await client.post(f"{API}/documents/exports/{generated_id}/send")

        assert response.status_code == 429
        assert reason(response) == "send_quota_exceeded"
        assert response.json()["error"]["details"]["retry_after"] > 0
        assert "scope" not in response.json()["error"]["details"]

    async def test_le_troisieme_envoi_du_jour_vers_la_meme_adresse_tierce_est_refuse(
        self, client: AsyncClient, fakes: Fakes, me: Principal, storage: InMemoryObjectStorage
    ) -> None:
        generated_id = await self._export(fakes, me, storage)
        for _ in range(2):
            ok = await client.post(
                f"{API}/documents/exports/{generated_id}/send", json={"to": "rh@acme.fr"}
            )
            assert ok.status_code == 202

        response = await client.post(
            f"{API}/documents/exports/{generated_id}/send", json={"to": "rh@acme.fr"}
        )

        assert response.status_code == 429
        assert reason(response) == "send_quota_exceeded"

    async def test_l_origine_est_lue_derriere_le_mandataire_de_confiance(
        self,
        client: AsyncClient,
        fakes: Fakes,
        me: Principal,
        storage: InMemoryObjectStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            deps, "_send_origin_limiter", SlidingWindowLimiter(max_hits=1, window_seconds=60)
        )
        generated_id = await self._export(fakes, me, storage)
        # Traefik (1 saut) ajoute l'adresse réelle à droite : ce que le client écrit à gauche ne compte pas.
        first = await client.post(
            f"{API}/documents/exports/{generated_id}/send",
            json={"to": "a@b.fr"},
            headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4"},
        )
        forged = await client.post(
            f"{API}/documents/exports/{generated_id}/send",
            json={"to": "c@d.fr"},
            headers={"X-Forwarded-For": "8.8.8.8, 1.2.3.4"},
        )
        assert first.status_code == 202
        assert forged.status_code == 429

    async def test_un_lot_vide_ou_trop_grand_est_refuse_par_le_schema(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        empty = await client.post(f"{API}/documents/exports/send", json={"generated_ids": []})
        too_many = await client.post(
            f"{API}/documents/exports/send",
            json={"generated_ids": [str(uuid4()) for _ in range(5)]},
        )
        assert empty.status_code == 422
        assert too_many.status_code == 422


class TestExportDuCvRecu:
    async def test_l_export_rend_le_cv_recu_rattache_au_dossier(
        self, client: AsyncClient, fakes: Fakes, me: Principal, ai_consented: None
    ) -> None:
        template, _ = fakes.templates.publish()
        dossier = str(uuid4())
        adapted = {
            **CV_DATA,
            "identity": {"first_name": "Awa", "last_name": "Diallo", "headline": "Cheffe"},
        }

        response = await client.post(
            f"{API}/cv/me/generated-cvs",
            json={"template_id": str(template.id), "dossier_id": dossier, "cv": adapted},
        )

        assert response.status_code == 201, response.text
        assert response.json()["dossier_id"] == dossier
        assert fakes.engine.called("render_pdf")[0]["view"]["header"]["headline"] == "Cheffe"

    async def test_un_cv_recu_hors_bornes_est_refuse(
        self, client: AsyncClient, fakes: Fakes, me: Principal, ai_consented: None
    ) -> None:
        template, _ = fakes.templates.publish()
        cv = {**CV_DATA, "skills": [{"name": "x"}] * 61}
        response = await client.post(
            f"{API}/cv/me/generated-cvs", json={"template_id": str(template.id), "cv": cv}
        )
        assert response.status_code == 422

    async def test_sans_cv_recu_ni_cv_de_base_l_export_repond_cv_missing(
        self, client: AsyncClient, fakes: Fakes, me: Principal, ai_consented: None
    ) -> None:
        template, _ = fakes.templates.publish()
        response = await client.post(
            f"{API}/cv/me/generated-cvs", json={"template_id": str(template.id)}
        )
        assert response.status_code == 409
        assert reason(response) == "cv_missing"


class TestCouleurPrincipale:
    """ADR-0034 : `primary_color` au rendu et à l'export."""

    async def test_le_rendu_prend_la_couleur_choisie_et_refuse_un_code_invalide(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        template, _ = fakes.templates.publish()

        ok = await client.post(
            f"{API}/documents/{template.id}/render",
            json={"cv": CV_DATA, "primary_color": "7b1e2b"},
        )
        bad = await client.post(
            f"{API}/documents/{template.id}/render",
            json={"cv": CV_DATA, "primary_color": "#12345"},
        )

        assert ok.status_code == 200, ok.text
        [call] = fakes.engine.called("render_html")
        assert call["spec"]["styles"]["section_title"]["color"] == "#7B1E2B"
        assert bad.status_code == 422

    async def test_l_export_retient_la_couleur_et_la_liste_la_rend(
        self, client: AsyncClient, fakes: Fakes, me: Principal, ai_consented: None
    ) -> None:
        template, _ = fakes.templates.publish()

        response = await client.post(
            f"{API}/cv/me/generated-cvs",
            json={"template_id": str(template.id), "cv": CV_DATA, "primary_color": "#2E7D32"},
        )

        assert response.status_code == 201, response.text
        assert response.json()["primary_color"] == "#2E7D32"
        listed = await client.get(f"{API}/cv/me/generated-cvs?kind=cv")
        assert listed.json()["items"][0]["primary_color"] == "#2E7D32"
        assert fakes.engine.called("render_pdf")[0]["spec"]["styles"]["section_title"]["color"] == (
            "#2E7D32"
        )
