"""Vue de rendu : le CV ou l'exemple du modèle, rangé par rôle pour les deux moteurs."""

from __future__ import annotations

from typing import Any

import pytest

from karria.modules.documents.domain.view import (
    TITLES_FR,
    from_cv,
    from_sample,
    grouped,
    period,
    separator,
    short_url,
    spec_sections,
    year,
)

pytestmark = pytest.mark.unit


def _section(kind: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "slots": [], "title_sep": "", "title_sample": kind.upper(), **extra}


def _spec(*sections: dict[str, Any]) -> dict[str, Any]:
    half = len(sections) // 2
    return {
        "styles": {},
        "header": {"elements": [], "photo": None},
        "bands": [
            {"columns": [{"sections": list(sections[:half])}]},
            {"columns": [{"sections": list(sections[half:])}]},
        ],
    }


def _by_kind(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["kind"]: s for s in view["sections"]}


class TestOutils:
    def test_l_annee_est_les_quatre_premiers_caracteres(self) -> None:
        assert year("2021-09") == "2021"
        assert year(None) == ""

    def test_une_periode_sur_deux_annees(self) -> None:
        assert period("2021-01", "2023-06", current=False) == "2021 - 2023"

    def test_une_periode_en_cours_finit_aujourd_hui(self) -> None:
        assert period("2023-01", "2020", current=True, sep=" – ") == "2023 – Aujourd’hui"

    def test_une_periode_sur_une_seule_annee_ne_repete_pas_l_annee(self) -> None:
        assert period("2022-01", "2022-12", current=False) == "2022"

    def test_une_seule_borne_donne_cette_borne(self) -> None:
        assert period(None, "2020", current=False) == "2020"
        assert period("2019", None, current=False) == "2019"
        assert period(None, None, current=False) == ""

    def test_le_separateur_est_lu_dans_l_exemple(self) -> None:
        assert separator([None, "Chef | Acme"]) == " | "  # type: ignore[list-item]

    def test_sans_separateur_dans_l_exemple_le_defaut_s_applique(self) -> None:
        assert separator(["Chef-de-projet"], default=", ") == ", "

    def test_les_elements_sont_groupes_par_categorie_consecutive(self) -> None:
        assert grouped("skill", [("Python", "Tech"), ("SQL", "Tech"), ("Anglais", None)]) == [
            {"subsection_title": "Tech", "skill": ["Python", "SQL"]},
            {"subsection_title": None, "skill": ["Anglais"]},
        ]

    def test_l_url_courte_perd_le_schema_le_www_et_la_barre_finale(self) -> None:
        assert short_url("https://www.linkedin.com/in/x/") == "linkedin.com/in/x"
        assert short_url("http://site.fr") == "site.fr"

    def test_les_rubriques_du_modele_sont_indexees_par_genre(self) -> None:
        spec = _spec(_section("skills"), _section("experiences"))
        assert set(spec_sections(spec)) == {"skills", "experiences"}


CV: dict[str, Any] = {
    "identity": {"first_name": " Awa ", "last_name": "Diallo ", "headline": " Data Analyst "},
    "contact": {
        "phone": "06 12 34 56 78",
        "email": "awa@example.fr",
        "city": "Paris",
        "links": [{"url": "https://www.linkedin.com/in/awa/"}],
    },
    "summary": "Analyste curieuse.",
    "experiences": [
        {
            "position": "Analyste",
            "company": "Acme",
            "start_date": "2021-01",
            "end_date": None,
            "is_current": True,
            "location": "Paris",
            "missions": ["Tableaux de bord", "SQL"],
            "description": "ignorée",
        },
        {
            "position": "Stagiaire",
            "company": "Beta",
            "start_date": "2020-02",
            "end_date": "2020-08",
            "description": "Stage d'été",
        },
    ],
    "educations": [
        {
            "degree": "Master",
            "school": "Dauphine",
            "start_date": "2019",
            "end_date": "2021",
            "is_in_progress": False,
            "description": None,
        }
    ],
    "skills": [{"name": "Python", "category": "Tech"}, {"name": "Excel"}],
    "languages": [
        {"language": "Français", "level": "Natif"},
        {"language": "Anglais", "level": "C1"},
        {"language": "Wolof"},
    ],
    "interests": [{"name": "Course"}],
    "projects": [
        {"title": "Karria", "technologies": ["Python", "FastAPI"], "description": "API"},
        {"title": "Blog"},
    ],
}


