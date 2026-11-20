"""Le bloc de titre posé comme un tout : nom dans un seul caractère, poste visé, précision."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from documents.infrastructure.fakes import cv_font_set
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.header_layout import (
    AVAILABILITY_STYLE,
    HEADLINE_STYLE,
    NAME_STYLE,
    layout_header,
)
from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer, fit_script

pytestmark = pytest.mark.unit

Json = dict[str, Any]


def style(size: float, *, weight: int = 400, upper: bool = False, spacing: float = 0.0) -> Json:
    return {
        "family": "Testa",
        "weight": weight,
        "italic": False,
        "size_pt": size,
        "color": "#111111",
        "letter_spacing_pt": spacing,
        "uppercase": upper,
        "box_h_mm": round(size * 1.2 * 25.4 / 72, 3),
        "pitch_mm": None,
    }


def a_spec(elements: list[Json], *, band_top: float = 70.0) -> Json:
    """Une page A4, une bande d'une colonne sous l'en-tête."""
    return {
        "page": {"width_mm": 210.0, "height_mm": 297.0, "background": "#FFFFFF"},
        "first_band_top_mm": band_top,
        "content_bottom_mm": 250.0,
        "page_decorations": [],
        "fonts": [],
        "header": {"elements": elements, "photo": None, "decorations": []},
        "styles": {
            "header.first_name": style(30, weight=700, upper=True, spacing=3.0),
            "header.last_name": style(24, upper=True, spacing=3.0),
            "header.full_name": style(30, weight=700),
            "header.headline": style(11, upper=True, spacing=1.0),
            "header.phone": style(9),
            "section_title": style(12, weight=700),
            "summary.summary": style(10),
        },
        "bands": [
            {
                "y_sample": band_top,
                "bottom_sample": 120.0,
                "columns": [
                    {
                        "x_mm": 20.0,
                        "w_mm": 170.0,
                        "sections": [
                            {
                                "kind": "summary",
                                "margin_top_mm": 0.0,
                                "untitled": True,
                                "slots": [],
                                "decorations": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def stacked_right() -> list[Json]:
    """Prénom gras 30 pt au-dessus d'un nom maigre 24 pt, au fer à droite (Frise Graphite)."""
    return [
        {
            "field": "first_name",
            "style": "header.first_name",
            "x_mm": 110.0,
            "y_mm": 20.0,
            "w_mm": 80.0,
        },
        {
            "field": "last_name",
            "style": "header.last_name",
            "x_mm": 140.0,
            "y_mm": 33.0,
            "w_mm": 50.0,
        },
        {
            "field": "headline",
            "style": "header.headline",
            "x_mm": 190.0,
            "y_mm": 46.0,
            "w_mm": None,
            "synthesized": True,
        },
    ]


def a_view(first: str = "Marc", last: str = "Takoumba", **more: str) -> Json:
    header = {
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}".strip(),
        "headline": "Data Analyst",
        "availability": "",
        "phone": "",
        "email": "",
        "address": "",
        "website": "",
    }
    header.update(more)
    return {"header": header, "sections": []}


@pytest.fixture(scope="module")
def fonts() -> FontSet:
    return cv_font_set()


def by_field(spec: Json) -> dict[str, Json]:
    return {e["field"]: e for e in spec["header"]["elements"]}


