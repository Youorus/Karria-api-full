"""Score ATS déterministe : chaque contrôle, les mots-clés d'une offre et le total sur 100."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from karria.modules.documents.domain.ats import (
    SPACED,
    AtsReport,
    Check,
    ExtractedDocument,
    analyse,
    covered,
    fold,
    keywords,
    order_score,
)

pytestmark = pytest.mark.unit

FILLER = " ".join(["mot"] * 300)


def _doc(text: str, **changes: Any) -> ExtractedDocument:
    base = ExtractedDocument(
        text=text,
        format="pdf",
        pages=1,
        font_types={"TrueType": 3},
        embedded=True,
        images=0,
        tables=0,
    )
    return replace(base, **changes)


def _view(*sections: tuple[str, str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "header": {},
        "sections": [{"kind": k, "title": t, "items": items} for k, t, items in sections],
    }


def _check(report: AtsReport, key: str) -> Check:
    return next(c for c in report.checks if c.key == key)


EXP = ("experiences", "Expérience", [{"item_title": "Analyste de données", "bullet": ["x"]}])
EDU = ("educations", "Formation", [{"item_title": "Master en statistique"}])
SKI = ("skills", "Compétences", [{"skill": ["Python avancé", 3]}])
LAN = ("languages", "Langues", [{"language": ["Anglais courant"]}])
SUM = ("summary", "Profil", [{"summary": "Curieuse et rigoureuse"}])
VIEW = _view(SUM, EXP, EDU, SKI, LAN)

CV: dict[str, Any] = {
    "identity": {"first_name": "Awa", "last_name": "Diallo"},
    "contact": {"email": "awa@example.fr", "phone": "06 12 34 56 78"},
    "experiences": [
        {"start_date": "2021-01", "missions": ["Hausse de 20 %", "Gestion de 3 projets"]},
        {"start_date": "2019-01", "missions": ["Suivi de 12 clients", "Reporting"]},
    ],
}
TEXT = (
    "Awa Diallo awa@example.fr 06 12 34 56 78 "
    "Profil Curieuse et rigoureuse "
    "Expérience Analyste de données "
    "Formation Master en statistique "
    "Compétences Python avancé "
    "Langues Anglais courant " + FILLER
)


class TestOutils:
    def test_le_pliage_retire_accents_et_casse(self) -> None:
        assert fold("Éléphant À Noël") == "elephant a noel"

    def test_un_terme_au_pluriel_couvre_le_singulier_et_inversement(self) -> None:
        assert covered("projets", "gestion de projet")
        assert covered("tableau", "des tableaux de bord") is False
        assert covered("tableau de bord", "tableau de bords")

    def test_chaque_mot_d_un_groupe_doit_figurer(self) -> None:
        assert covered("gestion projet", "projet et gestion")
        assert not covered("gestion budget", "gestion de projet")

    def test_le_controle_se_serialise_avec_les_points_arrondis(self) -> None:
        check = Check("k", "Libellé", 7.26, 10, "constat")
        assert check.to_json() == {
            "key": "k",
            "label": "Libellé",
            "points": 7.3,
            "max_points": 10,
            "finding": "constat",
            "tip": None,
        }


class TestNiveau:
    @pytest.mark.parametrize(
        ("score", "level"), [(100, "good"), (85, "good"), (84, "fair"), (70, "fair"), (69, "poor")]
    )
    def test_le_niveau_suit_les_seuils_85_et_70(self, score: int, level: str) -> None:
        assert AtsReport(score=score, format="pdf").level == level

    def test_la_forme_json_reprend_score_niveau_controles_et_suggestion(self) -> None:
        report = AtsReport(score=90, format="docx", checks=[Check("k", "L", 1, 1, "f")])
        assert report.to_json(suggest_ats_safe=True) == {
            "score": 90,
            "level": "good",
            "format": "docx",
            "checks": [report.checks[0].to_json()],
            "keywords": None,
            "suggest_ats_safe": True,
        }

    def test_sans_controle_aucun_intitule_n_est_lu_lettre_a_lettre(self) -> None:
        assert AtsReport(score=90, format="pdf").spaced_titles() is False

    def test_un_intitule_espace_dans_les_polices_est_detecte(self) -> None:
        fonts = Check("fonts", "", 0, 8, "1 intitulé(s) lu(s) lettre à lettre")
        assert AtsReport(score=90, format="pdf", checks=[fonts]).spaced_titles() is True

    def test_un_nom_lu_lettre_a_lettre_dans_le_contact_est_detecte(self) -> None:
        contact = Check("contact", "", 6, 9, "x", tip="Ton nom est lu lettre à lettre")
        fonts = Check("fonts", "", 8, 8, "rien")
        report = AtsReport(score=90, format="pdf", checks=[fonts, contact])
        assert report.spaced_titles() is True

    def test_un_contact_sans_conseil_ne_signale_rien(self) -> None:
        contact = Check("contact", "", 9, 9, "toutes détectées")
        assert AtsReport(score=90, format="pdf", checks=[contact]).spaced_titles() is False


class TestOrdreDeLecture:
    def test_des_rubriques_lues_d_un_seul_tenant_ont_la_note_pleine(self) -> None:
        assert order_score(TEXT, VIEW) == (1.0, "5/5 rubriques lues d'un seul tenant")

    def test_aucune_rubrique_retrouvee_donne_zero(self) -> None:
        assert order_score("texte sans rapport", VIEW) == (
            0.0,
            "aucune rubrique retrouvée dans le texte extrait",
        )

    def test_une_rubrique_coupee_par_une_autre_est_comptee_entrelacee(self) -> None:
        view = _view(
            ("experiences", "Expérience", [{"a": "Analyste de données", "b": "Chargée d'études"}]),
            ("educations", "Formation", [{"a": "Master en statistique"}]),
        )
        text = "Expérience Analyste de données Formation Master en statistique Chargée d'études"
        assert order_score(text, view) == (0.5, "1/2 rubriques lues d'un seul tenant")

    def test_un_element_present_seulement_avant_son_titre_est_retrouve_quand_meme(
        self,
    ) -> None:
        view = _view(
            ("summary", "Profil", [{"s": "Analyste de données confirmée"}]),
            ("experiences", "Expérience", [{"a": "Analyste de données"}]),
        )
        text = "Profil Analyste de données confirmée Expérience autre chose"
        # Sans ce repli, l'expérience n'aurait qu'une position et ne compterait pas.
        assert order_score(text, view) == (1.0, "2/2 rubriques lues d'un seul tenant")

    def test_les_textes_courts_et_les_valeurs_non_textuelles_sont_ignores(self) -> None:
        view = _view(("skills", "Compétences", [{"skill": ["SQL", 3], "n": None}]))
        assert order_score("Compétences SQL", view)[0] == 0.0


class TestAnalyseSansOffre:
    def test_un_cv_exemplaire_obtient_cent(self) -> None:
        report = analyse(_doc(TEXT), VIEW, CV, columns=1)
        assert report.score == 100
        assert report.level == "good"
        assert report.keywords is None
        assert [c.key for c in report.checks] == [
            "text_layer",
            "fonts",
            "reading_order",
            "contact",
            "layout",
            "sections",
            "content",
        ]
        assert all(c.tip is None for c in report.checks)
        assert _check(report, "contact").finding == "toutes détectées"
        assert _check(report, "layout").finding == "une colonne, sans tableau"

    def test_le_total_est_ramene_a_100_sans_les_mots_cles(self) -> None:
        report = analyse(_doc(TEXT), VIEW, CV, columns=1)
        assert sum(c.max_points for c in report.checks) == 80

    def test_des_blocs_absents_du_texte_baissent_la_couche_texte(self) -> None:
        text = TEXT.replace("Master en statistique", "")
        check = _check(analyse(_doc(text), VIEW, CV, columns=1), "text_layer")
        assert check.points == 8.3
        assert check.finding.startswith("5/6 blocs")
        assert check.tip is not None

    def test_une_vue_vide_ne_divise_pas_par_zero(self) -> None:
        check = _check(analyse(_doc(TEXT), _view(), CV, columns=1), "text_layer")
        assert check.points == 0.0
        assert check.finding.startswith("0/0")

    def test_les_polices_type3_et_l_incorporation_partielle_coutent_des_points(self) -> None:
        doc = _doc(TEXT + " ��", font_types={"Type3": 2}, embedded=False)
        check = _check(analyse(doc, VIEW, CV, columns=1), "fonts")
        assert check.points == 0.0
        assert "2 police(s) Type3, 2 caractère(s) illisible(s), incorporation partielle" in (
            check.finding
        )
        assert check.tip is not None
        assert "Type3" in check.tip

    def test_les_caracteres_illisibles_coutent_au_plus_trois_points(self) -> None:
        check = _check(analyse(_doc(TEXT + " �����"), VIEW, CV, columns=1), "fonts")
        assert check.points == 5.0
        assert check.tip is None

    def test_un_intitule_lu_lettre_a_lettre_coute_deux_points_et_suggere_la_variante(
        self,
    ) -> None:
        report = analyse(_doc(TEXT + " D A T A S"), VIEW, CV, columns=1)
        check = _check(report, "fonts")
        assert check.points == 6.0
        assert "1 intitulé(s) lu(s) lettre à lettre (« D A T A S… »)" in check.finding
        assert "optimisée ATS" in (check.tip or "")
        assert report.spaced_titles() is True

    def test_la_penalite_d_espacement_est_plafonnee_a_quatre_points(self) -> None:
        text = TEXT + " A B C D E ; F G H I J ; K L M N O"
        assert _check(analyse(_doc(text), VIEW, CV, columns=1), "fonts").points == 4.0

    def test_un_nom_illisible_seul_suggere_la_variante_optimisee(self) -> None:
        text = TEXT.replace("Awa Diallo", "A w a D i a l l o")
        check = _check(analyse(_doc(text), VIEW, CV, columns=1), "contact")
        assert check.points == 6.0
        assert check.finding == "non détecté : nom en tête"
        assert "optimisée ATS" in (check.tip or "")

    def test_un_nom_cite_seulement_dans_un_e_mail_n_est_pas_en_tete(self) -> None:
        cv = {**CV, "identity": {"first_name": "awa", "last_name": "example"}}
        check = _check(analyse(_doc(TEXT), VIEW, cv, columns=1), "contact")
        assert check.finding == "non détecté : nom en tête"

    def test_des_coordonnees_illisibles_recoivent_le_conseil_general(self) -> None:
        text = TEXT.replace("awa@example.fr", "").replace("06 12 34 56 78", "")
        check = _check(analyse(_doc(text), VIEW, CV, columns=1), "contact")
        assert check.points == 3.0
        assert check.finding == "non détecté : e-mail, téléphone"
        assert (check.tip or "").startswith("Écris tes coordonnées")

    def test_un_telephone_a_espaces_insecables_est_reconnu(self) -> None:
        text = TEXT.replace("06 12 34 56 78", "06\xa012\xa034\xa056\xa078")
        assert _check(analyse(_doc(text), VIEW, CV, columns=1), "contact").points == 9.0

    def test_une_coordonnee_absente_du_cv_n_est_pas_reprochee(self) -> None:
        cv = {**CV, "contact": {}}
        check = _check(analyse(_doc(TEXT), VIEW, cv, columns=1), "contact")
        assert check.points == 9.0
        assert check.tip is None

    def test_sans_identite_le_nom_manque(self) -> None:
        cv = {**CV, "identity": None, "contact": None}
        check = _check(analyse(_doc(TEXT), VIEW, cv, columns=1), "contact")
        assert check.finding == "non détecté : nom en tête"
        assert check.points == 6.0

    def test_plusieurs_colonnes_bien_lues_ne_coutent_rien(self) -> None:
        check = _check(analyse(_doc(TEXT), VIEW, CV, columns=2), "layout")
        assert (check.points, check.finding) == (8.0, "2 colonnes")

    def test_plusieurs_colonnes_entrelacees_coutent_trois_points(self) -> None:
        text = (
            TEXT.replace("Formation Master en statistique ", "")
            + " Formation Master en statistique"
        )
        text = text.replace("Expérience Analyste de données", "Expérience")
        text += " Analyste de données"
        report = analyse(_doc(text), VIEW, CV, columns=2)
        assert _check(report, "layout").points == 5.0
        assert _check(report, "reading_order").tip is not None

    def test_tableaux_photo_et_pages_en_trop_sont_signales(self) -> None:
        doc = _doc(TEXT, tables=2, images=1, pages=3)
        check = _check(analyse(doc, VIEW, CV, columns=1), "layout")
        assert check.points == 5.0
        assert check.finding == "2 tableau(x) de mise en page, photo, 3 pages"
        assert "photo" in (check.tip or "")

    def test_un_nombre_de_pages_inconnu_n_est_pas_penalise(self) -> None:
        assert _check(analyse(_doc(TEXT, pages=None), VIEW, CV, columns=1), "layout").points == 8

    def test_les_rubriques_requises_manquantes_sont_nommees(self) -> None:
        check = _check(analyse(_doc(TEXT), _view(EXP), CV, columns=1), "sections")
        assert check.points == round(25 * (1 / 3) * 0.8, 1)
        assert check.finding == "rubriques reconnues : experiences"
        assert check.tip == "Ajoute : Formation, Compétences."

    def test_sans_rubrique_reconnue_le_constat_le_dit(self) -> None:
        view = _view(("x", "Divers", []))
        check = _check(analyse(_doc(TEXT), view, CV, columns=1), "sections")
        assert check.points == 0.0
        assert check.finding == "rubriques reconnues : aucune"

    def test_un_contenu_maigre_recoit_chaque_conseil(self) -> None:
        cv = {**CV, "experiences": [{"missions": ["Accueil"]}, {}]}
        check = _check(analyse(_doc("court"), VIEW, cv, columns=1), "content")
        assert check.points == 0.0
        assert check.finding == (
            "0/2 expériences avec ≥ 2 missions, 0 résultat(s) chiffré(s), 1 mots"
        )
        assert check.tip == (
            "Pour aller plus loin : chiffre tes résultats (volumes, pourcentages, délais) ; "
            "donne au moins deux missions par expérience ; vise 250 à 900 mots (actuellement 1)."
        )

    def test_sans_experience_le_contenu_ne_divise_pas_par_zero(self) -> None:
        cv = {**CV, "experiences": None}
        check = _check(analyse(_doc(TEXT), VIEW, cv, columns=1), "content")
        assert check.points == 2.0
        assert check.tip == (
            "Pour aller plus loin : chiffre tes résultats (volumes, pourcentages, délais)."
        )


OFFER = "Nous recherchons un analyste. Compétences : SQL, Power BI, tableaux de bord."


class TestMotsCles:
    def test_les_termes_techniques_et_groupes_nominaux_sont_extraits(self) -> None:
        assert keywords(OFFER) == ["power bi", "tableaux de bord", "sql", "analyste", "competences"]

    def test_les_mots_vides_les_verbes_et_les_mots_deja_groupes_sont_ecartes(self) -> None:
        terms = keywords("Développer des API REST. Analyser les données. CDI à Paris, CDI.")
        assert "api rest" in terms
        assert "developper" not in terms
        assert "cdi" not in terms
        assert "api" not in terms
        assert "donnees" in terms

    def test_un_groupe_rencontre_une_seule_fois_sans_terme_technique_n_est_pas_retenu(
        self,
    ) -> None:
        assert keywords("gestion budget") == ["gestion", "budget"]

    def test_un_groupe_inclus_dans_un_groupe_retenu_n_est_pas_repris(self) -> None:
        assert keywords("MySQL Workbench ; SQL Workbench") == ["mysql workbench", "sql"]

    def test_le_nombre_de_mots_cles_est_borne(self) -> None:
        offer = ", ".join(f"mot{i}x" for i in range(30))
        assert len(keywords(offer, top=5)) == 5
        assert len(keywords(offer)) == 15

    def test_les_groupes_seuls_peuvent_depasser_la_borne_avant_coupe(self) -> None:
        offer = ", ".join(f"SQL{i} BI{i}" for i in range(4))
        assert len(keywords(offer, top=2)) == 2

    def test_les_mots_cles_couverts_et_manquants_entrent_dans_le_score(self) -> None:
        text = TEXT + " SQL tableaux de bord analyste"
        report = analyse(_doc(text), VIEW, CV, columns=1, offer=OFFER)
        check = _check(report, "keywords")
        assert report.keywords == {
            "coverage": 0.8,
            "covered": ["tableaux de bord", "sql", "analyste", "competences"],
            "missing": ["power bi"],
        }
        assert check.points == 16.0
        assert check.max_points == 20
        assert check.finding == "4/5 mots-clés de l'offre présents"
        assert check.tip == "Si c'est vrai pour toi, fais apparaître : power bi."
        assert report.score == round(100 * 96 / 100)

    def test_tous_les_mots_cles_couverts_ne_donnent_aucun_conseil(self) -> None:
        text = TEXT + " SQL Power BI tableaux de bord analyste"
        check = _check(analyse(_doc(text), VIEW, CV, columns=1, offer=OFFER), "keywords")
        assert check.points == 20.0
        assert check.tip is None

    def test_une_offre_sans_mot_cle_ne_divise_pas_par_zero(self) -> None:
        report = analyse(_doc(TEXT), VIEW, CV, columns=1, offer="et de la")
        assert report.keywords == {"coverage": 0.0, "covered": [], "missing": []}

    def test_une_offre_vide_est_ignoree(self) -> None:
        assert analyse(_doc(TEXT), VIEW, CV, columns=1, offer="").keywords is None


def test_un_outil_en_plus_ou_diese_est_reconnu_dans_le_cv() -> None:
    assert covered("c++", "python c++ java")
    assert covered("c#", "dotnet c# azure")


def test_un_mot_en_double_s_est_reconnu_au_singulier() -> None:
    assert covered("process", "gestion process agile")
    assert covered("business", "business intelligence")


def test_un_mot_n_est_pas_reconnu_a_l_interieur_d_un_autre() -> None:
    assert not covered("sql", "mysql")


def test_un_outil_en_er_n_est_pas_pris_pour_un_verbe() -> None:
    assert "docker" in keywords("Maîtrise de Docker et Kubernetes. Docker en production.")


def test_un_intitule_de_quatre_lettres_espacees_est_repere() -> None:
    assert SPACED.findall("D A T A analyst") == ["D A T A"]
