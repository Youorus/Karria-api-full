"""Le brouillon : ordre des chapitres, validation, passage, propositions."""

from __future__ import annotations

from uuid import uuid4

import pytest

from doubles import CV_CONTACT as CONTACT
from doubles import CV_EXPERIENCE as EXPERIENCE
from doubles import CV_IDENTITY as IDENTITY
from doubles import CV_NOW as NOW
from doubles import complete_draft
from karria.modules.cv.domain import (
    CHAPTER_ORDER,
    Chapter,
    ChapterStatus,
    ChapterSubmission,
    ChapterSubmissionError,
    ChapterValidationError,
    Draft,
)

pytestmark = pytest.mark.unit


class TestNouveauBrouillon:
    def test_le_premier_chapitre_est_ouvert_et_les_autres_attendent(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        assert draft.current_chapter is Chapter.IDENTITY
        assert draft.chapters[Chapter.IDENTITY].status is ChapterStatus.IN_PROGRESS
        assert all(draft.chapters[c].status is ChapterStatus.PENDING for c in CHAPTER_ORDER[1:])
        assert draft.incomplete_chapters == CHAPTER_ORDER
        assert not draft.is_complete


class TestValidation:
    def test_valider_un_chapitre_simple_ouvre_le_suivant(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        draft.submit(Chapter.IDENTITY, ChapterSubmission(data=IDENTITY), now=NOW)

        assert draft.chapters[Chapter.IDENTITY].status is ChapterStatus.VALIDATED
        assert draft.validated_data(Chapter.IDENTITY) == IDENTITY
        assert draft.current_chapter is Chapter.CONTACT
        assert draft.chapters[Chapter.CONTACT].status is ChapterStatus.IN_PROGRESS

    def test_le_contenu_est_revalide_et_normalise(self) -> None:
        """Le client n'est pas cru sur parole : la forme stockée est celle du schéma."""
        draft = Draft.new(uuid4(), now=NOW)

        draft.submit(
            Chapter.EXPERIENCES,
            ChapterSubmission(items=[{**EXPERIENCE, "start_date": "septembre 2022", "extra": 1}]),
            now=NOW,
        )

        [item] = draft.validated_items(Chapter.EXPERIENCES)
        assert item["start_date"] == "2022-09"
        assert "extra" not in item
        assert item["id"]

    def test_un_contenu_invalide_est_refuse_avec_le_champ_fautif(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        with pytest.raises(ChapterValidationError) as excinfo:
            draft.submit(Chapter.IDENTITY, ChapterSubmission(data={"first_name": "Awa"}), now=NOW)

        assert excinfo.value.details["reason"] == "chapter_invalid"
        assert excinfo.value.details["fields"][0]["field"] == "last_name"
        assert draft.chapters[Chapter.IDENTITY].status is ChapterStatus.IN_PROGRESS

    def test_un_chapitre_en_boucle_exige_des_elements(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        with pytest.raises(ChapterSubmissionError) as excinfo:
            draft.submit(Chapter.EXPERIENCES, ChapterSubmission(data=EXPERIENCE), now=NOW)
        assert excinfo.value.details["reason"] == "chapter_items_expected"

        with pytest.raises(ChapterSubmissionError) as excinfo:
            draft.submit(Chapter.EXPERIENCES, ChapterSubmission(items=[]), now=NOW)
        assert excinfo.value.details["reason"] == "chapter_items_empty"

    def test_un_chapitre_simple_refuse_une_liste(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        with pytest.raises(ChapterSubmissionError) as excinfo:
            draft.submit(Chapter.SUMMARY, ChapterSubmission(items=[{"summary": "x"}]), now=NOW)

        assert excinfo.value.details["reason"] == "chapter_data_expected"

    def test_un_element_invalide_dans_une_boucle_refuse_toute_la_liste(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        with pytest.raises(ChapterValidationError):
            draft.submit(
                Chapter.EDUCATIONS,
                ChapterSubmission(items=[{"degree": "BTS", "school": "Lycée"}, {"degree": "x"}]),
                now=NOW,
            )

        assert draft.validated_items(Chapter.EDUCATIONS) == []

    def test_revalider_un_chapitre_deja_valide_remplace_son_contenu(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)
        draft.submit(Chapter.IDENTITY, ChapterSubmission(data=IDENTITY), now=NOW)

        draft.submit(
            Chapter.IDENTITY, ChapterSubmission(data={**IDENTITY, "first_name": "Aminata"}), now=NOW
        )

        assert draft.validated_data(Chapter.IDENTITY)["first_name"] == "Aminata"  # type: ignore[index]
        assert draft.current_chapter is Chapter.CONTACT


class TestPassage:
    def test_un_chapitre_facultatif_se_passe(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        draft.submit(Chapter.EXPERIENCES, ChapterSubmission(skipped=True), now=NOW)

        assert draft.chapters[Chapter.EXPERIENCES].status is ChapterStatus.SKIPPED
        assert draft.validated_items(Chapter.EXPERIENCES) == []

    @pytest.mark.parametrize("chapter", [Chapter.IDENTITY, Chapter.CONTACT])
    def test_un_chapitre_obligatoire_ne_se_passe_pas(self, chapter: Chapter) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        with pytest.raises(ChapterSubmissionError) as excinfo:
            draft.submit(chapter, ChapterSubmission(skipped=True), now=NOW)

        assert excinfo.value.details["reason"] == "chapter_required"

    def test_passer_efface_une_proposition_anterieure(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)
        draft.propose(Chapter.SUMMARY, data={"summary": "Proposé."}, now=NOW)

        draft.submit(Chapter.SUMMARY, ChapterSubmission(skipped=True), now=NOW)

        assert draft.chapters[Chapter.SUMMARY].data is None


class TestCompletude:
    def test_un_brouillon_complet_na_plus_de_chapitre_en_attente(self) -> None:
        draft = complete_draft()

        assert draft.is_complete
        assert draft.incomplete_chapters == ()
        assert draft.current_chapter is Chapter.PHOTO

    def test_le_chapitre_courant_saute_ce_qui_est_fait(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)
        draft.submit(Chapter.SUMMARY, ChapterSubmission(skipped=True), now=NOW)
        draft.submit(Chapter.IDENTITY, ChapterSubmission(data=IDENTITY), now=NOW)

        assert draft.current_chapter is Chapter.CONTACT
        draft.submit(Chapter.CONTACT, ChapterSubmission(data=CONTACT), now=NOW)
        assert draft.current_chapter is Chapter.EXPERIENCES


class TestPropositions:
    def test_une_proposition_pose_le_chapitre_en_cours_sans_le_valider(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        placed = draft.propose(Chapter.SUMMARY, data={"summary": "Je suis motivée."}, now=NOW)

        assert placed
        state = draft.chapters[Chapter.SUMMARY]
        assert state.status is ChapterStatus.IN_PROGRESS
        assert state.data == {"summary": "Je suis motivée."}
        assert draft.validated_data(Chapter.SUMMARY) is None
        assert not draft.is_complete

    def test_les_elements_invalides_dune_boucle_sont_ignores_un_a_un(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        placed = draft.propose(
            Chapter.EXPERIENCES, items=[EXPERIENCE, {"position": "sans entreprise"}], now=NOW
        )

        assert placed
        assert len(draft.chapters[Chapter.EXPERIENCES].items) == 1

    def test_rien_de_valide_ne_pose_rien(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)

        assert not draft.propose(Chapter.EXPERIENCES, items=[{"position": "x"}], now=NOW)
        assert not draft.propose(Chapter.SUMMARY, data={"summary": ""}, now=NOW)
        assert not draft.propose(Chapter.SUMMARY, data=None, now=NOW)
        assert draft.chapters[Chapter.SUMMARY].status is ChapterStatus.PENDING

    def test_un_chapitre_valide_ou_passe_nest_pas_touche(self) -> None:
        draft = Draft.new(uuid4(), now=NOW)
        draft.submit(Chapter.IDENTITY, ChapterSubmission(data=IDENTITY), now=NOW)

        assert not draft.propose(
            Chapter.IDENTITY, data={**IDENTITY, "first_name": "Autre"}, now=NOW
        )
        assert draft.validated_data(Chapter.IDENTITY) == IDENTITY
