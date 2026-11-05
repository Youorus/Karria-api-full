"""La couleur principale d'un modèle, changée sans toucher au reste (`domain/palette.py`)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from karria.modules.documents.domain.palette import (
    _hls,
    derived_color,
    parse_hex_color,
    primary_color_of,
    recolor_spec,
)

pytestmark = pytest.mark.unit

BLEU = "#1A5276"
#: Une teinte claire du même bleu (bande latérale).
BLEU_PALE = "#D6E4EE"
BORDEAUX = "#7B1E2B"


def spec_bleue() -> dict[str, Any]:
    return {
        "page": {"width_mm": 210, "height_mm": 297, "background": "#FFFFFF"},
        "styles": {
            "name": {"color": "#111111", "size_pt": 24},
            "section_title": {"color": BLEU, "size_pt": 12},
            "body": {"color": "#333333", "size_pt": 10},
            "contact": {"color": BLEU, "size_pt": 9},
        },
        "header": {
            "elements": [],
            "decorations": [{"kind": "rect", "fill": BLEU, "stroke": None}],
        },
        "bands": [
            {
                "columns": [
                    {
                        "sections": [
                            {
                                "kind": "experiences",
                                "marker": {"shape": "circle", "color": BLEU},
                                "decorations": [{"kind": "vline", "stroke": "#999999"}],
                                "slots": [{"separator": {"stroke": BLEU}}],
                            }
                        ]
                    },
                    {"sections": [], "background": BLEU_PALE},
                ]
            }
        ],
        "page_decorations": [{"kind": "rect", "fill": BLEU_PALE, "stroke": None}],
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

    def test_la_couleur_principale_est_celle_des_titres(self) -> None:
        assert primary_color_of(spec_bleue()) == BLEU
        assert primary_color_of({"styles": {"section_title": {"color": "#1a5276"}}}) == BLEU
        assert primary_color_of({"styles": {}}) is None
        assert primary_color_of({}) is None


class TestRecoloration:
    def test_la_couleur_du_modele_change_partout_et_le_texte_reste(self) -> None:
        spec = spec_bleue()
        before = copy.deepcopy(spec)

        out = recolor_spec(spec, "#7b1e2b")

        assert spec == before, "la spécification d'origine n'est pas modifiée"
        assert out["styles"]["section_title"]["color"] == BORDEAUX
        assert out["styles"]["contact"]["color"] == BORDEAUX
        assert out["styles"]["name"]["color"] == "#111111"
        assert out["styles"]["body"]["color"] == "#333333"
        assert out["header"]["decorations"][0]["fill"] == BORDEAUX
        section = out["bands"][0]["columns"][0]["sections"][0]
        assert section["marker"]["color"] == BORDEAUX
        assert section["slots"][0]["separator"]["stroke"] == BORDEAUX
        assert section["decorations"][0]["stroke"] == "#999999", "un filet gris reste gris"
        assert out["page"]["background"] == "#FFFFFF"

    def test_une_teinte_derivee_suit_avec_la_meme_clarte(self) -> None:
        out = recolor_spec(spec_bleue(), BORDEAUX)

        pale = out["bands"][0]["columns"][1]["background"]
        assert pale == out["page_decorations"][0]["fill"]
        assert pale != BLEU_PALE
        # Même écart de clarté que la bande d'origine : toujours une couleur pâle.
        _, l_pale, s_pale = _hls(pale)
        _, l_bleu_pale, _ = _hls(BLEU_PALE)
        h_bordeaux, _, _ = _hls(BORDEAUX)
        h_pale, _, _ = _hls(pale)
        assert abs(l_pale - l_bleu_pale) < 0.03
        assert abs(h_pale - h_bordeaux) < 3
        assert s_pale > 0.12

    def test_rien_ne_change_sans_couleur_ou_avec_la_couleur_d_origine(self) -> None:
        spec = spec_bleue()
        assert recolor_spec(spec, None) is spec
        assert recolor_spec(spec, "#1a5276") is spec
        sans = {"styles": {"body": {"color": "#000000"}}}
        assert recolor_spec(sans, BORDEAUX) is sans

    def test_un_code_invalide_est_refuse(self) -> None:
        with pytest.raises(ValueError, match="rrggbb"):
            recolor_spec(spec_bleue(), "bleu")

    def test_un_modele_gris_ne_recolore_que_les_occurrences_exactes(self) -> None:
        spec = spec_bleue()
        spec["styles"]["section_title"]["color"] = "#333333"
        spec["styles"]["contact"]["color"] = "#333333"

        out = recolor_spec(spec, BORDEAUX)

        assert out["styles"]["section_title"]["color"] == BORDEAUX
        assert out["styles"]["contact"]["color"] == BORDEAUX
        # Les autres gris ne sont pas « des teintes » du gris des titres.
        assert out["styles"]["body"]["color"] == BORDEAUX, "même gris que les titres : il suit"
        assert out["styles"]["name"]["color"] == "#111111"
        assert out["bands"][0]["columns"][1]["background"] == BLEU_PALE

    def test_une_nouvelle_couleur_grise_donne_des_derivees_grises(self) -> None:
        out = recolor_spec(spec_bleue(), "#444444")

        assert out["styles"]["section_title"]["color"] == "#444444"
        pale = out["bands"][0]["columns"][1]["background"]
        r, g, b = pale[1:3], pale[3:5], pale[5:7]
        assert r == g == b, "un gris reste un gris"
        assert int(r, 16) > 0x44 * 2, "toujours clair"


class TestDerivee:
    def test_une_autre_teinte_n_est_pas_touchee(self) -> None:
        assert derived_color("#C0392B", BLEU, BORDEAUX) is None
        assert derived_color("#777777", BLEU, BORDEAUX) is None

    def test_la_meme_teinte_plus_sombre_reste_plus_sombre(self) -> None:
        sombre = derived_color("#0E2C40", BLEU, "#2E7D32")
        assert sombre is not None
        _, l_out, _ = _hls(sombre)
        _, l_in, _ = _hls("#2E7D32")
        assert l_out < l_in
