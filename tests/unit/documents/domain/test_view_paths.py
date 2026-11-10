"""Les chemins `data-chemin` de la vue : à côté des éléments, jamais dedans (ADR-0029)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from documents.fakes import CV_DATA, make_sample, make_spec
from karria.modules.documents.domain.view import from_cv, from_sample, header_path, item_path

pytestmark = pytest.mark.unit


def _spec(*, subtitle: bool = False) -> dict[str, Any]:
    spec = make_spec()
    if subtitle:
        for s in spec["bands"][0]["columns"][0]["sections"]:
            if s["kind"] == "experiences":
                s["slots"] = [{"roles": ["item_title"]}, {"roles": ["item_subtitle"]}]
    return spec


def _by_kind(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["kind"]: s for s in view["sections"]}


class TestCheminsDuCv:
    def test_l_en_tete_connait_le_chemin_de_chaque_champ(self) -> None:
        view = from_cv(_spec(), CV_DATA)
        assert header_path(view, "headline") == "identity.headline"
        assert header_path(view, "full_name") == "identity.full_name"
        assert header_path(view, "website") == "contact.links.0.url"
        assert header_path(view, "inconnu") is None

    def test_les_chemins_vivent_a_cote_des_elements_pas_dedans(self) -> None:
        view = from_cv(_spec(), CV_DATA)
        for section in view["sections"]:
            assert len(section["paths"]) == len(section["items"])
            for item in section["items"]:
                assert "paths" not in item
                assert all(not str(v).startswith("experiences.") for v in item.values())

    def test_un_intitule_compose_porte_un_chemin_virtuel(self) -> None:
        joined = _by_kind(from_cv(_spec(), CV_DATA))["experiences"]
        assert item_path(joined, 0, "item_title") == "experiences.0"
        assert item_path(joined, 0, "item_dates") == "experiences.0.dates"
        assert item_path(joined, 0, "bullet", 1) == "experiences.0.missions.1"
        assert item_path(joined, 0, "bullet", 9) is None
        split = _by_kind(from_cv(_spec(subtitle=True), CV_DATA))["experiences"]
        assert item_path(split, 0, "item_title") == "experiences.0.position"
        assert item_path(split, 0, "item_subtitle") == "experiences.0.company"

    def test_les_listes_groupees_gardent_l_index_d_origine(self) -> None:
        cv = copy.deepcopy(CV_DATA)
        cv["skills"] = [
            {"name": "Vente", "category": "Métier"},
            {"name": "Excel", "category": "Outils"},
            {"name": "Caisse", "category": "Métier"},
        ]
        skills = _by_kind(from_cv(_spec(), cv))["skills"]
        assert [i["skill"] for i in skills["items"]] == [["Vente"], ["Excel"], ["Caisse"]]
        assert item_path(skills, 2, "skill", 0) == "skills.2"
        languages = _by_kind(from_cv(_spec(), cv))["languages"]
        assert item_path(languages, 0, "language", 0) == "languages.0"

    def test_le_resume_et_le_contact(self) -> None:
        view = from_cv(_spec(), CV_DATA)
        sections = _by_kind(view)
        assert item_path(sections["summary"], 0, "summary") == "summary"
        assert item_path(sections["contact"], 0, "email") == "contact.email"
        assert item_path(sections["contact"], 0, "website") == "contact.links.0.url"


class TestExemple:
    def test_l_exemple_du_modele_n_a_aucun_chemin(self) -> None:
        view = from_sample(_spec(), make_sample())
        assert "paths" not in view
        assert header_path(view, "headline") is None
        for section in view["sections"]:
            assert item_path(section, 0, "item_title") is None

    def test_les_rubriques_libres_et_sans_titre_de_l_exemple(self) -> None:
        """Une rubrique « Réalisations » garde son titre ; une rubrique sans titre vaut un titre vide."""
        spec = _spec()
        sections = spec["bands"][0]["columns"][0]["sections"]
        sections.append({"kind": "other:realisations", "title_sample": "RÉALISATIONS", "slots": []})
        sections.append({"kind": "other:vide", "title_sample": "VIDE", "slots": []})
        for s in sections:
            if s["kind"] == "summary":
                s["title_sample"] = ""
                s["untitled"] = True
        sample = make_sample()
        sample["others"] = {
            "other:realisations": {
                "title": "RÉALISATIONS",
                "items": [
                    {
                        "title_raw": "",
                        "subtitle_raw": "",
                        "dates_raw": "",
                        "location": None,
                        "bullets": ["Prix 2023"],
                        "description": None,
                    }
                ],
            },
            "other:inconnue": {
                "title": "X",
                "items": [
                    {
                        "title_raw": "a",
                        "subtitle_raw": "",
                        "dates_raw": "",
                        "location": None,
                        "bullets": [],
                        "description": None,
                    }
                ],
            },
            "other:vide": {"title": "VIDE", "items": []},
        }
        view = _by_kind(from_sample(spec, sample))
        assert view["other:realisations"]["title"] == "RÉALISATIONS"
        assert view["other:realisations"]["items"][0]["bullet"] == ["Prix 2023"]
        assert view["other:realisations"]["items"][0]["item_title"] is None
        assert "other:inconnue" not in view
        assert "other:vide" not in view
        assert view["summary"]["title"] == ""
