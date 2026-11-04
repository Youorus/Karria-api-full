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
    ChapterSubmissionError,
    ChapterValidationError,
    Contact,
    CurriculumVitae,
    Draft,
    DraftIncompleteError,
    Identity,
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


def rempli() -> CurriculumVitae:
    return CurriculumVitae(
        id=uuid4(),
        user_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
        identity=Identity.model_validate(IDENTITY),
        contact=Contact.model_validate(CONTACT),
        summary="Je suis souriante.",
        image_key="k.jpg",
    )


LATER = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


class TestEditionDirecte:
    def test_une_section_simple_est_remplacee_et_revalidee(self) -> None:
        cv = rempli().with_chapter(
            Chapter.IDENTITY,
            ChapterSubmission(data={"first_name": " Awa ", "last_name": "Diallo", "headline": ""}),
            now=LATER,
        )

        assert cv.identity is not None
        assert cv.identity.first_name == "Awa"
        assert cv.identity.headline is None
        assert cv.updated_at == LATER
        assert cv.image_key == "k.jpg"

    def test_les_listes_se_remplacent_dans_lordre_donne_meme_vides(self) -> None:
        cv = rempli().with_chapter(
            Chapter.EXPERIENCES,
            ChapterSubmission(
                items=[
                    {"position": "B", "company": "Deux"},
                    {"position": "A", "company": "Une", "start_date": "2020-01"},
                ]
            ),
            now=LATER,
        )
        assert [e.company for e in cv.experiences] == ["Deux", "Une"]

        vide = cv.with_chapter(Chapter.EXPERIENCES, ChapterSubmission(items=[]), now=LATER)
        assert vide.experiences == ()

        formations = cv.with_chapter(
            Chapter.EDUCATIONS,
            ChapterSubmission(items=[{"degree": "BTS", "school": "Lycée"}]),
            now=LATER,
        )
        assert formations.educations[0].school == "Lycée"

    def test_competences_langues_et_interets(self) -> None:
        cv = rempli().with_chapter(
            Chapter.SKILLS,
            ChapterSubmission(
                data={
                    "skills": [{"name": "Vente", "level": "Expert"}],
                    "languages": [{"language": "Anglais", "level": "B2"}],
                }
            ),
            now=LATER,
        )
        assert [s.name for s in cv.skills] == ["Vente"]
        assert [lang.language for lang in cv.languages] == ["Anglais"]

        cv = cv.with_chapter(
            Chapter.INTERESTS,
            ChapterSubmission(data={"interests": [{"name": "Danse", "category": "Sport"}]}),
            now=LATER,
        )
        assert cv.interests[0].name == "Danse"

        cv = cv.with_chapter(
            Chapter.SUMMARY, ChapterSubmission(data={"summary": "Moi."}), now=LATER
        )
        assert cv.summary == "Moi."

    def test_skipped_vide_une_section_facultative_jamais_lidentite(self) -> None:
        cv = rempli()
        assert (
            cv.with_chapter(Chapter.SUMMARY, ChapterSubmission(skipped=True), now=LATER).summary
            is None
        )
        assert (
            cv.with_chapter(Chapter.SKILLS, ChapterSubmission(skipped=True), now=LATER).skills == ()
        )
        assert (
            cv.with_chapter(Chapter.INTERESTS, ChapterSubmission(skipped=True), now=LATER).interests
            == ()
        )
        assert (
            cv.with_chapter(
                Chapter.EDUCATIONS, ChapterSubmission(skipped=True), now=LATER
            ).educations
            == ()
        )
        for chapter in (Chapter.IDENTITY, Chapter.CONTACT):
            with pytest.raises(ChapterSubmissionError) as excinfo:
                cv.with_chapter(chapter, ChapterSubmission(skipped=True), now=LATER)
            assert excinfo.value.details["reason"] == "chapter_required"

    def test_les_mauvaises_formes_et_le_contenu_invalide_sont_refuses(self) -> None:
        cv = rempli()
        with pytest.raises(ChapterSubmissionError) as excinfo:
            cv.with_chapter(Chapter.EXPERIENCES, ChapterSubmission(data={}), now=LATER)
        assert excinfo.value.details["reason"] == "chapter_items_expected"
        with pytest.raises(ChapterSubmissionError) as excinfo:
            cv.with_chapter(Chapter.SUMMARY, ChapterSubmission(items=[]), now=LATER)
        assert excinfo.value.details["reason"] == "chapter_data_expected"
        with pytest.raises(ChapterSubmissionError):
            cv.with_chapter(Chapter.PHOTO, ChapterSubmission(data={"image_id": None}), now=LATER)
        with pytest.raises(ChapterValidationError) as invalid:
            cv.with_chapter(
                Chapter.CONTACT, ChapterSubmission(data={"email": None, "phone": None}), now=LATER
            )
        assert invalid.value.details["chapter"] == "contact"

    def test_la_photo_se_pose_par_sa_cle(self) -> None:
        cv = rempli().with_image_key(None, now=LATER)
        assert cv.image_key is None
        assert cv.with_image_key("n.jpg", now=LATER).image_key == "n.jpg"


