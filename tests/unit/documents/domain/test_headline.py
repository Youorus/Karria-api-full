"""Poste visé synthétisé dans la grammaire du modèle, et variante « optimisée ATS »."""

from __future__ import annotations

import pytest

from karria.modules.documents.domain.headline import (
    ATS_SAFE_TRACKING_EM,
    DEFAULT_BOX_RATIO,
    MM_PER_PT,
    ats_safe_spacing,
    ats_safe_spec,
    synthesize_headline_style,
)

pytestmark = pytest.mark.unit

NAME = {"family": "Raleway", "weight": 700, "size_pt": 28, "color": "#111111"}
TITLE = {"size_pt": 15, "color": "#AA0000", "letter_spacing_pt": 3.0, "uppercase": True}


def test_le_poste_reprend_la_famille_du_nom_et_n_est_jamais_italique() -> None:
    style = synthesize_headline_style(NAME, TITLE)
    assert style["family"] == "Raleway"
    assert style["italic"] is False
    assert style["synthesized"] is True
    assert style["pitch_mm"] is None


def test_le_corps_vaut_72_pourcent_des_titres_de_rubrique() -> None:
    assert synthesize_headline_style(NAME, TITLE)["size_pt"] == 10.8


def test_le_corps_ne_descend_pas_sous_1_15_fois_le_texte_courant() -> None:
    title = {**TITLE, "size_pt": 10}
    assert synthesize_headline_style(NAME, title, body_size_pt=10)["size_pt"] == 11.5


def test_un_texte_courant_plus_petit_ne_change_pas_le_corps() -> None:
    assert synthesize_headline_style(NAME, TITLE, body_size_pt=8)["size_pt"] == 10.8


def test_le_corps_est_plafonne_a_55_pourcent_du_nom() -> None:
    name = {**NAME, "size_pt": 16}
    assert synthesize_headline_style(name, TITLE)["size_pt"] == 8.8


def test_graisse_500_sous_un_nom_gras() -> None:
    assert synthesize_headline_style({**NAME, "weight": 600}, TITLE)["weight"] == 500


def test_graisse_400_sous_un_nom_maigre() -> None:
    assert synthesize_headline_style({**NAME, "weight": 400}, TITLE)["weight"] == 400


def test_la_couleur_d_accent_des_titres_est_reprise() -> None:
    assert synthesize_headline_style(NAME, TITLE)["color"] == "#AA0000"


def test_sans_accent_la_couleur_du_nom_est_reprise() -> None:
    title = {**TITLE, "color": "#111111"}
    assert synthesize_headline_style(NAME, title)["color"] == "#111111"


def test_l_interlettrage_des_titres_tres_espaces_est_plafonne_a_0_08_em() -> None:
    style = synthesize_headline_style(NAME, TITLE)  # titres à 0,2 em
    assert style["letter_spacing_pt"] == round(ATS_SAFE_TRACKING_EM * 10.8, 2)
    assert style["letter_spacing_pt"] / style["size_pt"] <= ATS_SAFE_TRACKING_EM


def test_un_interlettrage_faible_est_porte_a_0_06_em_puis_reduit_de_10_pourcent() -> None:
    title = {**TITLE, "letter_spacing_pt": 0.3}  # 0,02 em
    assert synthesize_headline_style(NAME, title)["letter_spacing_pt"] == round(0.054 * 10.8, 2)


def test_un_interlettrage_moyen_est_reduit_de_10_pourcent() -> None:
    title = {**TITLE, "letter_spacing_pt": 1.05}  # 0,07 em
    assert synthesize_headline_style(NAME, title)["letter_spacing_pt"] == round(
        0.07 * 0.9 * 10.8, 2
    )


def test_des_titres_sans_interlettrage_donnent_un_poste_sans_interlettrage() -> None:
    title = {"size_pt": 15, "color": "#AA0000"}
    style = synthesize_headline_style(NAME, title)
    assert style["letter_spacing_pt"] == 0.0
    assert style["uppercase"] is False


def test_les_capitales_suivent_les_titres_de_rubrique() -> None:
    assert synthesize_headline_style(NAME, TITLE)["uppercase"] is True


def test_la_hauteur_de_boite_suit_le_corps_et_le_rapport_donne() -> None:
    default = synthesize_headline_style(NAME, TITLE)
    custom = synthesize_headline_style(NAME, TITLE, box_ratio=1.5)
    assert default["box_h_mm"] == round(10.8 * DEFAULT_BOX_RATIO * MM_PER_PT, 3)
    assert custom["box_h_mm"] == round(10.8 * 1.5 * MM_PER_PT, 3)


def test_l_espacement_sur_est_plafonne_a_0_08_em() -> None:
    assert ats_safe_spacing({"letter_spacing_pt": 3.0, "size_pt": 10}) == 0.8


def test_un_espacement_deja_sur_est_garde() -> None:
    assert ats_safe_spacing({"letter_spacing_pt": 0.5, "size_pt": 10}) == 0.5


def test_la_variante_ats_ne_change_que_l_interlettrage_sans_modifier_l_original() -> None:
    spec = {
        "bands": [],
        "styles": {
            "name": {"letter_spacing_pt": 4.0, "size_pt": 20, "color": "#000"},
            "body": {"letter_spacing_pt": 0.0, "size_pt": 10, "color": "#333"},
        },
    }
    safe = ats_safe_spec(spec)
    assert safe["styles"]["name"] == {"letter_spacing_pt": 1.6, "size_pt": 20, "color": "#000"}
    assert safe["styles"]["body"]["letter_spacing_pt"] == 0.0
    assert safe["bands"] is spec["bands"]
    assert spec["styles"]["name"]["letter_spacing_pt"] == 4.0
