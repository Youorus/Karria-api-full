"""Les traductions modèle ↔ domaine des dépôts, sans base de données.

Les allers-retours réels sur PostgreSQL sont dans
`tests/integration/test_cv_repositories.py`. Ici on vérifie ce qui ne
dépend pas de la base : qu'une ligne devient l'objet attendu et
réciproquement, et qu'une ligne incomplète est tolérée.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from doubles import CV_NOW, complete_draft
from karria.modules.cv.domain import Chapter, ChapterStatus, CurriculumVitae, Experience, Identity
from karria.modules.cv.infrastructure.models import (
    CurriculumVitaeModel,
    CvDraftModel,
    CvPhotoModel,
)
from karria.modules.cv.infrastructure.repositories import (
    _chapters_to_json,
    _cv_from_row,
    _cv_to_values,
    _draft_from_row,
    _photo_from_row,
)

pytestmark = pytest.mark.unit

LATER = datetime(2026, 10, 22, tzinfo=UTC)


class TestBrouillon:
    def test_un_brouillon_fait_laller_retour_a_lidentique(self) -> None:
        draft = complete_draft()
        row = CvDraftModel(
            id=draft.id,
            user_id=draft.user_id,
            chapters=_chapters_to_json(draft),
            created_at=CV_NOW,
            updated_at=LATER,
        )

        loaded = _draft_from_row(row)

        assert loaded.id == draft.id
        assert loaded.user_id == draft.user_id
        assert loaded.chapters == draft.chapters
        assert loaded.created_at == CV_NOW
        assert loaded.updated_at == LATER
        assert loaded.is_complete

    def test_une_ligne_sans_certains_chapitres_les_considere_en_attente(self) -> None:
        row = CvDraftModel(
            id=uuid4(),
            user_id=uuid4(),
            chapters={"identity": {"status": "validated", "data": {"first_name": "A"}}},
            created_at=CV_NOW,
            updated_at=CV_NOW,
        )

        loaded = _draft_from_row(row)

        assert loaded.chapters[Chapter.IDENTITY].status is ChapterStatus.VALIDATED
        assert loaded.chapters[Chapter.IDENTITY].items == []
        assert loaded.chapters[Chapter.CONTACT].status is ChapterStatus.PENDING
        assert loaded.current_chapter is Chapter.CONTACT


class TestCv:
    def test_un_cv_fait_laller_retour_a_lidentique(self) -> None:
        cv = CurriculumVitae.from_draft(
            complete_draft(), cv_id=uuid4(), image_key="users/x/p.jpg", created_at=CV_NOW, now=LATER
        )
        values = _cv_to_values(cv)
        row = CurriculumVitaeModel(
            id=cv.id, user_id=cv.user_id, created_at=CV_NOW, updated_at=LATER, **values
        )

        loaded = _cv_from_row(row)

        assert loaded == cv
        assert values["experiences"][0]["id"] == str(cv.experiences[0].id)

    def test_un_cv_sans_sections_se_relit(self) -> None:
        row = CurriculumVitaeModel(
            id=uuid4(),
            user_id=uuid4(),
            created_at=CV_NOW,
            updated_at=CV_NOW,
            identity=None,
            contact=None,
            summary=None,
            image_key=None,
            experiences=[],
            educations=[],
            skills=[],
            languages=[],
            interests=[],
            projects=[],
        )

        loaded = _cv_from_row(row)

        assert loaded.is_blank
        assert loaded.projects == ()

    def test_les_sections_sont_relues_par_le_schema(self) -> None:
        row = CurriculumVitaeModel(
            id=uuid4(),
            user_id=uuid4(),
            created_at=CV_NOW,
            updated_at=CV_NOW,
            identity={"first_name": "Awa", "last_name": "Diallo"},
            experiences=[{"position": "V", "company": "Z", "start_date": "2022-09"}],
        )

        loaded = _cv_from_row(row)

        assert loaded.identity == Identity(first_name="Awa", last_name="Diallo")
        assert isinstance(loaded.experiences[0], Experience)


def test_une_photo_se_relit() -> None:
    row = CvPhotoModel(
        id=uuid4(), user_id=uuid4(), image_key="users/x/p.jpg", enhanced=True, created_at=CV_NOW
    )

    photo = _photo_from_row(row)

    assert photo.id == row.id
    assert photo.user_id == row.user_id
    assert photo.image_key == "users/x/p.jpg"
    assert photo.enhanced
    assert photo.created_at == CV_NOW