class TestDepuisUneExtraction:
    def test_chaque_element_est_revalide_et_ce_qui_tombe_est_compte(self) -> None:
        extracted = {
            Chapter.IDENTITY: {"first_name": "Awa", "last_name": "Diallo"},
            Chapter.CONTACT: {"city": "Paris"},  # ni e-mail ni téléphone : refusé
            Chapter.EXPERIENCES: [
                {"position": "Vendeuse", "company": "Zara", "start_date": "2022-09"},
                {"position": "sans entreprise"},
            ],
            Chapter.EDUCATIONS: "pas une liste",
            Chapter.SKILLS: {"skills": [{"name": "Vente", "level": "Expert"}], "languages": []},
        }
        user = uuid4()

        assembly = CurriculumVitae.from_extraction(
            extracted, user_id=user, cv_id=uuid4(), image_key="k.jpg", created_at=NOW, now=NOW
        )

        cv = assembly.cv
        assert cv.user_id == user
        assert cv.image_key == "k.jpg"
        assert cv.contact is None
        assert [e.company for e in cv.experiences] == ["Zara"]
        assert cv.educations == ()
        assert assembly.kept == {
            "identity": 1,
            "contact": 0,
            "summary": 0,
            "skills": 1,
            "interests": 0,
            "experiences": 1,
            "educations": 0,
            "languages": 0,
        }
        assert assembly.discarded == {"contact": 1, "experiences": 1}
        assert cv.missing_fields == (
            "contact.phone",
            "contact.email",
            "contact.city",
            "identity.headline",
            "summary",
            "educations",
        )

    def test_une_extraction_vide_donne_un_cv_a_creer(self) -> None:
        assembly = CurriculumVitae.from_extraction(
            {}, user_id=uuid4(), cv_id=uuid4(), image_key=None, created_at=NOW, now=NOW
        )
        assert assembly.cv.is_blank
        assert assembly.discarded == {}
        assert assembly.cv.missing_fields[:3] == ("contact.phone", "contact.email", "contact.city")
        assert "identity" in assembly.cv.missing_fields


