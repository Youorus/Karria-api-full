"""De la sortie du modèle aux propositions : chemins qui existent, rien d'inventé."""

from __future__ import annotations

import pytest

from doubles import sample_adaptation_output, sample_base_cv
from karria.modules.applications.domain import (
    AdaptationOutput,
    invented_figures,
    propositions_from,
)

pytestmark = pytest.mark.unit


def test_la_sortie_de_test_devient_cinq_propositions() -> None:
    cv = sample_base_cv()

    conversion = propositions_from(sample_adaptation_output(), cv)

    paths = [p.path for p in conversion.propositions]
    assert paths == [
        "identity.headline",
        "summary",
        "experiences.0.position",
        "experiences.0.missions.0",
        "skills",
    ]
    assert conversion.discarded == ()
    headline = conversion.propositions[0]
    assert headline.before == "Vendeuse"
    assert headline.after == "Conseillère de vente"
    assert headline.reason == "L'offre nomme le poste ainsi."
    skills = conversion.propositions[-1]
    assert [s["name"] for s in skills.after] == ["Relation client", "Caisse", "Merchandising"]
    assert skills.before == cv["skills"]


def test_un_chiffre_absent_du_cv_est_ecarte() -> None:
    output = AdaptationOutput.model_validate(
        {
            "summary": {
                "text": "J'ai augmenté le chiffre d'affaires de 25 %.",
                "reason": "Résultat.",
            },
            "experiences": [
                {
                    "index": 0,
                    "missions": [
                        {"index": 1, "text": "Tenir la caisse (3 ans)", "reason": "Durée."}
                    ],
                }
            ],
        }
    )

    conversion = propositions_from(output, sample_base_cv())

    assert conversion.propositions == ()
    assert set(conversion.discarded) == {
        "summary:invented_figure",
        "experiences.0.missions.1:invented_figure",
    }


def test_un_chiffre_deja_dans_le_cv_passe() -> None:
    cv = sample_base_cv()
    cv["summary"] = "Vendeuse depuis 3 ans."
    output = AdaptationOutput.model_validate(
        {"summary": {"text": "Conseillère de vente depuis 3 ans.", "reason": "Mots de l'offre."}}
    )

    conversion = propositions_from(output, cv)

    assert len(conversion.propositions) == 1


def test_ce_qui_ne_change_rien_est_ecarte() -> None:
    output = AdaptationOutput.model_validate(
        {"headline": {"text": "Vendeuse", "reason": "Rien."}, "skills_order": {"order": [0, 1, 2]}}
    )

    conversion = propositions_from(output, sample_base_cv())

    assert conversion.propositions == ()
    assert conversion.discarded == ("identity.headline:unchanged", "skills:not_a_permutation")


def test_un_index_hors_du_cv_ou_un_ordre_faux_est_ecarte() -> None:
    output = AdaptationOutput.model_validate(
        {
            "experiences": [
                {"index": 7, "position": {"text": "X"}},
                {"index": 0, "missions": [{"index": 9, "text": "Y"}]},
            ],
            "experiences_order": {"order": [0, 0]},
            "skills_order": {"order": [2, 1]},
        }
    )

    conversion = propositions_from(output, sample_base_cv())

    assert conversion.propositions == ()
    assert conversion.discarded == (
        "experiences.7:no_such_experience",
        "experiences.0.missions.9:no_such_mission",
        "skills:not_a_permutation",
        "experiences:not_a_permutation",
    )


def test_sans_identite_l_intitule_est_ecarte() -> None:
    cv = sample_base_cv()
    cv["identity"] = None
    output = AdaptationOutput.model_validate({"headline": {"text": "Conseillère"}})

    conversion = propositions_from(output, cv)

    assert conversion.discarded == ("identity.headline:no_identity",)


def test_invented_figures_compare_les_nombres() -> None:
    assert invented_figures("25 % en 2 ans", "Depuis 2 ans") == ("25",)
    assert invented_figures("aucun chiffre", "") == ()
