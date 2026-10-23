"""Le tour de dialogue : le modèle propose, le domaine décide."""

from __future__ import annotations

import pytest

from karria.modules.offers.domain import (
    DECLINE_REPLY,
    DialogueProposal,
    DialogueStatus,
    Intent,
    LocationCriteria,
    SearchCriteria,
    announce_results,
    decide,
    merge,
)
from karria.modules.offers.domain.dialogue import (
    REPLY_MAX_LENGTH,
    SUGGESTION_MAX_LENGTH,
    SUGGESTIONS_MAX,
    clean_suggestions,
    fallback_question,
)
from offers.fakes import make_criteria

pytestmark = pytest.mark.unit

LYON = LocationCriteria(label="Lyon", latitude=45.76, longitude=4.83)


def criteria(**fields: object) -> SearchCriteria:
    base: dict[str, object] = {
        "query": "serveur à Lyon",
        "job_titles": ["serveur"],
        "location": LYON,
    }
    base.update(fields)
    return SearchCriteria.model_validate(base)


class TestDecision:
    def test_des_criteres_complets_lancent_la_recherche(self) -> None:
        outcome = decide(DialogueProposal(reply="  C'est noté.  ", criteria=criteria()), None)
        assert outcome.status is DialogueStatus.SEARCH
        assert outcome.reply == "C'est noté."

    def test_hors_sujet_on_repond_sans_toucher_aux_criteres(self) -> None:
        previous = criteria()
        proposal = DialogueProposal(reply="Parlons emploi.", criteria=criteria(), off_topic=True)
        outcome = decide(proposal, previous)
        assert outcome.status is DialogueStatus.CHAT
        assert outcome.criteria is previous

    def test_sans_criteres_cest_une_simple_reponse(self) -> None:
        assert (
            decide(DialogueProposal(reply="Bonjour", criteria=None), None).status
            is DialogueStatus.CHAT
        )

    def test_il_manque_le_lieu_on_le_demande(self) -> None:
        outcome = decide(DialogueProposal(reply="", criteria=criteria(location=None)), None)
        assert outcome.status is DialogueStatus.CLARIFY
        assert outcome.reply == fallback_question(criteria(location=None))
        assert "ville" in outcome.reply

    def test_la_question_du_modele_est_gardee(self) -> None:
        outcome = decide(DialogueProposal(reply="Où ?", criteria=criteria(location=None)), None)
        assert outcome.reply == "Où ?"

    def test_sans_metier_on_demande_le_metier(self) -> None:
        assert "métier" in fallback_question(criteria(job_titles=[]))

    def test_la_reponse_est_bornee(self) -> None:
        outcome = decide(DialogueProposal(reply="a" * 2_000, criteria=None), None)
        assert len(outcome.reply) == REPLY_MAX_LENGTH

    def test_les_suggestions_sont_nettoyees(self) -> None:
        proposal = DialogueProposal(
            reply="ok", criteria=criteria(), suggestions=("En  CDI", "en cdi", "", "x" * 80)
        )
        assert decide(proposal, None).suggestions == ("En CDI",)


class TestSuggestions:
    def test_au_plus_quelques_courtes(self) -> None:
        found = clean_suggestions(tuple(f"Option {i}" for i in range(10)))
        assert len(found) == SUGGESTIONS_MAX
        assert all(len(text) <= SUGGESTION_MAX_LENGTH for text in found)


class TestFusion:
    def test_sans_criteres_precedents_la_proposition_vaut(self) -> None:
        proposed = criteria()
        assert merge(None, proposed) is proposed

    def test_les_trous_sont_combles_par_le_tour_precedent(self) -> None:
        previous = criteria(keywords=["brasserie"], semantic_query="Serveur en brasserie")
        merged = merge(previous, criteria(job_titles=[], location=None, contract_types=["cdi"]))
        assert merged.job_titles == ["serveur"]
        assert merged.keywords == ["brasserie"]
        assert merged.semantic_query == "Serveur en brasserie"
        assert merged.location == LYON
        assert merged.contract_types == ["cdi"]

    def test_ce_que_dit_la_proposition_lemporte(self) -> None:
        previous = criteria(keywords=["brasserie"], semantic_query="Serveur en brasserie")
        proposed = criteria(keywords=["gastronomique"], semantic_query="Serveur gastronomique")
        merged = merge(previous, proposed)
        assert merged.keywords == ["gastronomique"]
        assert merged.semantic_query == "Serveur gastronomique"

    def test_toute_la_france_dite_maintenant_lemporte_sur_la_ville(self) -> None:
        merged = merge(criteria(), criteria(location=None, whole_france=True))
        assert merged.location is None
        assert merged.whole_france

    def test_un_precedent_sans_lieu_ne_comble_rien(self) -> None:
        merged = merge(criteria(location=None, whole_france=True), criteria(location=None))
        assert merged.location is None
        assert merged.whole_france


class TestAnnonce:
    def test_aucun_resultat(self) -> None:
        assert "aucune offre" in announce_results(0)

    def test_singulier_et_pluriel(self) -> None:
        assert announce_results(1) == "J'ai trouvé une offre, elle est dans ton fil."
        assert announce_results(12) == "J'ai trouvé 12 offres, elles sont dans ton fil."


class TestIntention:
    def test_l_intention_dite_par_le_modele_prime(self) -> None:
        proposal = DialogueProposal(reply="x", criteria=None, intent=Intent.APP_HELP)
        assert proposal.resolved_intent is Intent.APP_HELP

    def test_sans_intention_les_drapeaux_la_deduisent(self) -> None:
        assert (
            DialogueProposal(reply="x", criteria=None, off_topic=True).resolved_intent
            is Intent.OFF_TOPIC
        )
        assert DialogueProposal(reply="x", criteria=None).resolved_intent is Intent.SMALL_TALK
        assert (
            DialogueProposal(reply="x", criteria=make_criteria()).resolved_intent is Intent.SEARCH
        )

    def test_parler_metier_ou_app_repond_sans_toucher_aux_criteres(self) -> None:
        previous = make_criteria()
        for intent in (Intent.CAREER_TALK, Intent.APP_HELP, Intent.SMALL_TALK):
            outcome = decide(
                DialogueProposal(
                    reply="Voilà.", criteria=make_criteria(job_titles=["x"]), intent=intent
                ),
                previous,
            )
            assert outcome.status is DialogueStatus.CHAT
            assert outcome.criteria == previous
            assert outcome.intent is intent

    def test_un_hors_sujet_sans_reponse_recoit_le_refus_gabarit(self) -> None:
        outcome = decide(DialogueProposal(reply="  ", criteria=None, intent=Intent.OFF_TOPIC), None)
        assert outcome.reply == DECLINE_REPLY
        assert outcome.intent is Intent.OFF_TOPIC

    def test_une_recherche_garde_son_intention(self) -> None:
        outcome = decide(
            DialogueProposal(reply="Ok", criteria=make_criteria(), intent=Intent.REFINE), None
        )
        assert (outcome.status, outcome.intent) == (DialogueStatus.SEARCH, Intent.REFINE)

    def test_la_question_de_repli_tutoie(self) -> None:
        outcome = decide(DialogueProposal(reply="", criteria=make_criteria(job_titles=[])), None)
        assert "cherches-tu" in outcome.reply
