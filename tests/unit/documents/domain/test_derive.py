"""Dériver un modèle original (`derive_spec`) : miroir, polices, couleur, titres, trace."""

from __future__ import annotations

import copy
from typing import Any

from karria.modules.documents.domain.derive import (
    PRIMARIES,
    choose_pairing,
    choose_primary,
    derive_spec,
)

SPEC: dict[str, Any] = {
    "meta": {"title": "Bleu"},
    "page": {"width_mm": 210.0, "height_mm": 297.0, "background": "#FFFFFF"},
    "fonts": [
        {"family": "Poppins", "weight": 400, "italic": False},
        {"family": "League Spartan", "weight": 700, "italic": False},
    ],
    "styles": {
        "header.first_name": {
            "family": "League Spartan",
            "weight": 700,
            "size_pt": 30.0,
            "color": "#1B3A6B",
        },
        "section_title": {
            "family": "Poppins",
            "weight": 700,
            "size_pt": 13.0,
            "color": "#1B3A6B",
            "uppercase": True,
            "letter_spacing_pt": 2.0,
        },
        "body": {"family": "Poppins", "weight": 400, "size_pt": 10.0, "color": "#222222"},
    },
    "header": {
        "photo": {"x_mm": 20.0, "w_mm": 40.0, "y_mm": 10.0, "h_mm": 40.0, "shape": "circle"},
        "elements": [
            {"x_mm": 80.0, "w_mm": 60.0, "y_mm": 20.0, "field": "first_name", "align": "left"},
            {"x_mm": 150.0, "rx_mm": 200.0, "y_mm": 40.0, "field": "headline", "align": "right"},
        ],
        "decorations": [],
    },
    "page_decorations": [
        {"kind": "rect", "x_mm": 0.0, "y_mm": 0.0, "w_mm": 70.0, "h_mm": 297.0, "fill": "#1B3A6B"},
        {
            "kind": "vline",
            "x_mm": 80.0,
            "y_mm": 60.0,
            "w_mm": 0.0,
            "h_mm": 200.0,
            "stroke": "#1B3A6B",
            "stroke_pt": 0.75,
        },
    ],
    "bands": [
        {
            "y_sample": 60.0,
            "decorations": [],
            "columns": [
                {
                    "x_mm": 10.0,
                    "w_mm": 55.0,
                    "sections": [{"kind": "skills", "side": None, "slots": [], "decorations": []}],
                },
                {
                    "x_mm": 80.0,
                    "w_mm": 120.0,
                    "sections": [
                        {"kind": "experiences", "side": None, "slots": [], "decorations": []}
                    ],
                },
            ],
        }
    ],
    "source": {"producer": "Canva"},
}


class TestDerivation:
    def test_ne_modifie_pas_la_source_et_inverse_la_composition(self) -> None:
        before = copy.deepcopy(SPEC)
        derived, report = derive_spec(SPEC, seed=1, source_sha256="abc")

        assert before == SPEC
        assert report.mirrored is True
        columns = derived["bands"][0]["columns"]
        # Les colonnes sont inversées : la large à gauche, l'étroite à droite.
        assert [c["w_mm"] for c in columns] == [120.0, 55.0]
        assert columns[1]["x_mm"] == 210.0 - 10.0 - 55.0
        assert columns[1]["sections"][0]["side"] is None
        # La bande latérale pleine passe à droite ; la photo carrée change de forme.
        assert derived["page_decorations"][0]["x_mm"] == 140.0
        assert derived["header"]["photo"]["shape"] == "rect"
        assert derived["header"]["photo"]["x_mm"] == 150.0
        # Un élément ancré à droite garde son ancrage ; ses bornes sont inversées.
        headline = derived["header"]["elements"][1]
        assert headline["align"] == "right"
        assert (headline["x_mm"], headline["rx_mm"]) == (10.0, 60.0)

    def test_ne_partage_aucune_police_avec_la_source(self) -> None:
        derived, report = derive_spec(SPEC, seed=0)
        assert report.shares_a_font is False
        assert "Poppins" not in {f["family"] for f in derived["fonts"]}
        # Le nom seul prend la police de titre ; les titres de rubrique restent en police de texte.
        assert derived["styles"]["header.first_name"]["family"] == report.display_font
        assert derived["styles"]["section_title"]["family"] == report.body_font
        assert derived["styles"]["body"]["family"] == report.body_font

    def test_change_de_teinte_et_recolore_le_decor(self) -> None:
        derived, report = derive_spec(SPEC, seed=0)
        assert report.source_primary == "#1B3A6B"
        assert report.primary in PRIMARIES
        assert report.primary != "#1F4FA3"  # le bleu est trop proche de la source
        assert derived["page_decorations"][0]["fill"] != "#1B3A6B"

    def test_garde_les_titres_et_trace_la_provenance(self) -> None:
        derived, report = derive_spec(SPEC, seed=3, source_sha256="deadbeef")
        assert report.title_uppercase is None
        assert derived["styles"]["section_title"]["uppercase"] is True
        assert derived["styles"]["section_title"]["letter_spacing_pt"] == 1.5
        assert derived["page_decorations"][1]["stroke_pt"] == 0.75
        assert derived["meta"]["origin"] == "karria"
        assert derived["source"]["producer"] == "Canva"
        assert derived["source"]["derived_from_sha256"] == "deadbeef"
        assert derived["source"]["derivation"]["seed"] == 3
        assert derived["source"]["derivation"]["fonts"] == {
            "display": report.display_font,
            "body": report.body_font,
        }

    def test_deux_graines_donnent_deux_creations_differentes(self) -> None:
        a, ra = derive_spec(SPEC, seed=0)
        b, rb = derive_spec(SPEC, seed=1)
        assert (ra.display_font, ra.primary) != (rb.display_font, rb.primary)
        assert a["styles"]["body"]["family"] != b["styles"]["body"]["family"]


def test_pas_de_miroir_quand_une_rubrique_a_une_colonne_laterale() -> None:
    spec = copy.deepcopy(SPEC)
    spec["bands"][0]["columns"][0]["sections"][0]["side"] = "left"
    derived, report = derive_spec(spec, seed=0)
    assert report.mirrored is False
    assert derived["bands"][0]["columns"][0]["x_mm"] == 10.0


def test_les_choix_evitent_la_source() -> None:
    assert choose_pairing({"Playfair Display"}, 1) != ("Playfair Display", "Lato")
    assert choose_primary("#1F4FA3", 0) != "#1F4FA3"
    assert choose_primary(None, 0) == PRIMARIES[0]