class TestNom:
    def test_prenom_et_nom_prennent_le_meme_caractere(self, fonts: FontSet) -> None:
        spec = a_spec(stacked_right())
        out = layout_header(spec, a_view(), fonts, photo=False)
        laid = by_field(out)
        assert laid["first_name"]["style"] == laid["last_name"]["style"] == NAME_STYLE
        name = out["styles"][NAME_STYLE]
        # Le style du plus grand des deux : gras, en capitales — jamais plus grand que lui.
        assert name["weight"] == 700
        assert name["uppercase"] is True
        assert name["size_pt"] <= 30.0
        # La spécification d'origine n'est pas touchée.
        assert spec["header"]["elements"][0]["style"] == "header.first_name"
        assert NAME_STYLE not in spec["styles"]

    def test_les_lignes_gardent_le_bord_quelles_partagent(self, fonts: FontSet) -> None:
        out = layout_header(a_spec(stacked_right()), a_view(), fonts, photo=False)
        laid = by_field(out)
        for field in ("first_name", "last_name"):
            assert laid[field]["align"] == "right"
            assert laid[field]["rx_mm"] == pytest.approx(190.0)
            assert laid[field]["laid"] is True
        assert laid["first_name"]["y_mm"] < laid["last_name"]["y_mm"]

    def test_le_bloc_ne_depasse_pas_la_hauteur_quil_avait(self, fonts: FontSet) -> None:
        spec = a_spec(stacked_right())
        out = layout_header(spec, a_view(), fonts, photo=False)
        laid = by_field(out)
        box = out["styles"][NAME_STYLE]["box_h_mm"]
        template_bottom = 33.0 + spec["styles"]["header.last_name"]["box_h_mm"]
        assert laid["first_name"]["y_mm"] >= 20.0 - 0.01
        assert laid["last_name"]["y_mm"] + box <= template_bottom + 0.01

    def test_un_nom_tres_long_reduit_les_deux_lignes_ensemble(self, fonts: FontSet) -> None:
        out = layout_header(
            a_spec(stacked_right()),
            a_view("Marie-Charlotte", "de La Rochefoucauld-Montmorency"),
            fonts,
            photo=False,
        )
        name = out["styles"][NAME_STYLE]
        laid = by_field(out)
        assert name["size_pt"] < 30.0
        for field in ("first_name", "last_name"):
            # Chaque ligne tient entre la marge et son bord, dans le même corps.
            assert laid[field]["x_mm"] >= 20.0 - 0.5
            assert laid[field]["w_mm"] <= 170.5
            assert "box_w_mm" not in laid[field]

    def test_sur_une_ligne_prenom_puis_nom_se_suivent(self, fonts: FontSet) -> None:
        elements = [
            {
                "field": "last_name",
                "style": "header.first_name",
                "x_mm": 85.0,
                "y_mm": 12.0,
                "w_mm": 70.0,
            },
            {
                "field": "first_name",
                "style": "header.last_name",
                "x_mm": 160.0,
                "y_mm": 12.0,
                "w_mm": 39.0,
            },
            {
                "field": "headline",
                "style": "header.headline",
                "x_mm": 122.0,
                "y_mm": 33.0,
                "w_mm": 76.0,
            },
        ]
        laid = by_field(layout_header(a_spec(elements), a_view(), fonts, photo=False))
        first, last = laid["first_name"], laid["last_name"]
        assert first["style"] == last["style"] == NAME_STYLE
        assert first["y_mm"] == last["y_mm"]
        # À la française, dans un même caractère : le prénom d'abord, le nom ensuite.
        assert first["x_mm"] + first["w_mm"] < last["x_mm"]
        # L'ensemble reste appuyé sur le bord droit du modèle.
        assert last["x_mm"] + last["w_mm"] == pytest.approx(199.0, abs=0.1)

    def test_un_nom_de_rappel_en_petit_nest_pas_touche(self, fonts: FontSet) -> None:
        elements = [
            {
                "field": "full_name",
                "style": "header.phone",
                "x_mm": 160.0,
                "y_mm": 9.0,
                "w_mm": 30.0,
            },
            *stacked_right(),
        ]
        laid = layout_header(a_spec(elements), a_view(), fonts, photo=False)["header"]["elements"]
        reminder = next(e for e in laid if e["field"] == "full_name")
        assert reminder["style"] == "header.phone"
        assert "laid" not in reminder

    def test_lexemple_du_modele_garde_son_dessin(self, fonts: FontSet) -> None:
        spec = a_spec(stacked_right())
        assert layout_header(spec, {**a_view(), "sample": True}, fonts, photo=False) is spec
        assert layout_header(spec, {"kind": "letter", "header": {}}, fonts, photo=False) is spec
        assert layout_header(spec, {"header": {}, "sections": []}, fonts, photo=False) is spec

    def test_sans_nom_rien_nest_pose(self, fonts: FontSet) -> None:
        out = layout_header(a_spec(stacked_right()), a_view("", ""), fonts, photo=False)
        assert all("laid" not in e for e in out["header"]["elements"])


