"""Le branchement de `documents` sur le module `cv` (`bootstrap/wiring.py`)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from doubles import CV_NOW, FakeClock, InMemoryCvRepository, complete_draft
from karria.bootstrap.app import create_app
from karria.bootstrap.wiring import CvModuleSource, cv_as_json, cv_source_from_cv
from karria.modules.cv.application import GetMyCv
from karria.modules.cv.domain import CurriculumVitae
from karria.modules.documents import get_cv_source
from karria.shared.config import AppSettings, Environment, Settings

pytestmark = pytest.mark.unit


def a_cv(user_id: object = None) -> CurriculumVitae:
    draft = complete_draft(user_id)  # type: ignore[arg-type]
    return CurriculumVitae.from_draft(
        draft, cv_id=uuid4(), image_key="cv/photo.jpg", created_at=CV_NOW, now=CV_NOW
    )


def test_la_source_de_cv_de_documents_est_branchee_sur_cv() -> None:
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))

    assert app.dependency_overrides[get_cv_source] is cv_source_from_cv


def test_le_cv_est_rendu_sous_la_forme_du_contrat_sans_la_photo() -> None:
    data = cv_as_json(a_cv())

    assert data["identity"] == {
        "first_name": "Awa",
        "last_name": "Diallo",
        "headline": "Conseillère de vente",
    }
    assert data["contact"] == {
        "email": "awa@example.com",
        "phone": None,
        "city": "Montreuil",
        "links": [],
    }
    assert data["summary"] == "Je suis souriante."
    assert isinstance(data["experiences"], list)
    assert data["experiences"][0]["position"] == "Vendeuse"
    assert data["educations"] == []
    assert [s["name"] for s in data["skills"]] == ["Vente"]  # type: ignore[union-attr]
    assert [lg["language"] for lg in data["languages"]] == ["Anglais"]  # type: ignore[union-attr]
    assert [i["name"] for i in data["interests"]] == ["Danse"]  # type: ignore[union-attr]
    assert data["projects"] == []
    assert "image_key" not in data


def test_un_cv_sans_identite_ni_contact_les_rend_nuls() -> None:
    data = cv_as_json(CurriculumVitae.empty(uuid4(), now=CV_NOW))

    assert data["identity"] is None
    assert data["contact"] is None


class TestCvModuleSource:
    async def test_le_cv_finalise_devient_un_instantane_pour_documents(self) -> None:
        cvs = InMemoryCvRepository()
        cv = a_cv()
        await cvs.save(cv)
        source = cv_source_from_cv(GetMyCv(cvs, FakeClock()))

        snapshot = await source.snapshot(cv.user_id)

        assert isinstance(source, CvModuleSource)
        assert snapshot is not None
        assert snapshot.data == cv_as_json(cv)
        assert snapshot.image_key == "cv/photo.jpg"
        assert snapshot.updated_at == CV_NOW

    async def test_un_cv_encore_vide_ne_donne_rien_a_rendre(self) -> None:
        source = CvModuleSource(GetMyCv(InMemoryCvRepository(), FakeClock()))

        assert await source.snapshot(uuid4()) is None
