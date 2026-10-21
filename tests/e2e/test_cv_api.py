"""Parcours HTTP complet du module `cv` : le contrat `docs/contrat-api-cv.md`.

Toute la pile est traversée — authentification, validation, routes, cas
d'usage, format d'erreur — avec des dépôts en mémoire et un Gemini factice
substitués par `dependency_overrides`, la couture prévue dans
`api/dependencies.py`. Aucune base, aucun réseau.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from doubles import (
    CV_CONTACT,
    CV_EXPERIENCE,
    CV_IDENTITY,
    FakeClock,
    FakeEnhancer,
    FakeExtractor,
    FakeInspector,
    FakeInterpreter,
    FakeTranscriber,
    InMemoryCvRepository,
    InMemoryDraftRepository,
    InMemoryPhotoRepository,
    sample_image,
)
from karria.modules.cv.api.dependencies import (
    get_audio_transcriber,
    get_chapter_interpreter,
    get_clock,
    get_cv_extractor,
    get_cv_repository,
    get_draft_repository,
    get_photo_repository,
    get_portrait_enhancer,
    get_upload_inspector,
)
from karria.modules.cv.application import GeneratedImage, Interpretation
from karria.modules.cv.domain import (
    Chapter,
    ModelUnavailableError,
    PhotoEnhanceRefusedError,
    PhotoEnhanceUnavailableError,
)
from karria.shared.auth import Principal
from karria.shared.database import NullUnitOfWork
from karria.shared.http.dependencies import get_unit_of_work

pytestmark = pytest.mark.e2e

API = "/api/v1"


class Fakes:
    """Toutes les doublures d'un test, accessibles pour les préparer et les inspecter."""

    def __init__(self) -> None:
        self.drafts = InMemoryDraftRepository()
        self.cvs = InMemoryCvRepository()
        self.photos = InMemoryPhotoRepository()
        self.interpreter = FakeInterpreter()
        self.transcriber = FakeTranscriber(text="Je m'appelle Awa.")
        self.enhancer = FakeEnhancer(result=GeneratedImage(sample_image(400, 500), "image/jpeg"))
        self.extractor = FakeExtractor(
            {Chapter.IDENTITY: CV_IDENTITY, Chapter.EXPERIENCES: [CV_EXPERIENCE]}
        )
        self.inspector = FakeInspector(pages=1, duration=10.0)


@pytest.fixture
def fakes(app: FastAPI) -> Fakes:
    doubles = Fakes()
    app.dependency_overrides.update(
        {
            get_draft_repository: lambda: doubles.drafts,
            get_cv_repository: lambda: doubles.cvs,
            get_photo_repository: lambda: doubles.photos,
            get_chapter_interpreter: lambda: doubles.interpreter,
            get_audio_transcriber: lambda: doubles.transcriber,
            get_portrait_enhancer: lambda: doubles.enhancer,
            get_cv_extractor: lambda: doubles.extractor,
            get_upload_inspector: lambda: doubles.inspector,
            get_clock: FakeClock,
            get_unit_of_work: NullUnitOfWork,
        }
    )
    return doubles


@pytest.fixture
def me(authenticate: Callable[..., Principal]) -> Principal:
    return authenticate()


async def validate(client: AsyncClient, chapter: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.put(f"{API}/cv/draft/chapters/{chapter}", json=body)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def complete_all_but_photo(client: AsyncClient) -> None:
    await client.post(f"{API}/cv/draft")
    await validate(client, "identity", {"data": CV_IDENTITY})
    await validate(client, "contact", {"data": CV_CONTACT})
    await validate(client, "summary", {"data": {"summary": "Je suis souriante."}})
    await validate(client, "experiences", {"items": [CV_EXPERIENCE]})
    await validate(client, "educations", {"skipped": True})
    await validate(
        client,
        "skills",
        {"data": {"skills": [{"name": "Vente", "level": "Avancé"}], "languages": []}},
    )
    await validate(client, "interests", {"skipped": True})


class TestAuthentification:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/cv/me"),
            ("GET", "/cv/draft"),
            ("POST", "/cv/draft"),
            ("POST", "/cv/draft/interpret"),
            ("PUT", "/cv/draft/chapters/identity"),
            ("POST", "/cv/draft/finalize"),
            ("POST", "/cv/photo"),
            ("POST", "/cv/import"),
            ("POST", "/voice/transcribe"),
        ],
    )
    async def test_toute_route_exige_la_session(
        self, client: AsyncClient, fakes: Fakes, method: str, path: str
    ) -> None:
        response = await client.request(method, f"{API}{path}")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"