class TestDepuisLeCv:
    def test_l_en_tete_porte_le_nom_nettoye_et_le_poste_vise(self) -> None:
        header = from_cv(_spec(), CV)["header"]
        assert header == {
            "first_name": "Awa",
            "last_name": "Diallo",
            "full_name": "Awa Diallo",
            "headline": "Data Analyst",
            "phone": "06 12 34 56 78",
            "email": "awa@example.fr",
            "address": "Paris",
            "website": "linkedin.com/in/awa",
        }

    def test_sans_poste_vise_l_en_tete_le_laisse_vide(self) -> None:
        cv = {**CV, "identity": {"first_name": "Awa", "last_name": "Diallo", "headline": None}}
        assert from_cv(_spec(), cv)["header"]["headline"] == ""

    def test_les_rubriques_suivent_l_ordre_canonique_avec_les_titres_francais(self) -> None:
        view = from_cv(_spec(), CV)
        kinds = [s["kind"] for s in view["sections"]]
        assert kinds == [
            "contact",
            "summary",
            "experiences",
            "educations",
            "skills",
            "languages",
            "interests",
            "projects",
        ]
        assert all(s["title"] == TITLES_FR[s["kind"]] for s in view["sections"])

    def test_le_contact_ne_garde_que_les_champs_renseignes(self) -> None:
        cv = {**CV, "contact": {"email": "awa@example.fr"}}
        contact = _by_kind(from_cv(_spec(), cv))["contact"]
        assert contact["items"] == [{"email": "awa@example.fr"}]

    def test_poste_et_entreprise_sont_reunis_si_le_modele_n_a_pas_de_sous_titre(self) -> None:
        spec = _spec(_section("experiences", title_sep="Chef — Acme"))
        items = _by_kind(from_cv(spec, CV))["experiences"]["items"]
        assert items[0]["item_title"] == "Analyste — Acme"
        assert items[0]["item_subtitle"] is None

    def test_sans_rubrique_experiences_dans_le_modele_le_separateur_par_defaut(self) -> None:
        items = _by_kind(from_cv(_spec(), CV))["experiences"]["items"]
        assert items[1]["item_title"] == "Stagiaire - Beta"

    def test_l_entreprise_va_dans_le_sous_titre_si_le_modele_en_a_un(self) -> None:
        slots = [{"roles": ["item_title", "item_dates"]}, {"roles": ["item_subtitle"]}]
        spec = _spec(_section("experiences", slots=slots))
        items = _by_kind(from_cv(spec, CV))["experiences"]["items"]
        assert (items[0]["item_title"], items[0]["item_subtitle"]) == ("Analyste", "Acme")

    def test_une_experience_avec_missions_les_met_en_puces_sans_description(self) -> None:
        first, second = _by_kind(from_cv(_spec(), CV))["experiences"]["items"]
        assert first["bullet"] == ["Tableaux de bord", "SQL"]
        assert first["item_text"] is None
        assert first["item_dates"] == "2021 - Aujourd’hui"
        assert first["item_location"] == "Paris"
        assert second["bullet"] == []
        assert second["item_text"] == "Stage d'été"
        assert second["item_dates"] == "2020"

    def test_une_formation_garde_diplome_ecole_et_periode(self) -> None:
        (item,) = _by_kind(from_cv(_spec(), CV))["educations"]["items"]
        assert item["item_title"] == "Master"
        assert item["item_subtitle"] == "Dauphine"
        assert item["item_dates"] == "2019 - 2021"
        assert item["bullet"] == []

    def test_les_competences_sont_groupees_par_categorie(self) -> None:
        items = _by_kind(from_cv(_spec(), CV))["skills"]["items"]
        assert items == [
            {"subsection_title": "Tech", "skill": ["Python"]},
            {"subsection_title": None, "skill": ["Excel"]},
        ]

    def test_le_niveau_de_langue_natif_se_lit_langue_maternelle(self) -> None:
        (item,) = _by_kind(from_cv(_spec(), CV))["languages"]["items"]
        assert item["language"] == ["Français (langue maternelle)", "Anglais (C1)", "Wolof"]

    def test_les_centres_d_interet_sont_une_liste(self) -> None:
        (item,) = _by_kind(from_cv(_spec(), CV))["interests"]["items"]
        assert item["interest"] == ["Course"]

    def test_les_technologies_d_un_projet_forment_son_sous_titre(self) -> None:
        first, second = _by_kind(from_cv(_spec(), CV))["projects"]["items"]
        assert first["item_subtitle"] == "Python, FastAPI"
        assert first["item_text"] == "API"
        assert second["item_subtitle"] is None
        assert second["item_dates"] is None

    def test_un_cv_vide_donne_un_en_tete_vide_et_aucune_rubrique(self) -> None:
        view = from_cv(_spec(), {})
        assert view["sections"] == []
        assert view["header"]["full_name"] == ""
        assert view["header"]["website"] == ""
        assert "sample" not in view


