"""Resserrer un CV pour une page (`condense_cv`) : par paliers, sans rien perdre du parcours."""

from __future__ import annotations

import copy
from typing import Any

from karria.modules.documents.domain import CONDENSE_LEVELS, condense_cv

CV: dict[str, Any] = {
    "summary": (
        "Développeur full-stack orienté produit, cinq ans d'expérience entre start-up et "
        "cabinet de conseil. Je conçois des applications mobiles et des API de bout en bout. "
        "J'aime la donnée et l'IA appliquée. Je cherche une équipe produit exigeante où le "
        "code sert directement les utilisateurs, et où l'on mesure ce que l'on livre. "
        "Encore une phrase pour dépasser la limite du palier deux."
    ),
    "experiences": [
        {"position": "Lead", "company": "A", "missions": ["m1", "m2", "m3", "m4", "m5"]},
        {"position": "Dev", "company": "B", "missions": ["m1", "m2", "m3"]},
        {"position": "Stage", "company": "C", "missions": [], "description": "libre"},
    ],
    "educations": [
        {"degree": "Master", "school": "E", "description": "Spécialisation data."},
        {"degree": "Licence", "school": "U"},
    ],
    "skills": [{"name": f"s{i}", "level": "Avancé"} for i in range(12)],
    "languages": [{"language": f"l{i}", "level": "B2"} for i in range(5)],
    "interests": [{"name": f"i{i}"} for i in range(6)],
    "projects": [
        {"title": "P1", "description": "d1"},
        {"title": "P2", "description": "d2"},
        {"title": "P3"},
    ],
}


class TestPalierUn:
    def test_replie_les_missions_au_dela_de_trois_et_le_detail(self) -> None:
        tight, notes = condense_cv(CV, 1)

        assert [len(e["missions"]) for e in tight["experiences"]] == [3, 3, 0]
        assert tight["experiences"][2]["description"] == "libre"
        assert all(e.get("description") is None for e in tight["educations"])
        assert [p["title"] for p in tight["projects"]] == ["P1", "P2"]
        assert all(p.get("description") is None for p in tight["projects"])
        assert notes == {"missions": 2, "educations_detail": 1, "projects": 1, "projects_detail": 2}

    def test_ne_retire_ni_experience_ni_formation_ni_competence(self) -> None:
        tight, _ = condense_cv(CV, 1)
        assert len(tight["experiences"]) == 3
        assert len(tight["educations"]) == 2
        assert len(tight["skills"]) == 12
        assert tight["summary"] == CV["summary"]

    def test_ne_modifie_pas_le_cv_recu(self) -> None:
        before = copy.deepcopy(CV)
        condense_cv(CV, 1)
        assert before == CV


class TestPalierDeux:
    def test_va_plus_loin_sur_tout_ce_qui_se_liste(self) -> None:
        tight, notes = condense_cv(CV, 2)

        assert [len(e["missions"]) for e in tight["experiences"]] == [2, 2, 0]
        assert len(tight["skills"]) == 10
        assert len(tight["languages"]) == 4
        assert len(tight["interests"]) == 4
        assert notes["missions"] == 4
        assert notes["skills"] == 2
        assert notes["languages"] == 1
        assert notes["interests"] == 2

    def test_ramene_l_a_propos_a_ses_premieres_phrases(self) -> None:
        tight, notes = condense_cv(CV, 2)
        assert notes["summary"] == 1
        assert tight["summary"].endswith(".")
        assert len(tight["summary"]) <= 320
        assert tight["summary"].startswith("Développeur full-stack")

    def test_un_cv_deja_court_n_a_rien_a_replier(self) -> None:
        court = {
            "summary": "Court.",
            "experiences": [{"position": "Dev", "company": "B", "missions": ["m1"]}],
            "skills": [{"name": "s"}],
        }
        tight, notes = condense_cv(court, 2)
        assert notes == {}
        assert tight == court


def test_les_paliers_sont_du_plus_discret_au_plus_franc() -> None:
    assert CONDENSE_LEVELS == (1, 2, 3)


class TestPalierTrois:
    def test_le_dernier_recours_garde_une_mission_et_toutes_les_experiences(self) -> None:
        tight, notes = condense_cv(CV, 3)
        assert [len(e["missions"]) for e in tight["experiences"]] == [1, 1, 0]
        assert len(tight["experiences"]) == 3
        assert len(tight["skills"]) == 8
        assert len(tight["languages"]) == 3
        assert len(tight["interests"]) == 3
        assert len(tight["summary"]) <= 200
        assert notes["skills"] == 4