class TestAVerifier:
    def test_rien_ne_manque_a_un_cv_complet(self) -> None:
        cv = CurriculumVitae.from_extraction(
            {
                Chapter.IDENTITY: IDENTITY,
                Chapter.CONTACT: {**CONTACT, "phone": "06"},
                Chapter.SUMMARY: {"summary": "Moi."},
                Chapter.EXPERIENCES: [{"position": "V", "company": "Z", "start_date": "2020"}],
                Chapter.EDUCATIONS: [{"degree": "BTS", "school": "L", "end_date": "2019"}],
                Chapter.SKILLS: {"skills": [{"name": "Vente", "level": "Expert"}]},
            },
            user_id=uuid4(),
            cv_id=uuid4(),
            image_key=None,
            created_at=NOW,
            now=NOW,
        ).cv
        assert cv.missing_fields == ()

    def test_les_dates_absentes_se_signalent(self) -> None:
        cv = CurriculumVitae.from_extraction(
            {
                Chapter.IDENTITY: IDENTITY,
                Chapter.CONTACT: {**CONTACT, "phone": "06"},
                Chapter.SUMMARY: {"summary": "Moi."},
                Chapter.EXPERIENCES: [{"position": "V", "company": "Z"}],
                Chapter.EDUCATIONS: [{"degree": "BTS", "school": "L"}],
                Chapter.SKILLS: {"skills": [{"name": "Vente", "level": "Expert"}]},
            },
            user_id=uuid4(),
            cv_id=uuid4(),
            image_key=None,
            created_at=NOW,
            now=NOW,
        ).cv
        assert cv.missing_fields == ("experiences.dates", "educations.dates")


class TestExtractionIndulgente:
    def test_un_telephone_double_ne_fait_pas_tomber_le_contact(self) -> None:
        assembly = CurriculumVitae.from_extraction(
            {
                Chapter.CONTACT: {
                    "email": "awa@example.com",
                    "phone": "06 12 34 56 78 (+33 7 59 65 00 05)",
                    "city": "Paris",
                    "links": [{"label": "LinkedIn", "url": "linkedin.com/in/awa"}, {"label": "x"}],
                }
            },
            user_id=uuid4(),
            cv_id=uuid4(),
            image_key=None,
            created_at=NOW,
            now=NOW,
        )
        contact = assembly.cv.contact
        assert contact is not None
        assert contact.phone == "06 12 34 56 78"
        assert contact.city == "Paris"
        assert [link.label for link in contact.links] == ["LinkedIn"]
        assert assembly.kept["contact"] == 1
        assert "contact" not in assembly.discarded

    def test_un_contact_sans_canal_reste_absent(self) -> None:
        assembly = CurriculumVitae.from_extraction(
            {Chapter.CONTACT: {"city": "Paris", "phone": "aucun", "links": "non"}},
            user_id=uuid4(),
            cv_id=uuid4(),
            image_key=None,
            created_at=NOW,
            now=NOW,
        )
        assert assembly.cv.contact is None
        assert assembly.discarded == {"contact": 1}

    def test_les_competences_et_interets_se_jugent_un_par_un(self) -> None:
        assembly = CurriculumVitae.from_extraction(
            {
                Chapter.SKILLS: {
                    "skills": [
                        {"name": "Vente", "level": "Expert"},
                        {"name": "", "level": "Expert"},
                    ],
                    "languages": [{"language": "Anglais", "level": "B2"}, {"language": "Oui"}],
                },
                Chapter.INTERESTS: {"interests": [{"name": "Danse"}, {"nom": "sans nom"}]},
            },
            user_id=uuid4(),
            cv_id=uuid4(),
            image_key=None,
            created_at=NOW,
            now=NOW,
        )
        assert [s.name for s in assembly.cv.skills] == ["Vente"]
        assert [lang.language for lang in assembly.cv.languages] == ["Anglais"]
        assert [i.name for i in assembly.cv.interests] == ["Danse"]
        assert assembly.kept["skills"] == 1
        assert assembly.kept["languages"] == 1
        assert assembly.discarded == {"skills": 2, "interests": 1}

    def test_des_formes_inattendues_donnent_des_sections_vides(self) -> None:
        assembly = CurriculumVitae.from_extraction(
            {Chapter.SKILLS: "liste", Chapter.INTERESTS: ["Danse"], Chapter.CONTACT: "x"},
            user_id=uuid4(),
            cv_id=uuid4(),
            image_key=None,
            created_at=NOW,
            now=NOW,
        )
        assert assembly.cv.skills == ()
        assert assembly.cv.interests == ()
        assert assembly.cv.contact is None