class TestPosteVise:
    def test_le_poste_synthetise_suit_lalignement_du_nom(self, fonts: FontSet) -> None:
        out = layout_header(a_spec(stacked_right()), a_view(), fonts, photo=False)
        headline = by_field(out)["headline"]
        assert headline["style"] == HEADLINE_STYLE
        assert headline["align"] == "right"
        assert headline["rx_mm"] == pytest.approx(190.0)
        # Lisible : jamais réduit sous 80 % sur une ligne.
        assert out["styles"][HEADLINE_STYLE]["size_pt"] >= 0.8 * 11
        assert "lines" not in headline

    def test_un_poste_tres_long_se_replie_sur_deux_lignes(self, fonts: FontSet) -> None:
        text = "Développeuse full-stack JavaScript et cheffe de projet numérique confirmée " * 2
        out = layout_header(
            a_spec(stacked_right()), a_view(headline=text.strip()), fonts, photo=False
        )
        headline = by_field(out)["headline"]
        assert headline["lines"] >= 2
        assert headline["box_w_mm"] > 100.0
        assert headline["pitch_mm"] > 0.0

    def test_un_filet_contre_le_bord_arrete_le_texte(self, fonts: FontSet) -> None:
        spec = a_spec(stacked_right())
        spec["header"]["decorations"].append(
            {"kind": "vline", "x_mm": 187.0, "y_mm": 44.0, "h_mm": 12.0, "w_mm": 0.0}
        )
        headline = by_field(layout_header(spec, a_view(), fonts, photo=False))["headline"]
        assert headline["rx_mm"] < 187.0

    def test_un_poste_en_tete_de_flux_recoit_son_bord_et_sa_largeur(self, fonts: FontSet) -> None:
        elements = [
            {
                "field": "full_name",
                "style": "header.full_name",
                "x_mm": 32.0,
                "y_mm": 29.0,
                "w_mm": 96.0,
            },
            {
                "field": "headline",
                "style": "header.headline",
                "x_mm": 128.0,
                "y_mm": 42.0,
                "w_mm": None,
                "flow": True,
                "gap_mm": 3.0,
                "synthesized": True,
            },
        ]
        out = layout_header(
            a_spec(elements, band_top=42.0),
            a_view(availability="Disponible immédiatement"),
            fonts,
            photo=False,
        )
        headline = by_field(out)["headline"]
        assert headline["x_mm"] == pytest.approx(32.0)  # sous le nom, pas à son bord droit
        assert headline["flow_w_mm"] > 100.0
        assert headline["flow_extra"] == AVAILABILITY_STYLE
        html = HtmlRenderer(
            a_spec(elements, band_top=42.0), a_view(availability="Dispo"), fonts, photo=None
        ).render()
        assert ">Dispo</div>" in html


