"""La couleur d'un modèle : le décor change, le texte reste (`domain/palette.py`, ADR-0034 § 2026-11-06)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from karria.modules.documents.domain.palette import (
    TEXT_ON_DARK,
    TEXT_ON_LIGHT,
    _hls,
    contrast_ratio,
    decor_colors,
    derived_color,
    legible_styles,
    parse_hex_color,
    primary_color_of,
    recolor_spec,
    tinted_ink,
)

pytestmark = pytest.mark.unit

NOIR = "#000000"
BLEU = "#1A5276"
#: Une teinte claire du même bleu (bande latérale).
BLEU_PALE = "#D6E4EE"
BORDEAUX = "#7B1E2B"
MARINE = "#1F3A5F"
#: Une bande claire, mais encore une couleur (clarté 0,82).
CIEL = "#BFD3E6"
BEIGE = "#F2E8C9"
GRIS = "#999999"
TEXTE = "#333333"


def _style(color: str, box_h: float = 4.0) -> dict[str, Any]:
    return {"family": "Inter", "size_pt": 10.0, "color": color, "box_h_mm": box_h}


def hline(color: str, w: float = 170.0, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "hline",
        "x_mm": 20.0,
        "y_mm": 60.0,
        "w_mm": w,
        "h_mm": 0.0,
        "fill": None,
        "stroke": color,
        "stroke_pt": 0.75,
        **extra,
    }


def rect(color: str, *, x: float, y: float, w: float, h: float, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "rect",
        "x_mm": x,
        "y_mm": y,
        "w_mm": w,
        "h_mm": h,
        "fill": color,
        "stroke": None,
        "stroke_pt": None,
        **extra,
    }


def section(kind: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "slots": [], "decorations": [], "marker": None, **extra}


def spec_noire() -> dict[str, Any]:
    """Un modèle à la Meridian : tout le texte est noir, tout le décor aussi."""
    return {
        "page": {"width_mm": 210, "height_mm": 297, "background": "#FFFFFF"},
        "first_band_top_mm": 60.0,
        "content_bottom_mm": 280.0,
        "styles": {
            "section_title": _style(NOIR),
            "header.first_name": _style(NOIR, 8.0),
            "experiences.item_title": _style(TEXTE),
            "experiences.bullet": _style(NOIR),
        },
        "header": {
            "elements": [
                {"field": "first_name", "style": "header.first_name", "x_mm": 20, "y_mm": 20}
            ],
            "decorations": [
                {
                    "kind": "vline",
                    "x_mm": 80.0,
                    "y_mm": 0.0,
                    "w_mm": 0.0,
                    "h_mm": 42.0,
                    "fill": None,
                    "stroke": NOIR,
                    "stroke_pt": 0.75,
                }
            ],
            "photo": None,
        },
        "bands": [
            {
                "y_sample": 60.0,
                "bottom_sample": 250.0,
                "columns": [
                    {
                        "x_mm": 20.0,
                        "w_mm": 55.0,
                        "sections": [
                            section(
                                "experiences",
                                marker={"shape": "circle", "size_mm": 1.3, "color": NOIR},
                                decorations=[
                                    hline(GRIS, 50.0, anchor="after", dx_mm=0.0, dy_mm=2.0)
                                ],
                                slots=[
                                    {
                                        "key": "a",
                                        "separator": {
                                            "stroke": NOIR,
                                            "h_mm": 4.0,
                                            "stroke_pt": 0.75,
                                        },
                                    }
                                ],
                            )
                        ],
                    }
                ],
            }
        ],
        "page_decorations": [
            {
                "kind": "vline",
                "x_mm": 80.0,
                "y_mm": 60.0,
                "w_mm": 0.0,
                "h_mm": 217.0,
                "fill": None,
                "stroke": NOIR,
                "stroke_pt": 0.75,
                "anchor": "page",
            }
        ],
    }


def spec_bleue() -> dict[str, Any]:
    """Un modèle coloré : filets bleus, bande latérale bleu pâle, texte gris foncé, un filet gris."""
    return {
        "page": {"width_mm": 210, "height_mm": 297, "background": "#FFFFFF"},
        "first_band_top_mm": 60.0,
        "content_bottom_mm": 280.0,
        "styles": {
            "section_title": _style(BLEU),
            "header.name": _style("#111111", 8.0),
            "experiences.item_title": _style(TEXTE),
            "skills.skill": _style(TEXTE),
        },
        "header": {
            "elements": [{"field": "full_name", "style": "header.name", "x_mm": 80, "y_mm": 20}],
            "decorations": [hline(BLEU, 120.0, x_mm=80.0, y_mm=40.0)],
            "photo": None,
        },
        "bands": [
            {
                "y_sample": 60.0,
                "bottom_sample": 250.0,
                "columns": [
                    {"x_mm": 10.0, "w_mm": 50.0, "sections": [section("skills")]},
                    {
                        "x_mm": 80.0,
                        "w_mm": 110.0,
                        "sections": [
                            section("experiences", decorations=[hline(GRIS, 100.0, anchor="after")])
                        ],
                    },
                ],
            }
        ],
        "page_decorations": [rect(BLEU_PALE, x=0, y=0, w=70, h=297, anchor="page")],
    }


class TestCodeCouleur:
    def test_les_formes_acceptees_sont_normalisees(self) -> None:
        assert parse_hex_color("#1a2b3c") == "#1A2B3C"
        assert parse_hex_color("1a2b3c") == "#1A2B3C"
        assert parse_hex_color(" #ABC ") == "#AABBCC"

    @pytest.mark.parametrize("value", ["", "#", "#12345", "bleu", "#GGGGGG", "#1234567"])
    def test_ce_qui_n_est_pas_un_code_est_refuse(self, value: str) -> None:
        with pytest.raises(ValueError, match="rrggbb"):
            parse_hex_color(value)


class TestCouleurDuDecor:
    def test_un_decor_noir_a_le_noir_pour_couleur_meme_avec_des_titres_noirs(self) -> None:
        assert primary_color_of(spec_noire()) == NOIR

    def test_les_titres_ne_definissent_plus_la_couleur(self) -> None:
        sans_decor = {"styles": {"section_title": {"color": BLEU}}, "header": {}, "bands": []}
        assert primary_color_of(sans_decor) is None
        assert primary_color_of({}) is None

    def test_la_couleur_dominante_se_mesure_par_surface(self) -> None:
        spec = spec_noire()
        spec["page_decorations"].append(rect(MARINE, x=0, y=0, w=70, h=297, anchor="page"))

        assert primary_color_of(spec) == MARINE, "une bande l'emporte sur des filets"

    def test_une_bande_tres_claire_n_est_qu_un_fond(self) -> None:
        assert primary_color_of(spec_bleue()) == BLEU, "le filet bleu, pas la bande pâle"

    def test_sans_autre_decor_la_couleur_claire_est_la_couleur(self) -> None:
        spec = spec_bleue()
        spec["header"]["decorations"] = []
        spec["bands"][0]["columns"][1]["sections"][0]["decorations"] = []

        assert primary_color_of(spec) == BLEU_PALE

    def test_le_blanc_du_papier_ne_compte_pas(self) -> None:
        spec = {
            "page": {"background": "#FAFAFA"},
            "header": {"decorations": [rect("#FFFFFF", x=0, y=0, w=210, h=40)]},
            "page_decorations": [rect("#FAFAFA", x=0, y=0, w=210, h=297, anchor="page")],
            "bands": [],
        }
        assert primary_color_of(spec) is None

    def test_a_surface_egale_les_occurrences_departagent(self) -> None:
        spec = {
            "header": {
                "decorations": [hline(BLEU, 100.0), hline(BORDEAUX, 100.0), hline(BLEU, 100.0)]
            },
            "page_decorations": [],
            "bands": [],
        }
        assert primary_color_of(spec) == BLEU

    def test_les_puces_les_separateurs_et_un_fond_de_colonne_sont_du_decor(self) -> None:
        spec = spec_noire()
        spec["header"]["decorations"] = []
        spec["page_decorations"] = []
        spec["bands"][0]["columns"][0]["background"] = CIEL

        colors = dict(decor_colors(spec))
        assert set(colors) == {NOIR, GRIS, CIEL}
        assert primary_color_of(spec) == CIEL
        # Une puce de 1,3 mm pèse 1,69 mm² ; un séparateur de 4 mm, 4 × 0,26 mm².
        assert [mm2 for color, mm2 in decor_colors(spec) if color == NOIR] == pytest.approx(
            [1.69, 4 * 0.75 * 25.4 / 72], abs=0.01
        )

    def test_un_filet_sans_couleur_ne_compte_pas(self) -> None:
        spec = {"header": {"decorations": [hline("")]}, "page_decorations": [], "bands": []}
        spec["header"]["decorations"][0]["stroke"] = None
        assert decor_colors(spec) == []

    def test_un_cadre_sans_fond_compte_par_son_trait(self) -> None:
        spec = {
            "header": {"decorations": [{"kind": "rect", "w_mm": 10, "h_mm": 10, "stroke": BLEU}]},
            "page_decorations": [],
            "bands": [],
        }
        assert primary_color_of(spec) == BLEU


class TestRecoloration:
    def test_un_decor_noir_devient_bleu_et_le_texte_noir_reste_noir(self) -> None:
        spec = spec_noire()
        before = copy.deepcopy(spec)

        out = recolor_spec(spec, BLEU)

        assert spec == before, "la spécification d'origine n'est pas modifiée"
        assert out["header"]["decorations"][0]["stroke"] == BLEU
        assert out["page_decorations"][0]["stroke"] == BLEU
        sec = out["bands"][0]["columns"][0]["sections"][0]
        assert sec["marker"]["color"] == BLEU, "la puce est un décor dessiné"
        assert sec["slots"][0]["separator"]["stroke"] == BLEU
        for key in ("section_title", "header.first_name", "experiences.bullet"):
            assert out["styles"][key]["color"] == NOIR, f"{key} est un texte"
        assert out["styles"]["experiences.item_title"]["color"] == TEXTE
        assert out["page"]["background"] == "#FFFFFF"

    def test_dans_un_decor_gris_un_filet_gris_suit_en_plus_clair(self) -> None:
        out = recolor_spec(spec_noire(), BLEU)

        filet = out["bands"][0]["columns"][0]["sections"][0]["decorations"][0]["stroke"]
        h, lightness, saturation = _hls(filet)
        assert filet != GRIS
        assert abs(h - _hls(BLEU)[0]) < 3
        assert lightness > _hls(BLEU)[1], "il reste plus clair que le bleu"
        assert saturation > 0.12

    def test_dans_un_decor_colore_un_gris_reste_gris(self) -> None:
        out = recolor_spec(spec_bleue(), BORDEAUX)

        assert out["header"]["decorations"][0]["stroke"] == BORDEAUX
        filet = out["bands"][0]["columns"][1]["sections"][0]["decorations"][0]["stroke"]
        assert filet == GRIS
        assert out["styles"]["section_title"]["color"] == BLEU, "un titre bleu est un texte"
        assert out["styles"]["experiences.item_title"]["color"] == TEXTE

    def test_une_teinte_derivee_suit_a_la_meme_clarte(self) -> None:
        out = recolor_spec(spec_bleue(), BORDEAUX)

        pale = out["page_decorations"][0]["fill"]
        assert pale != BLEU_PALE
        h_pale, l_pale, s_pale = _hls(pale)
        assert abs(l_pale - _hls(BLEU_PALE)[1]) < 0.03, "toujours une bande pâle"
        assert abs(h_pale - _hls(BORDEAUX)[0]) < 3
        assert s_pale > 0.12

    def test_rien_ne_change_sans_couleur_ou_avec_la_couleur_d_origine(self) -> None:
        spec = spec_bleue()
        assert recolor_spec(spec, None) is spec
        assert recolor_spec(spec, "#1a5276") is spec
        sans = {"styles": {"section_title": {"color": BLEU}}, "header": {}, "bands": []}
        assert recolor_spec(sans, BORDEAUX) is sans

    def test_un_code_invalide_est_refuse(self) -> None:
        with pytest.raises(ValueError, match="rrggbb"):
            recolor_spec(spec_bleue(), "bleu")

    def test_une_nouvelle_couleur_grise_donne_des_derivees_grises(self) -> None:
        out = recolor_spec(spec_bleue(), "#444444")

        pale = out["page_decorations"][0]["fill"]
        r, g, b = pale[1:3], pale[3:5], pale[5:7]
        assert r == g == b, "un gris reste un gris"
        assert int(r, 16) > 0x44 * 2, "toujours clair"

    def test_une_specification_sans_styles_se_recolore_quand_meme(self) -> None:
        spec = {"header": {"decorations": [hline(BLEU)]}, "page_decorations": [], "bands": []}
        out = recolor_spec(spec, BORDEAUX)
        assert out["header"]["decorations"][0]["stroke"] == BORDEAUX
        assert "styles" not in out


class TestLisibilite:
    """Le texte ne change que s'il ne se lit plus sur une surface recolorée."""

    @staticmethod
    def bande_sombre() -> dict[str, Any]:
        """Une bande d'en-tête marine avec le nom en blanc ; le poste visé, marine, sur le papier."""
        spec = spec_bleue()
        spec["styles"]["header.name"] = _style("#FFFFFF", 8.0)
        spec["styles"]["header.headline"] = _style(MARINE, 5.0)
        spec["header"]["elements"] = [
            {"field": "full_name", "style": "header.name", "x_mm": 20, "y_mm": 15},
            {"field": "headline", "style": "header.headline", "x_mm": 20, "y_mm": 62},
            {"field": "phone", "style": "header.ghost", "x_mm": 20, "y_mm": 30},
        ]
        spec["header"]["decorations"] = [rect(MARINE, x=0, y=0, w=210, h=50)]
        spec["page_decorations"] = []
        return spec

    def test_une_bande_sombre_recoloree_en_clair_passe_le_texte_blanc_en_noir(self) -> None:
        spec = self.bande_sombre()
        assert primary_color_of(spec) == MARINE

        out = recolor_spec(spec, BEIGE)

        assert out["header"]["decorations"][0]["fill"] == BEIGE
        assert out["styles"]["header.name"]["color"] == TEXT_ON_LIGHT
        assert out["styles"]["header.headline"]["color"] == MARINE, "sur le papier : intact"
        assert out["styles"]["section_title"]["color"] == BLEU

    def test_une_bande_sombre_recoloree_en_sombre_garde_le_texte_blanc(self) -> None:
        out = recolor_spec(self.bande_sombre(), BORDEAUX)

        assert out["styles"]["header.name"]["color"] == "#FFFFFF"

    def test_une_bande_claire_recoloree_en_sombre_passe_le_texte_noir_en_blanc(self) -> None:
        spec = spec_bleue()
        spec["header"]["decorations"] = []
        spec["bands"][0]["columns"][1]["sections"][0]["decorations"] = []
        spec["page_decorations"] = [rect(CIEL, x=0, y=0, w=70, h=297, anchor="page")]
        assert primary_color_of(spec) == CIEL

        out = recolor_spec(spec, MARINE)

        assert out["page_decorations"][0]["fill"] == MARINE
        assert out["styles"]["skills.skill"]["color"] == TEXT_ON_DARK
        # Un style partagé entre la bande et le papier ne peut pas être sauvé
        # des deux côtés : il garde sa couleur plutôt que d'en perdre un.
        assert out["styles"]["section_title"]["color"] == BLEU
        assert out["styles"]["experiences.item_title"]["color"] == TEXTE, "sur le papier"

    def test_un_fond_de_colonne_declare_porte_ses_rubriques(self) -> None:
        spec = spec_bleue()
        spec["header"]["decorations"] = []
        spec["page_decorations"] = []
        spec["bands"][0]["columns"][1]["sections"][0]["decorations"] = []
        spec["bands"][0]["columns"][0]["background"] = CIEL

        out = recolor_spec(spec, MARINE)

        assert out["bands"][0]["columns"][0]["background"] == MARINE
        assert out["styles"]["skills.skill"]["color"] == TEXT_ON_DARK

    def test_une_barre_de_titre_porte_le_titre(self) -> None:
        spec = spec_noire()
        spec["styles"]["section_title"] = _style("#FFFFFF")
        spec["bands"][0]["columns"][0]["sections"][0]["decorations"] = [
            rect(NOIR, x=0, y=0, w=55, h=5, anchor="section", dx_mm=0.0, dy_mm=-0.5),
            rect(NOIR, x=0, y=0, w=55, h=5, anchor="section", dx_mm=0.0, dy_mm=30.0),
            {"kind": "vline", "w_mm": 0, "h_mm": 40, "stroke": NOIR, "anchor": "section"},
            rect(NOIR, x=0, y=0, w=2, h=2, anchor="item"),
            rect(NOIR, x=0, y=0, w=55, h=5, anchor="after", dx_mm=0.0, dy_mm=2.0),
        ]

        out = recolor_spec(spec, BEIGE)

        assert out["styles"]["section_title"]["color"] == TEXT_ON_LIGHT
        assert out["styles"]["experiences.item_title"]["color"] == TEXTE, "pas sur la barre"

    def test_une_pastille_de_ligne_et_un_fond_d_element_portent_leurs_textes(self) -> None:
        spec = spec_noire()
        spec["styles"]["experiences.item_title"] = _style("#FFFFFF")
        spec["styles"]["experiences.item_dates"] = _style("#FFFFFF")
        sec = spec["bands"][0]["columns"][0]["sections"][0]
        sec["decorations"] = [
            rect(NOIR, x=0, y=0, w=20, h=4, anchor="item", attach="row", ref_role="item_dates")
        ]
        out = recolor_spec(spec, BEIGE)
        assert out["styles"]["experiences.item_dates"]["color"] == TEXT_ON_LIGHT
        assert out["styles"]["experiences.item_title"]["color"] == "#FFFFFF"

        sec["decorations"] = [rect(NOIR, x=0, y=0, w=55, h=12, anchor="item")]
        out = recolor_spec(spec, BEIGE)
        assert out["styles"]["experiences.item_title"]["color"] == TEXT_ON_LIGHT
        assert out["styles"]["experiences.item_dates"]["color"] == TEXT_ON_LIGHT

    def test_un_element_centre_est_cherche_a_son_centre(self) -> None:
        spec = self.bande_sombre()
        spec["header"]["elements"][0].update(align="center", cx_mm=105.0, x_mm=-500.0)

        out = recolor_spec(spec, BEIGE)

        assert out["styles"]["header.name"]["color"] == TEXT_ON_LIGHT

    def test_une_surface_non_recoloree_ne_declenche_rien(self) -> None:
        spec = self.bande_sombre()
        spec["styles"]["header.on_gray"] = _style("#FFFFFF", 5.0)
        spec["header"]["elements"].append(
            {"field": "email", "style": "header.on_gray", "x_mm": 20, "y_mm": 110}
        )
        spec["header"]["decorations"].append(rect("#666666", x=0, y=100, w=210, h=30))
        # Une pastille pleine de 3 × 3 mm sous le nom n'est pas une surface de texte.
        spec["header"]["decorations"].append(rect(MARINE, x=20, y=18, w=3, h=3))
        spec["page_decorations"] = [hline(MARINE, 170.0, anchor="band", band=0, dy_mm=1.0)]

        out = recolor_spec(spec, BEIGE)

        assert out["header"]["decorations"][1]["fill"] == "#666666", "un gris reste"
        assert out["page_decorations"][0]["stroke"] == BEIGE
        assert out["styles"]["header.name"]["color"] == TEXT_ON_LIGHT
        assert out["styles"]["header.on_gray"]["color"] == "#FFFFFF", "sa surface n'a pas bougé"

    def test_une_rubrique_sans_style_de_titre_ne_casse_rien(self) -> None:
        spec = spec_bleue()
        del spec["styles"]["section_title"]
        spec["bands"][0]["columns"][0]["background"] = CIEL

        out = recolor_spec(spec, MARINE)

        assert out["styles"]["skills.skill"]["color"] == TEXT_ON_DARK
        assert "section_title" not in out["styles"]

    def test_les_styles_sont_rendus_tels_quels_sans_surface(self) -> None:
        spec = spec_noire()
        spec["styles"]["experiences.item_title"]["color"] = None
        out = legible_styles(spec, spec)
        assert out == spec["styles"]


