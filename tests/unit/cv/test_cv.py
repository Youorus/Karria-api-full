"""Le CV : assemblé depuis un brouillon complet, vide sinon."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from doubles import CV_CONTACT as CONTACT
from doubles import CV_IDENTITY as IDENTITY
from doubles import CV_NOW as NOW
from doubles import complete_draft
from karria.modules.cv.domain import (
    Chapter,
    ChapterSubmission,
    CurriculumVitae,
    Draft,
    DraftIncompleteError,
)

pytestmark = pytest.mark.unit


class TestDepuisUnBrouillon:
    def test_les_chapitres_valides_deviennent_les_sections_v1(self) -> None:
        draft = complete_draft()
        cv_id = uuid4()
        created = datetime(2026, 1, 1, tzinfo=UTC)

        cv = CurriculumVitae.from_draft(
            draft, cv_id=cv_id, image_key="users/x/photo.jpg", created_at=created, now=NOW
        )

        assert cv.id == cv_id
        assert cv.user_id == draft.user_id
        assert cv.created_at == created
        assert cv.updated_at == NOW
        assert cv.identity is not None
        assert cv.identity.first_name == "Awa"
        assert cv.contact is not None
        assert cv.contact.email == "awa@example.com"
        assert cv.summary == "Je suis souriante."
        assert cv.image_key == "users/x/photo.jpg"
        assert [e.company for e in cv.experiences] == ["Zara"]
        assert cv.educations == ()
        assert [s.name for s in cv.skills] == ["Vente"]
        assert [lg.language for lg in cv.languages] == ["Anglais"]
        assert [i.name for i in cv.interests] == ["Danse"]
        assert cv.projects == ()
        assert not cv.is_blank

    def test_les_chapitres_passes_donnent_des_sections_vides(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)
        draft.submit(Chapter.IDENTITY, ChapterSubmission(data=IDENTITY), now=NOW)
        draft.submit(Chapter.CONTACT, ChapterSubmission(data=CONTACT), now=NOW)
        for chapter in (
            Chapter.SUMMARY,
            Chapter.EXPERIENCES,
            Chapter.EDUCATIONS,
            Chapter.SKILLS,
            Chapter.INTERESTS,
            Chapter.PHOTO,
        ):
            draft.submit(chapter, ChapterSubmission(skipped=True), now=NOW)

        cv = CurriculumVitae.from_draft(
            draft, cv_id=uuid4(), image_key=None, created_at=NOW, now=NOW
        )

        assert cv.summary is None
        assert cv.image_key is None
        assert cv.skills == ()
        assert cv.languages == ()
        assert cv.interests == ()

    def test_un_brouillon_incomplet_est_refuse_avec_la_liste(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        with pytest.raises(DraftIncompleteError) as excinfo:
            CurriculumVitae.from_draft(
                draft, cv_id=uuid4(), image_key=None, created_at=NOW, now=NOW
            )

        assert excinfo.value.details["reason"] == "draft_incomplete"
        assert excinfo.value.details["chapters"][0] == "identity"


class TestVide:
    def test_le_cv_vide_est_reconnu_comme_a_creer(self) -> None:
        cv = CurriculumVitae.empty(uuid4(), now=NOW)

        assert cv.is_blank
        assert cv.experiences == ()
        assert cv.identity is None
