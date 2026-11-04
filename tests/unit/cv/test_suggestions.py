"""Les règles des suggestions à la saisie (`domain/suggestions.py`) : pures, sans base."""

from __future__ import annotations

import pytest

from karria.modules.cv.domain import (
    MATERIALIZED_KINDS,
    REFERENCE_KINDS,
    SUGGESTION_LIMIT,
    SuggestionKind,
    VocabularyEntry,
    clean_label,
    clean_vocabulary,
    fold,
    normalize_query,
    parse_reference,
    rank,
    reference_entries,
    suggest_from_reference,
)

pytestmark = pytest.mark.unit


def entry(label: str, weight: int = 0) -> VocabularyEntry:
    return VocabularyEntry(label=label, folded=normalize_query(label), weight=weight)


class TestLaSaisie:
    def test_se_plie_sans_casse_ni_accents_ni_espaces_en_trop(self) -> None:
        assert normalize_query("  Développeur   Web ") == "developpeur web"

    def test_se_borne_a_soixante_caracteres(self) -> None:
        assert len(normalize_query("a" * 200)) == 60

    def test_vide_reste_vide(self) -> None:
        assert normalize_query("   ") == ""
        assert rank([entry("Paris")], "") == []


class TestLeClassement:
    def test_prefixe_avant_debut_de_mot_puis_poids_puis_alphabet(self) -> None:
        entries = [
            entry("Chef de rayon", 50),
            entry("Responsable de rayon", 90),
            entry("Rayonniste", 5),
            entry("Rayon frais", 5),
            entry("Boulanger", 100),
        ]
        assert rank(entries, "ray") == [
            "Rayon frais",
            "Rayonniste",
            "Responsable de rayon",
            "Chef de rayon",
        ]

    def test_ignore_la_casse_et_les_accents_de_la_saisie(self) -> None:
        entries = [entry("Développeur"), entry("Dessinateur")]
        assert rank(entries, normalize_query("DÉV")) == ["Développeur"]

    def test_ne_rend_jamais_plus_que_la_limite(self) -> None:
        entries = [entry(f"Paris {i}") for i in range(20)]
        assert len(rank(entries, "paris")) == SUGGESTION_LIMIT
        assert len(rank(entries, "paris", limit=3)) == 3

    def test_un_milieu_de_mot_ne_compte_pas(self) -> None:
        assert rank([entry("Comptable")], "table") == []


class TestLeNettoyageDunLibelle:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Serveur H/F", "Serveur"),
            ("Serveur (H/F)", "Serveur"),
            ("Vendeur F/H - CDI - Lyon", "Vendeur"),
            ("Chef de projet (CDI)", "Chef de projet"),
            ("  Développeur   web ", "Développeur web"),
            ("CARISTE", "Cariste"),
            ("RH", "RH"),
            ("12345", None),
            ("A", None),
            ("x" * 151, None),
        ],
    )
    def test_un_intitule_d_annonce_devient_un_intitule_de_cv(
        self, raw: str, expected: str | None
    ) -> None:
        assert clean_label(SuggestionKind.TITLE, raw) == expected

    def test_une_entreprise_garde_son_tiret_mais_perd_ses_majuscules(self) -> None:
        assert clean_label(SuggestionKind.COMPANY, "CARREFOUR MARKET") == "Carrefour Market"
        assert clean_label(SuggestionKind.COMPANY, "Saint-Gobain - Isover") == (
            "Saint-Gobain - Isover"
        )

    def test_une_ville_reste_telle_quelle(self) -> None:
        assert clean_label(SuggestionKind.CITY, "Saint-Étienne") == "Saint-Étienne"


class TestLeVocabulaire:
    def test_fusionne_les_formes_qui_se_plient_pareil_et_garde_la_plus_frequente(self) -> None:
        entries = clean_vocabulary(
            SuggestionKind.TITLE,
            [("Développeur H/F", 10), ("developpeur", 30), ("DÉVELOPPEUR (H/F)", 5)],
        )
        assert entries == [VocabularyEntry(label="developpeur", folded="developpeur", weight=45)]

    def test_ecarte_ce_qui_est_vu_moins_de_deux_fois_pour_les_intitules(self) -> None:
        entries = clean_vocabulary(SuggestionKind.TITLE, [("Rare", 1), ("Courant", 2)])
        assert [e.label for e in entries] == ["Courant"]

    def test_garde_une_ville_vue_une_seule_fois(self) -> None:
        entries = clean_vocabulary(SuggestionKind.CITY, [("Aurillac", 1)])
        assert [e.label for e in entries] == ["Aurillac"]

    def test_ecarte_un_libelle_dont_la_cle_pliee_deborde_la_colonne(self) -> None:
        # « ﬁ » (une ligature, un caractère) se plie en « fi » (deux) : le
        # libellé tient dans 150, sa clé non. L'entrée s'écarte, le reste passe.
        long_label = "\ufb01" * 100
        entries = clean_vocabulary(SuggestionKind.CITY, [(long_label, 5), ("Paris", 1)])
        assert [e.label for e in entries] == ["Paris"]

    def test_ecarte_un_libelle_dont_la_cle_pliee_est_vide(self) -> None:
        # Des signes combinants seuls : un libellé « non vide » sans clé.
        entries = clean_vocabulary(SuggestionKind.CITY, [("\u0301\u0301\u0301", 5)])
        assert entries == []

    def test_trie_par_poids_decroissant_puis_alphabet(self) -> None:
        entries = clean_vocabulary(
            SuggestionKind.CITY, [("Lyon", 5), ("Paris", 50), ("Lille", 5), ("Nancy", 9)]
        )
        assert [e.label for e in entries] == ["Paris", "Nancy", "Lille", "Lyon"]


class TestLesListesDeReference:
    def test_une_ligne_par_valeur_commentaires_et_doublons_ignores(self) -> None:
        entries = parse_reference(["# titre", "Anglais", "", "anglais  # doublon", "Arabe"])
        assert [e.label for e in entries] == ["Anglais", "Arabe"]
        assert entries[0].weight > entries[1].weight

    @pytest.mark.parametrize("kind", sorted(REFERENCE_KINDS))
    def test_chaque_liste_embarquee_se_lit_et_n_est_pas_vide(self, kind: SuggestionKind) -> None:
        entries = reference_entries(kind)
        assert len(entries) > 50
        assert all(e.folded == fold(e.label) for e in entries)

    def test_les_familles_sont_partitionnees(self) -> None:
        assert frozenset(SuggestionKind) == REFERENCE_KINDS | MATERIALIZED_KINDS
        assert not REFERENCE_KINDS & MATERIALIZED_KINDS

    def test_une_langue_se_propose_sans_accent_et_la_plus_parlee_d_abord(self) -> None:
        assert suggest_from_reference(SuggestionKind.LANGUAGE, "fran")[0] == "Français"
        assert suggest_from_reference(SuggestionKind.LANGUAGE, "ang")[0] == "Anglais"

    def test_un_diplome_se_propose_par_un_de_ses_mots(self) -> None:
        assert "BTS MCO (Management commercial opérationnel)" in suggest_from_reference(
            SuggestionKind.DEGREE, "mco"
        )

    def test_une_competence_et_une_ecole_se_proposent(self) -> None:
        assert suggest_from_reference(SuggestionKind.SKILL, "exc")[0] == "Excel"
        assert suggest_from_reference(SuggestionKind.SCHOOL, "hec") == ["HEC Paris"]

    def test_huit_au_plus(self) -> None:
        assert len(suggest_from_reference(SuggestionKind.DEGREE, "b")) == SUGGESTION_LIMIT