class TestPrecision:
    def test_la_precision_a_sa_ligne_sous_le_poste_vise(self, fonts: FontSet) -> None:
        view = a_view(availability="Recherche alternance 24 mois · Disponible immédiatement")
        out = layout_header(a_spec(stacked_right()), view, fonts, photo=False)
        laid = by_field(out)
        added, headline = laid["availability"], laid["headline"]
        assert added["style"] == AVAILABILITY_STYLE
        assert added["y_mm"] > headline["y_mm"]
        assert added["align"] == headline["align"] == "right"
        extra = out["styles"][AVAILABILITY_STYLE]
        # La famille du poste visé, en bas de casse, lisible.
        assert extra["family"] == out["styles"][HEADLINE_STYLE]["family"]
        assert extra["uppercase"] is False
        assert extra["size_pt"] >= 8.5

    def test_sans_poste_vise_elle_prend_sa_place(self, fonts: FontSet) -> None:
        view = a_view(headline="", availability="Disponible en septembre")
        laid = by_field(layout_header(a_spec(stacked_right()), view, fonts, photo=False))
        assert laid["availability"]["y_mm"] == pytest.approx(46.0)
        assert "laid" not in laid["headline"]

    def test_ce_qui_etait_dessous_descend_dautant(self, fonts: FontSet) -> None:
        elements = [
            *stacked_right(),
            {"field": "phone", "style": "header.phone", "x_mm": 150.0, "y_mm": 52.0, "w_mm": 30.0},
            {"field": "email", "style": "header.phone", "x_mm": 20.0, "y_mm": 271.0, "w_mm": 40.0},
        ]
        view = a_view(
            availability="Recherche alternance 24 mois", phone="06 12 34 56 78", email="a@b.fr"
        )
        laid = by_field(layout_header(a_spec(elements), view, fonts, photo=False))
        assert laid["phone"]["y_mm"] > 52.0
        assert laid["email"]["y_mm"] == 271.0

    def test_le_rendu_ecrit_la_precision_avec_son_chemin(self, fonts: FontSet) -> None:
        view = a_view(availability="Disponible immédiatement")
        view["paths"] = {"header": {"availability": "identity.availability"}}
        html = HtmlRenderer(
            a_spec(stacked_right()), view, fonts, photo=None, anchored=True
        ).render()
        assert 'data-chemin="identity.availability"' in html
        assert "Disponible immédiatement</div>" in html
        assert 'class="s-header-title-name" data-hb="1"' in html


class TestCoordonnees:
    def test_un_bord_gauche_partage_lemporte_sur_un_fer_a_droite(self, fonts: FontSet) -> None:
        elements = [
            *stacked_right(),
            {
                "field": "phone",
                "style": "header.phone",
                "x_mm": 26.4,
                "y_mm": 27.0,
                "w_mm": 24.0,
                "align": "right",
                "rx_mm": 50.4,
            },
            {
                "field": "email",
                "style": "header.phone",
                "x_mm": 26.4,
                "y_mm": 31.8,
                "w_mm": 42.0,
                "align": "right",
                "rx_mm": 68.4,
            },
        ]
        view = a_view(phone="06 12 34 56 78", email="marc.takoumba@example.com")
        laid = by_field(layout_header(a_spec(elements), view, fonts, photo=False))
        for field in ("phone", "email"):
            assert "align" not in laid[field]
            assert laid[field]["x_mm"] == pytest.approx(26.4)

    def test_des_bords_droits_partages_font_un_fer_a_droite(self, fonts: FontSet) -> None:
        elements = [
            *stacked_right(),
            {"field": "address", "style": "header.phone", "x_mm": 30.0, "y_mm": 76.0, "w_mm": 40.0},
            {"field": "phone", "style": "header.phone", "x_mm": 46.0, "y_mm": 83.0, "w_mm": 24.0},
        ]
        view = a_view(phone="06 12 34 56 78", address="Ivry-sur-Seine")
        laid = by_field(layout_header(a_spec(elements), view, fonts, photo=False))
        for field in ("phone", "address"):
            assert laid[field]["align"] == "right"
            assert laid[field]["rx_mm"] == pytest.approx(70.0)

    def test_un_centrage_que_la_geometrie_dement_est_retire(self, fonts: FontSet) -> None:
        elements = [
            *stacked_right(),
            {
                "field": "email",
                "style": "header.phone",
                "x_mm": 15.0,
                "y_mm": 271.0,
                "w_mm": 41.0,
                "align": "center",
                "cx_mm": 174.0,
            },
            {
                "field": "phone",
                "style": "header.phone",
                "x_mm": 60.0,
                "y_mm": 280.0,
                "w_mm": 22.0,
                "align": "left",
                "box_w_mm": 23.5,
                "pitch_mm": 0.0,
                "sample_lines": [{"text": "a", "x_mm": 60.0, "y_mm": 280.0}],
            },
        ]
        view = a_view(phone="06 12 34 56 78", email="a@b.fr")
        laid = by_field(layout_header(a_spec(elements), view, fonts, photo=False))
        assert "align" not in laid["email"]
        assert "cx_mm" not in laid["email"]
        assert "box_w_mm" not in laid["phone"]  # une « boîte » d'une seule ligne n'en est pas une