class TestDerivee:
    def test_une_autre_teinte_n_est_pas_touchee(self) -> None:
        assert derived_color("#C0392B", BLEU, BORDEAUX) is None
        assert derived_color("#777777", BLEU, BORDEAUX) is None
        assert derived_color("#C0392B", NOIR, BORDEAUX) is None, "une couleur dans un décor gris"

    def test_la_meme_teinte_plus_sombre_reste_plus_sombre(self) -> None:
        sombre = derived_color("#0E2C40", BLEU, "#2E7D32")
        assert sombre is not None
        assert _hls(sombre)[1] < _hls("#2E7D32")[1]

    def test_un_gris_clair_dans_un_decor_noir_ne_devient_jamais_blanc(self) -> None:
        clair = derived_color("#EBEBEB", NOIR, "#1F4FA3")
        assert clair not in (None, "#FFFFFF")
        assert 0.9 < _hls(str(clair))[1] < 1.0

    def test_un_decor_blanc_se_decline_vers_le_sombre_sans_s_effacer(self) -> None:
        assert derived_color("#FFFFFF", "#FFFFFF", BLEU) == BLEU
        presque = derived_color("#F0F0F0", "#FFFFFF", BLEU)
        assert presque is not None
        assert _hls(BLEU)[1] * 0.9 < _hls(presque)[1] <= _hls(BLEU)[1]


class TestEncreDesPictogrammes:
    def test_l_encre_du_decor_prend_la_couleur_choisie_a_l_arrondi_pres(self) -> None:
        assert tinted_ink(NOIR, NOIR, BLEU) == BLEU
        assert tinted_ink("#050505", NOIR, BLEU) == BLEU

    def test_une_teinte_du_decor_suit_une_autre_encre_reste(self) -> None:
        suit = tinted_ink(BLEU_PALE, BLEU, BORDEAUX)
        assert suit is not None
        assert abs(_hls(suit)[0] - _hls(BORDEAUX)[0]) < 3
        assert tinted_ink("#C0392B", BLEU, BORDEAUX) is None


class TestContraste:
    def test_le_contraste_wcag(self) -> None:
        assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
        assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
        assert contrast_ratio(BLEU, BLEU) == pytest.approx(1.0)
        assert contrast_ratio("#FFFFFF", MARINE) > 4.5
        assert contrast_ratio("#FFFFFF", BEIGE) < 4.5