SAMPLE: dict[str, Any] = {
    "identity": {"first_name": "Jean", "last_name": "Martin", "headline": "Designer"},
    "contact": {"phone": "0102030405", "email": "j@m.fr", "city": "", "website": "jm.fr"},
    "summary": "Créatif.",
    "experiences": [
        {
            "title_raw": "Designer | Studio",
            "subtitle_raw": "",
            "dates_raw": "",
            "location": None,
            "bullets": ["Logos"],
            "description": None,
        }
    ],
    "references": [
        {
            "title_raw": "M. Durand",
            "subtitle_raw": "Directeur",
            "dates_raw": "2020",
            "location": "Lyon",
            "bullets": [],
            "description": "Joignable",
        }
    ],
    "skills": [{"name": "Figma", "category": "Outils"}],
    "languages": [{"name": "Anglais"}],
}


class TestDepuisLExemple:
    def test_l_en_tete_reprend_l_exemple_tel_quel(self) -> None:
        header = from_sample(_spec(), SAMPLE)["header"]
        assert header["full_name"] == "Jean Martin"
        assert header["headline"] == "Designer"
        assert header["website"] == "jm.fr"
        assert header["address"] == ""

    def test_sans_poste_vise_dans_l_exemple_l_en_tete_le_laisse_vide(self) -> None:
        sample = {**SAMPLE, "identity": {"first_name": "Jean"}}
        header = from_sample(_spec(), sample)["header"]
        assert header["headline"] == ""
        assert header["full_name"] == "Jean"

    def test_les_titres_d_origine_du_modele_sont_gardes(self) -> None:
        spec = _spec(_section("contact"), _section("experiences", title_sample="Parcours"))
        view = from_sample(spec, SAMPLE)
        sections = _by_kind(view)
        assert view["sample"] is True
        assert sections["contact"]["title"] == "CONTACT"
        assert sections["experiences"]["title"] == "Parcours"
        assert sections["skills"]["title"] == TITLES_FR["skills"]

    def test_le_contact_ne_garde_que_les_champs_renseignes(self) -> None:
        sections = _by_kind(from_sample(_spec(_section("contact")), SAMPLE))
        assert sections["contact"]["items"] == [
            {"phone": "0102030405", "email": "j@m.fr", "website": "jm.fr"}
        ]

    def test_sans_rubrique_contact_dans_le_modele_le_contact_n_est_pas_une_rubrique(
        self,
    ) -> None:
        assert "contact" not in _by_kind(from_sample(_spec(), SAMPLE))

    def test_les_elements_bruts_vides_deviennent_none(self) -> None:
        (exp,) = _by_kind(from_sample(_spec(), SAMPLE))["experiences"]["items"]
        assert exp == {
            "item_title": "Designer | Studio",
            "item_subtitle": None,
            "item_dates": None,
            "item_location": None,
            "bullet": ["Logos"],
            "item_text": None,
        }

    def test_les_rubriques_presentes_seules_sont_rendues_dans_l_ordre(self) -> None:
        kinds = [s["kind"] for s in from_sample(_spec(), SAMPLE)["sections"]]
        assert kinds == ["summary", "experiences", "references", "skills", "languages"]

    def test_les_listes_sont_groupees_par_categorie(self) -> None:
        sections = _by_kind(from_sample(_spec(), SAMPLE))
        assert sections["skills"]["items"] == [{"subsection_title": "Outils", "skill": ["Figma"]}]
        assert sections["languages"]["items"] == [
            {"subsection_title": None, "language": ["Anglais"]}
        ]

    def test_un_exemple_vide_ne_donne_aucune_rubrique(self) -> None:
        view = from_sample(_spec(), {})
        assert view["sections"] == []
        assert view["header"]["first_name"] == ""
