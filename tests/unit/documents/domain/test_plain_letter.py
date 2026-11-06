"""La lettre sobre et le choix du style de lettre (ADR-0036)."""

from __future__ import annotations

import pytest

from karria.modules.documents.domain.palette import recolor_spec
from karria.modules.documents.domain.plain_letter import (
    PLAIN_BOX_RATIO,
    PLAIN_FAMILY,
    PLAIN_LETTER_SAMPLE,
    PLAIN_LETTER_VERSION,
    letter_style_served,
    plain_letter_spec,
)

pytestmark = pytest.mark.unit

ROLES = {
    "header.full_name",
    "header.headline",
    "header.address",
    "header.phone",
    "header.email",
    "recipient.company",
    "recipient.city",
    "date_line",
    "subject",
    "greeting",
    "paragraph",
    "closing",
    "signature",
}


class TestSpecification:
    def test_une_lettre_d_affaires_francaise_en_carlito_onze_points(self) -> None:
        spec = plain_letter_spec()
        assert spec["kind"] == "letter"
        assert spec["version"] == PLAIN_LETTER_VERSION
        assert spec["page"] == {"width_mm": 210.0, "height_mm": 297.0, "background": "#FFFFFF"}
        assert set(spec["styles"]) == ROLES
        assert {s["family"] for s in spec["styles"].values()} == {PLAIN_FAMILY}
        assert spec["styles"]["paragraph"]["size_pt"] == 11.0
        assert spec["styles"]["subject"]["weight"] == 700
        assert spec["styles"]["paragraph"]["box_h_mm"] == pytest.approx(
            11.0 * PLAIN_BOX_RATIO * 25.4 / 72, abs=0.001
        )
        assert [f["weight"] for f in spec["fonts"]] == [400, 700]

    def test_l_en_tete_expediteur_a_gauche_destinataire_et_date_a_droite(self) -> None:
        blocks = plain_letter_spec()["header"]["blocks"]
        anchors = [b["anchor"] for b in blocks]
        assert anchors == ["left", "right", "right"]
        assert [e["field"] for e in blocks[0]["elements"]] == [
            "full_name",
            "headline",
            "address",
            "phone",
            "email",
        ]
        assert [e["field"] for e in blocks[1]["elements"]] == ["company", "city"]
        assert [e["field"] for e in blocks[2]["elements"]] == ["date_line"]
        assert blocks[0]["x_mm"] == 25.0
        assert blocks[1]["right_mm"] == 25.0
        assert blocks[0]["y_mm"] < blocks[1]["y_mm"] < blocks[2]["y_mm"]

    def test_le_corps_dans_l_ordre_d_une_lettre_paragraphes_justifies(self) -> None:
        body = plain_letter_spec()["body"]
        assert (body["x_mm"], body["w_mm"]) == (25.0, 160.0)
        assert [b["role"] for b in body["blocks"]] == [
            "subject",
            "greeting",
            "paragraph",
            "closing",
            "signature",
        ]
        paragraph = body["blocks"][2]
        assert paragraph["align"] == "justify"
        assert paragraph["gap_between_mm"] == 5.4
        assert body["blocks"][0]["gap_before_mm"] == 0.0
        assert body["top_mm"] > plain_letter_spec()["header"]["blocks"][2]["y_mm"]

    def test_elle_ne_porte_aucune_couleur_de_modele_et_reste_neutre_une_fois_recoloree(
        self,
    ) -> None:
        spec = plain_letter_spec()
        assert "section_title" not in spec["styles"]
        assert recolor_spec(spec, "#7B1E2B") is spec

    def test_chaque_appel_rend_une_specification_neuve(self) -> None:
        a, b = plain_letter_spec(), plain_letter_spec()
        a["styles"]["paragraph"]["color"] = "#FF0000"
        assert b["styles"]["paragraph"]["color"] == "#111111"

    def test_le_contenu_d_exemple_est_une_lettre_complete(self) -> None:
        assert len(PLAIN_LETTER_SAMPLE["paragraphs"]) == 5
        assert PLAIN_LETTER_SAMPLE["subject"].startswith("Candidature")
        assert PLAIN_LETTER_SAMPLE["date_line"] == "Lyon, le 6 novembre 2026"
        assert PLAIN_LETTER_SAMPLE["sender"]["city"] == "Lyon"


class TestStyleServi:
    def test_sans_lettre_assortie_la_sobre_est_servie_quoi_qu_on_demande(self) -> None:
        for asked in (None, "template", "plain"):
            assert letter_style_served(asked, has_template_letter=False) == "plain"

    def test_avec_une_lettre_assortie_le_choix_decide_et_l_assortie_est_le_defaut(self) -> None:
        assert letter_style_served(None, has_template_letter=True) == "template"
        assert letter_style_served("template", has_template_letter=True) == "template"
        assert letter_style_served("plain", has_template_letter=True) == "plain"

    def test_un_style_inconnu_est_refuse(self) -> None:
        with pytest.raises(ValueError, match="inconnu"):
            letter_style_served("fancy", has_template_letter=True)