class TestMonCv:
    async def test_sans_cv_les_sections_sont_vides(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.get(f"{API}/cv/me")

        assert response.status_code == 200
        body = response.json()
        assert body["identity"] is None
        assert body["summary"] is None
        assert body["image_url"] is None
        assert body["experiences"] == []
        assert body["projects"] == []
        UUID(body["id"])


class TestBrouillon:
    async def test_le_brouillon_se_cree_une_fois_puis_se_relit(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        missing = await client.get(f"{API}/cv/draft")
        assert missing.status_code == 404
        assert missing.json()["error"]["details"]["reason"] == "draft_not_found"

        created = await client.post(f"{API}/cv/draft")
        again = await client.post(f"{API}/cv/draft")
        read = await client.get(f"{API}/cv/draft")

        assert created.status_code == 201
        assert again.status_code == 201
        assert read.status_code == 200
        assert created.json()["id"] == again.json()["id"] == read.json()["id"]
        body = read.json()
        assert body["current_chapter"] == "identity"
        assert body["chapters"]["identity"] == {
            "status": "in_progress",
            "data": None,
            "items": None,
        }
        assert body["chapters"]["photo"]["status"] == "pending"

    async def test_valider_un_chapitre_revalide_et_avance(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        await client.post(f"{API}/cv/draft")

        body = await validate(client, "identity", {"data": {**CV_IDENTITY, "first_name": " Awa "}})

        assert body["current_chapter"] == "contact"
        assert body["chapters"]["identity"]["status"] == "validated"
        assert body["chapters"]["identity"]["data"]["first_name"] == "Awa"

    async def test_un_contenu_invalide_est_un_422_qui_nomme_le_champ(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        await client.post(f"{API}/cv/draft")

        response = await client.put(
            f"{API}/cv/draft/chapters/identity", json={"data": {"first_name": "Awa"}}
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "invalid_input"
        assert error["details"]["reason"] == "chapter_invalid"
        assert error["details"]["fields"][0]["field"] == "last_name"

    async def test_une_soumission_ambigue_ou_un_chapitre_inconnu_sont_refuses(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        await client.post(f"{API}/cv/draft")

        ambiguous = await client.put(
            f"{API}/cv/draft/chapters/summary", json={"data": {}, "skipped": True}
        )
        unknown = await client.put(f"{API}/cv/draft/chapters/hobbies", json={"skipped": True})
        required = await client.put(f"{API}/cv/draft/chapters/contact", json={"skipped": True})

        assert ambiguous.status_code == 422
        assert unknown.status_code == 422
        assert required.status_code == 422
        assert required.json()["error"]["details"]["reason"] == "chapter_required"

    async def test_sans_brouillon_valider_est_un_404(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.put(f"{API}/cv/draft/chapters/summary", json={"skipped": True})

        assert response.status_code == 404


class TestInterpretation:
    async def test_un_jet_valide_revient_au_format_du_chapitre(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        fakes.interpreter = FakeInterpreter(
            Interpretation(
                {"position": "Vendeuse", "company": "Zara", "start_date": "sept. 2022"},
                ("end_date",),
                "Tu y es encore ?",
                False,
            )
        )

        response = await client.post(
            f"{API}/cv/draft/interpret",
            json={
                "chapter": "experiences",
                "answer": "vendeuse chez Zara depuis sept 2022",
                "correction_of": None,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["proposal"]["start_date"] == "2022-09"
        assert body["missing"] == ["end_date"]
        assert body["follow_up"] == "Tu y es encore ?"
        assert body["off_topic"] is False

    async def test_une_reponse_vide_ou_trop_longue_est_refusee(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        empty = await client.post(
            f"{API}/cv/draft/interpret", json={"chapter": "summary", "answer": ""}
        )
        long = await client.post(
            f"{API}/cv/draft/interpret", json={"chapter": "summary", "answer": "x" * 4001}
        )

        assert empty.status_code == 422
        assert long.status_code == 422

    async def test_une_panne_du_modele_est_un_503_nomme(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        fakes.interpreter = FakeInterpreter(ModelUnavailableError("HTTP 503"))

        response = await client.post(
            f"{API}/cv/draft/interpret", json={"chapter": "summary", "answer": "je suis motivée"}
        )

        assert response.status_code == 503
        assert response.json()["error"]["details"]["reason"] == "ai_unavailable"


class TestFinalisation:
    async def test_un_brouillon_incomplet_est_un_409_avec_la_liste(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        await client.post(f"{API}/cv/draft")

        response = await client.post(f"{API}/cv/draft/finalize")

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "rule_violation"
        assert error["details"]["reason"] == "draft_incomplete"
        assert error["details"]["chapters"][0] == "identity"

    async def test_le_brouillon_complet_devient_le_cv(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        await complete_all_but_photo(client)
        await validate(client, "photo", {"data": {"image_id": None}})

        response = await client.post(f"{API}/cv/draft/finalize")

        assert response.status_code == 201
        body = response.json()
        assert body["identity"]["first_name"] == "Awa"
        assert body["contact"]["email"] == "awa@example.com"
        assert body["summary"] == "Je suis souriante."
        assert [e["company"] for e in body["experiences"]] == ["Zara"]
        assert body["skills"][0]["level"] == "Avancé"
        assert body["image_url"] is None
        assert (await client.get(f"{API}/cv/me")).json()["id"] == body["id"]
        assert (await client.get(f"{API}/cv/draft")).status_code == 404

    async def test_sans_brouillon_finaliser_est_un_404(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        assert (await client.post(f"{API}/cv/draft/finalize")).status_code == 404


def photo_form(
    enhance: bool, payload: bytes | None = None, content_type: str = "image/jpeg"
) -> dict[str, Any]:
    return {
        "files": {"image": ("photo.jpg", payload or sample_image(640, 480), content_type)},
        "data": {"enhance": json.dumps(enhance)},
    }


class TestPhoto:
    async def test_la_photo_est_rangee_et_rendue_signee(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(f"{API}/cv/photo", **photo_form(False))

        assert response.status_code == 201
        body = response.json()
        assert body["enhanced"] is False
        assert body["image_url"].startswith("https://storage.local/")
        photo = fakes.photos.photos[UUID(body["image_id"])]
        assert photo.user_id == me.subject_id
        assert fakes.enhancer.calls == []

    async def test_lamelioration_rend_limage_du_generateur(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(f"{API}/cv/photo", **photo_form(True))

        assert response.status_code == 201
        assert response.json()["enhanced"] is True
        assert len(fakes.enhancer.calls) == 1

    async def test_un_refus_est_un_422_et_une_panne_un_503(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        fakes.enhancer = FakeEnhancer(error=PhotoEnhanceRefusedError("IMAGE_SAFETY"))
        refused = await client.post(f"{API}/cv/photo", **photo_form(True))
        fakes.enhancer = FakeEnhancer(error=PhotoEnhanceUnavailableError("HTTP 503"))
        down = await client.post(f"{API}/cv/photo", **photo_form(True))

        assert refused.status_code == 422
        assert refused.json()["error"]["details"]["reason"] == "photo_enhance_refused"
        assert down.status_code == 503
        assert down.json()["error"]["details"]["reason"] == "photo_enhance_unavailable"

    async def test_ce_qui_nest_pas_une_image_est_un_422(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(
            f"{API}/cv/photo", **photo_form(False, b"%PDF-1.4 pas une image")
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "storage_payload_rejected"

    async def test_la_photo_retenue_est_servie_dans_le_brouillon_puis_dans_le_cv(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        await complete_all_but_photo(client)
        uploaded = (await client.post(f"{API}/cv/photo", **photo_form(False))).json()

        draft = await validate(client, "photo", {"data": {"image_id": uploaded["image_id"]}})
        cv = (await client.post(f"{API}/cv/draft/finalize")).json()

        photo_data = draft["chapters"]["photo"]["data"]
        assert photo_data["image_id"] == uploaded["image_id"]
        assert photo_data["image_url"].startswith("https://storage.local/")
        assert cv["image_url"].startswith("https://storage.local/")
        assert fakes.photos.photos == {}

    async def test_une_photo_dautrui_est_refusee(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        await client.post(f"{API}/cv/draft")

        response = await client.put(
            f"{API}/cv/draft/chapters/photo", json={"data": {"image_id": str(uuid4())}}
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["fields"][0]["field"] == "image_id"


class TestImport:
    async def test_un_cv_importe_prerempli_le_brouillon_sans_rien_valider(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(
            f"{API}/cv/import", files={"file": ("cv.pdf", b"%PDF-1.4 contenu", "application/pdf")}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["chapters"]["identity"]["status"] == "in_progress"
        assert body["chapters"]["identity"]["data"]["first_name"] == "Awa"
        assert body["chapters"]["experiences"]["items"][0]["company"] == "Zara"
        assert body["current_chapter"] == "identity"
        assert fakes.extractor.calls[0][1] == "application/pdf"

    async def test_un_type_inconnu_est_un_422(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(
            f"{API}/cv/import", files={"file": ("cv.zip", b"PK", "application/zip")}
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "document_type_unsupported"


class TestVoix:
    async def test_lenregistrement_devient_du_texte(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(
            f"{API}/voice/transcribe",
            files={"audio_file": ("reponse.m4a", b"\x00" * 4096, "audio/m4a")},
        )

        assert response.status_code == 200
        assert response.json() == {"text": "Je m'appelle Awa."}
        assert fakes.transcriber.calls == [(4096, "audio/m4a")]

    async def test_un_format_inconnu_est_un_422(
        self, client: AsyncClient, fakes: Fakes, me: Principal
    ) -> None:
        response = await client.post(
            f"{API}/voice/transcribe",
            files={"audio_file": ("video.mp4", b"\x00" * 4096, "video/mp4")},
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["field"] == "audio_file"
