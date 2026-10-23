"""La porte d'intitulé : racinisation grossière et symétrique, phrases entières."""

from __future__ import annotations

import pytest

from karria.modules.offers.domain import TitleMatcher, title_tokens
from karria.modules.offers.domain.titles import stem

pytestmark = pytest.mark.unit


class TestRacines:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("serveuse", "serveur"),
            ("animatrice", "animateur"),
            ("electricienne", "electricien"),
            ("infirmieres", "infirmier"),
            ("bouchere", "boucher"),
            ("sportives", "sportif"),
            ("commerciales", "commercial"),
            ("generaux", "general"),
            ("specialisee", "specialis"),
            ("specialise", "specialis"),
            ("caristes", "carist"),
            ("bus", "bus"),
            ("ere", "ere"),
        ],
    )
    def test_le_feminin_et_le_pluriel_se_ramenent_au_masculin_singulier(
        self, word: str, expected: str
    ) -> None:
        assert stem(word) == expected


class TestMots:
    def test_les_mots_vides_et_les_marques_inclusives_disparaissent(self) -> None:
        assert title_tokens("Serveur(euse) polyvalent(te) H/F") == {"serveur", "polyvalent"}

    def test_accents_casse_et_ponctuation_sont_plies(self) -> None:
        assert title_tokens("ÉLECTRICIENNE — Bâtiment") == {"electricien", "batiment"}

    def test_le_feminin_rejoint_le_masculin(self) -> None:
        assert title_tokens("Éducatrice spécialisée") == title_tokens("Educateur spécialisé")

    def test_l_ecriture_inclusive_a_point_median(self) -> None:
        assert title_tokens("Chef·Fe de Rang H/F") == {"chef", "rang"}


class TestPorte:
    def test_une_phrase_exige_tous_ses_mots(self) -> None:
        matcher = TitleMatcher.of(["serveuse", "chef de rang"])

        assert matcher.match("Chef de partie H/F") is None
        assert matcher.match("Serveur(euse) en brasserie") == 0
        assert matcher.match("Chef de rang - Palace") == 1

    def test_le_premier_intitule_qui_correspond_l_emporte(self) -> None:
        matcher = TitleMatcher.of(["data analyst", "analyste"])

        assert matcher.match("Business Analyst Data H/F") == 0

    def test_un_intitule_fait_de_mots_vides_est_ignore(self) -> None:
        matcher = TitleMatcher.of(["H/F", "de la", "plombier"])

        assert len(matcher.phrases) == 1
        assert matcher.match("Plombière chauffagiste") == 0

    def test_sans_intitule_rien_ne_passe(self) -> None:
        assert TitleMatcher.of([]).match("Serveur") is None


class TestMotsDeNiveau:
    def test_un_niveau_absent_de_la_demande_elimine(self) -> None:
        matcher = TitleMatcher.of(["cuisinier", "cuisinière"])
        assert matcher.match("Cuisinier / Cuisinière (H/F)") == 0
        assert matcher.match("Chef cuisinier / Cheffe cuisinière") is None
        assert matcher.match("Cuisinier chef de partie h/f") is None
        assert matcher.match("Second de cuisine - Cuisinier") is None
        assert matcher.match("Aide cuisinier H/F") is None
        assert matcher.match("Cuisinier stagiaire") is None

    def test_un_niveau_demande_est_admis(self) -> None:
        matcher = TitleMatcher.of(["chef de rang", "serveur"])
        assert matcher.match("Chef de rang H/F") == 0
        assert matcher.match("Serveur / Serveuse") == 1
        assert matcher.match("Chef de salle") is None  # « chef » admis, mais « rang » manque
        assert matcher.match("Serveur adjoint") is None

    def test_le_feminin_du_niveau_compte_aussi(self) -> None:
        matcher = TitleMatcher.of(["directrice de magasin"])
        assert matcher.match("Directeur de magasin H/F") == 0
        assert matcher.match("Directeur adjoint de magasin") is None
        assert TitleMatcher.of(["aide-soignante"]).match("Aide soignant de nuit") == 0
        assert TitleMatcher.of(["comptable"]).match("Assistant comptable") is None


class TestExclusionsDuModele:
    def test_un_intitule_exclu_par_le_modele_est_ecarte_meme_sans_mot_de_niveau(self) -> None:
        matcher = TitleMatcher.of(["cuisinier"], excluded=["commis de cuisine", "plongeur"])
        assert matcher.match("Commis de cuisine / Cuisinier H/F") is None
        assert matcher.match("Cuisinier / Cuisinière") == 0

    def test_l_exclusion_passe_avant_la_phrase_acceptee(self) -> None:
        matcher = TitleMatcher.of(["chef de partie"], excluded=["chef de partie tournant"])
        assert matcher.match("Chef de partie") == 0
        assert matcher.match("Chef de partie tournant") is None

    def test_une_exclusion_vide_est_ignoree(self) -> None:
        assert TitleMatcher.of(["serveur"], excluded=["", "h/f"]).match("Serveur") == 0