class TestPremiereBande:
    def test_seul_ce_qui_est_au_dessus_de_la_bande_la_pousse(self, fonts: FontSet) -> None:
        elements = [
            *stacked_right(),
            {"field": "email", "style": "header.phone", "x_mm": 20.0, "y_mm": 271.0, "w_mm": 40.0},
        ]
        html = HtmlRenderer(a_spec(elements), a_view(email="a@b.fr"), fonts, photo=None).render()
        # Le pied de page n'est pas compté : il renvoyait tout le CV en page 2.
        assert '<div class="s-header-phone" style="position:absolute' in html
        assert html.count('data-hb="1"') == 3

    def test_le_contenu_dune_deuxieme_page_se_coupe_au_dessus_du_pied(self) -> None:
        spec = a_spec(
            [{"field": "email", "style": "header.phone", "x_mm": 20.0, "y_mm": 271.0, "w_mm": 40.0}]
        )
        assert "cut-29.00*mm" in fit_script(spec, live=False)
        assert "cut-0.00*mm" in fit_script(a_spec(stacked_right()), live=False)

    def test_une_bande_a_cote_du_nom_nest_pas_poussee(self, fonts: FontSet) -> None:
        spec = a_spec(stacked_right(), band_top=20.0)
        spec["bands"][0]["columns"][0].update(x_mm=20.0, w_mm=70.0)  # le profil à gauche du nom
        out = layout_header(copy.deepcopy(spec), a_view(), fonts, photo=False)
        assert all("pushes_band" not in e for e in out["header"]["elements"])
        under = layout_header(a_spec(stacked_right()), a_view(), fonts, photo=False)
        assert all(e.get("pushes_band") for e in under["header"]["elements"])


class TestPileDeCoordonneesPoussee:
    def test_une_coordonnee_poussee_entraine_celles_du_dessous(self, fonts: FontSet) -> None:
        """La précision allonge le bloc de titre : le site, assez long pour passer sous lui,
        descend — et le courriel et le téléphone empilés sous lui descendent avec lui."""
        contact = [
            {
                "field": field,
                "style": "header.phone",
                "x_mm": 190.0 - width,
                "y_mm": y,
                "w_mm": width,
                "align": "right",
                "rx_mm": 190.0,
            }
            for field, y, width in (
                ("address", 30.0, 22.0),
                ("website", 34.0, 26.0),
                ("email", 38.0, 26.0),
                ("phone", 42.0, 18.0),
            )
        ]
        name = {"field": "full_name", "style": "header.full_name", "x_mm": 70.0, "y_mm": 10.0}
        headline = {"field": "headline", "style": "header.headline", "x_mm": 85.0, "y_mm": 24.0}
        for e, width in ((name, 70.0), (headline, 40.0)):
            e.update(w_mm=width, align="center", cx_mm=105.0)
        spec = a_spec([name, headline, *contact])
        view = a_view(
            availability="Recherche alternance 24 mois · Disponible immédiatement",
            phone="06 12 34 56 78",
            email="marc@example.com",
            address="Ivry",
            website="linkedin.com/in/" + "marc-takoumba-" * 5,
        )
        after = by_field(layout_header(spec, view, fonts, photo=False))
        deltas = {e["field"]: round(after[e["field"]]["y_mm"] - e["y_mm"], 2) for e in contact}
        # Seul le site, très long, passe sous le bloc de titre ; il descend, et les deux
        # lignes empilées sous lui descendent d'autant. L'adresse, au-dessus, reste.
        assert deltas["website"] > 0
        assert deltas["email"] == deltas["phone"] == deltas["website"]
        assert deltas["address"] == 0
