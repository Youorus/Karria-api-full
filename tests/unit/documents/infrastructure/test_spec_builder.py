"""PDF de modèle + rôles des lignes → spécification canonique de mise en page.

Deux modèles construits en mémoire (voir `fakes.py`) : un CV à deux colonnes
et deux bandes, et un CV à une colonne au nom centré. Les étiquettes sont
écrites à la main, ligne par ligne, comme les rendrait le modèle de vision.
"""

from __future__ import annotations

from typing import Any

import pytest

from documents.infrastructure.fakes import (
    BOLD,
    CENTERED_ROLES,
    CV_ROLES,
    REGULAR,
    SERVED,
    PdfBuilder,
    centered_template_pdf,
    cv_font_set,
    cv_fonts,
    cv_template_pdf,
    labels_for,
    png,
)
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument, PdfPage
from karria.modules.documents.infrastructure.spec_builder import (
    SPEC_VERSION,
    Line,
    SpecBuilder,
    collapse_spaced,
    is_pictogram_text,
    majority_alignment,
    med,
    reference,
)

pytestmark = pytest.mark.unit

Json = dict[str, Any]


def build(
    pdf: bytes, roles: dict[str, tuple[str, str, int]], **meta: Any
) -> tuple[Json, Json, dict[str, bytes]]:
    """La spécification d'un PDF, d'après des rôles donnés par texte."""
    with PdfDocument(pdf) as doc:
        page = doc.read()
        labels = labels_for(page.lines, roles)
        labels.update(meta)
        return SpecBuilder(doc, page, labels, cv_font_set(), SERVED).build()


@pytest.fixture(scope="module")
def cv() -> tuple[Json, Json, dict[str, bytes]]:
    return build(cv_template_pdf(), CV_ROLES)


@pytest.fixture(scope="module")
def spec(cv: tuple[Json, Json, dict[str, bytes]]) -> Json:
    return cv[0]


@pytest.fixture(scope="module")
def centered() -> tuple[Json, Json, dict[str, bytes]]:
    return build(centered_template_pdf(), CENTERED_ROLES, photo_shape=None)


def sections(spec: Json) -> dict[str, Json]:
    return {s["kind"]: s for b in spec["bands"] for c in b["columns"] for s in c["sections"]}


def slot(section: Json, key: str) -> Json:
    return next(s for s in section["slots"] if s["key"] == key)


# ---------------------------------------------------------------------------
# Fonctions de base
# ---------------------------------------------------------------------------


class TestOutils:
    def test_la_mediane_ignore_les_inconnues(self) -> None:
        assert med([1.0, None, 3.0]) == 2.0
        assert med([None], default=7.0) == 7.0
        assert med([]) == 0.0

    def test_la_reference_de_deux_mesures_est_la_premiere(self) -> None:
        assert reference([0.0, 4.0]) == 0.0
        assert reference([0.0, 4.0, 0.2]) == 0.2
        assert reference([3.0]) == 3.0

    def test_des_lettres_espacees_sont_recollees(self) -> None:
        assert collapse_spaced("B E N J A M I N") == "BENJAMIN"
        assert collapse_spaced("Jean Dupont") == "Jean Dupont"

    def test_une_ligne_connait_sa_droite_et_son_bas(self) -> None:
        raw = {"id": "L1", "text": "A", "x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0}
        line = Line(raw, {"role": "skill", "section": "skills", "item": "2"})
        assert (line.right, line.bottom, line.item) == (4.0, 6.0, 2)


# ---------------------------------------------------------------------------
# Le modèle à deux colonnes
# ---------------------------------------------------------------------------


class TestPageEtMeta:
    def test_la_page_les_meta_et_la_source(self, spec: Json) -> None:
        assert spec["version"] == SPEC_VERSION
        assert spec["source"] == {"producer": "Canva"}
        assert spec["meta"]["title"] == "Azur"
        assert spec["meta"]["photo_shape"] == "circle"
        assert spec["page"] == {"width_mm": 210.0, "height_mm": 297.0, "background": "#FFFFFF"}
        assert spec["first_band_top_mm"] == pytest.approx(56.51, abs=0.05)
        assert spec["content_bottom_mm"] == pytest.approx(177.79, abs=0.05)

    def test_les_polices_servies_comptent_le_poste_synthetise(self, spec: Json) -> None:
        assert spec["fonts"] == [
            {"family": "Testa", "weight": 400, "italic": False},
            {"family": "Testa", "weight": 500, "italic": False},
            {"family": "Testa", "weight": 700, "italic": False},
        ]


class TestStyles:
    def test_un_style_par_role_et_par_rubrique(self, spec: Json) -> None:
        styles = spec["styles"]
        for key in (
            "section_title",
            "contact.email",
            "skills.skill",
            "summary.summary",
            "experiences.item_title",
            "experiences.item_dates",
            "experiences.bullet",
            "educations.item_title",
            "header.full_name",
            "header.phone",
            "header.headline",
        ):
            assert key in styles

    def test_le_titre_de_rubrique_garde_police_couleur_et_capitales(self, spec: Json) -> None:
        title = spec["styles"]["section_title"]
        assert (title["family"], title["weight"], title["italic"]) == ("Testa", 700, False)
        assert title["size_pt"] == 11.0
        assert title["color"] == "#336699"
        assert title["uppercase"] is True
        assert title["source_font"] == BOLD

    def test_le_paragraphe_garde_son_interligne_mesure(self, spec: Json) -> None:
        summary = spec["styles"]["summary.summary"]
        assert summary["pitch_mm"] == pytest.approx(4.5, abs=0.01)
        assert summary["source_font"] == REGULAR

    def test_le_poste_vise_est_synthetise_dans_la_grammaire_du_modele(self, spec: Json) -> None:
        headline = spec["styles"]["header.headline"]
        assert headline["synthesized"] is True
        assert headline["weight"] == 500
        assert headline["color"] == "#336699"
        assert headline["uppercase"] is True


class TestBandesEtColonnes:
    def test_deux_bandes_de_deux_colonnes(self, spec: Json) -> None:
        bands = spec["bands"]
        assert len(bands) == 2
        assert [c["x_mm"] for c in bands[0]["columns"]] == [15.0, 80.0]
        assert [[s["kind"] for s in c["sections"]] for c in bands[0]["columns"]] == [
            ["contact", "skills"],
            ["summary", "experiences"],
        ]
        assert [[s["kind"] for s in c["sections"]] for c in bands[1]["columns"]] == [
            ["languages"],
            ["educations"],
        ]

    def test_la_colonne_de_gauche_sarrete_avant_celle_de_droite(self, spec: Json) -> None:
        left, right = spec["bands"][0]["columns"]
        assert left["x_mm"] + left["w_mm"] <= right["x_mm"] - 2

    def test_lecart_entre_bandes_est_mesure(self, spec: Json) -> None:
        first, second = spec["bands"]
        assert first["gap_after_mm"] == pytest.approx(
            second["y_sample"] - first["bottom_sample"], abs=0.01
        )
        assert "gap_after_mm" not in second


class TestRubriques:
    def test_le_contact_a_ses_emplacements_et_ses_pictogrammes(
        self, cv: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        spec, _, assets = cv
        contact = sections(spec)["contact"]
        assert contact["slot_order"] == ["email", "address"]
        assert slot(contact, "email")["dx_mm"] == 5.0
        assert contact["icons"]["email"]["file"] == "icon_email.png"
        assert contact["icons"]["email"]["dx_mm"] == -5.0
        assert assets["icon_email.png"].startswith(b"\x89PNG")

    def test_une_puce_ronde_est_vue_ronde(self, spec: Json) -> None:
        skills = sections(spec)["skills"]
        assert skills["slot_order"] == ["subsection_title", "skill"]
        assert skills["marker"]["shape"] == "circle"
        assert skills["marker"]["size_mm"] == pytest.approx(
            1.2, abs=0.05
        )  # boîte peinte, au pixel près
        assert skills["marker"]["color"] == "#336699"
        assert slot(skills, "skill")["repeat_margin_mm"] is not None

    def test_une_puce_carree_est_vue_carree(self, spec: Json) -> None:
        marker = sections(spec)["experiences"]["marker"]
        assert marker["shape"] == "square"
        assert marker["dx_mm"] == pytest.approx(-3.0, abs=0.01)

    def test_un_paragraphe_aligne_a_droite_est_justifie(self, spec: Json) -> None:
        assert slot(sections(spec)["summary"], "summary")["align"] == "justify"

    def test_deux_champs_sur_une_ligne_et_leur_separateur(self, spec: Json) -> None:
        exp = sections(spec)["experiences"]
        assert exp["slot_order"] == ["item_title+item_dates", "item_subtitle", "bullet"]
        joined = slot(exp, "item_title+item_dates")
        assert joined["roles"] == ["item_title", "item_dates"]
        assert joined["gap_mm"] is not None
        sep = joined["separator"]
        assert sep["stroke"] == "#999999"
        assert sep["stroke_pt"] == 0.5
        assert sep["h_mm"] == pytest.approx(4.5, abs=0.01)
        assert exp["title_sep"] == "Developpeur"
        assert exp["item_gap_mm"] > 0

    def test_la_frise_est_un_connecteur_etire_avec_le_retrait_du_dernier(self, spec: Json) -> None:
        decos = sections(spec)["experiences"]["decorations"]
        line = next(d for d in decos if d["kind"] == "vline")
        assert line["anchor"] == "item"
        assert line["stretch"] is True
        assert line["connector"] is True
        assert "last_bottom_inset_mm" in line

    def test_la_pastille_de_frise_est_attachee_a_lintitule(self, spec: Json) -> None:
        decos = sections(spec)["experiences"]["decorations"]
        dot = next(d for d in decos if d["kind"] == "curve")
        assert dot["attach"] == "row"
        assert dot["ref_role"] == "item_title"
        assert dot["anchor"] == "item"
        # Le décor du second élément n'est pas dupliqué.
        assert sum(d["kind"] == "curve" for d in decos) == 1

    def test_un_filet_sous_la_rubrique_est_ancre_apres(self, spec: Json) -> None:
        (after,) = sections(spec)["summary"]["decorations"]
        assert after["kind"] == "hline"
        assert after["anchor"] == "after"
        assert after["stroke"] == "#CCCCCC"


class TestEnteteEtPage:
    def test_les_elements_de_lentete_sont_en_position_absolue(self, spec: Json) -> None:
        fields = {e["field"]: e for e in spec["header"]["elements"]}
        assert fields["full_name"]["x_mm"] == 20.0
        assert fields["phone"]["x_mm"] == 140.0
        assert fields["phone"]["icon"]["file"] == "icon_phone.png"

    def test_le_poste_synthetise_se_place_contre_le_filet_vertical(self, spec: Json) -> None:
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        assert headline["synthesized"] is True
        assert headline["x_mm"] == pytest.approx(24.0 + 3.5, abs=0.01)
        assert "align" not in headline

    def test_la_photo_ronde_est_un_carre_centre_dans_son_cadre(
        self, cv: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        spec, _, assets = cv
        photo = spec["header"]["photo"]
        assert photo["shape"] == "circle"
        assert (photo["w_mm"], photo["h_mm"]) == (25.0, 25.0)
        assert photo["y_mm"] == pytest.approx(10.5, abs=0.01)
        assert photo["sample_object_position"] == "50.0% 50.0%"
        assert "sample_photo.png" in assets

    def test_les_ornements_hauts_vont_dans_lentete(self, spec: Json) -> None:
        kinds = [(d["kind"], d["fill"] or d["stroke"]) for d in spec["header"]["decorations"]]
        assert ("rect", "#336699") in kinds
        assert ("vline", "#999999") in kinds

    def test_le_filet_entre_bandes_est_ancre_a_la_bande(self, spec: Json) -> None:
        decos = spec["page_decorations"]
        band = next(d for d in decos if d["anchor"] == "band")
        assert band["kind"] == "hline"
        assert band["band"] == 0
        page = next(d for d in decos if d["anchor"] == "page")
        assert page["fill"] == "#EEEEEE"


class TestContenuDExemple:
    def test_lexemple_reprend_le_contenu_du_modele(
        self, cv: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        sample = cv[1]
        assert sample["identity"] == {"first_name": "Jean", "last_name": "Dupont"}
        assert sample["contact"] == {
            "email": "jean@mail.fr",
            "city": "Paris",
            "phone": "06 12 34 56 78",
        }
        assert sample["summary"] == "lorem ipsum dolor sit lorem ipsum dolor sit amet"
        first = sample["experiences"][0]
        assert first["title_raw"] == "Developpeur"
        assert first["dates_raw"] == "2020 - 2023"
        assert first["bullets"] == ["Concu une API", "Deploye en prod"]
        assert sample["skills"] == [
            {"name": "Python", "category": "TECHNIQUE"},
            {"name": "SQL", "category": "TECHNIQUE"},
        ]
        assert sample["languages"] == [{"name": "Anglais", "category": None}]
        assert sample["educations"][0]["title_raw"] == "Master"


# ---------------------------------------------------------------------------
# Le modèle à une colonne, nom centré
# ---------------------------------------------------------------------------


class TestModeleCentre:
    def test_une_seule_bande_dune_colonne(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        spec = centered[0]
        assert len(spec["bands"]) == 1
        assert len(spec["bands"][0]["columns"]) == 1
        assert spec["page_decorations"] == []

    def test_un_titre_sur_deux_lignes_garde_largeur_et_interligne(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        interests = sections(centered[0])["interests"]
        assert interests["title_sample"] == "CENTRES INTERETS"
        assert interests["title_pitch_mm"] == pytest.approx(5.0, abs=0.01)
        assert interests["title_w_mm"] is not None

    def test_un_filet_de_rubrique_etire(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        (line,) = sections(centered[0])["interests"]["decorations"]
        assert line["anchor"] == "section"
        assert line["stretch"] is True
        assert "bottom_inset_mm" in line

    def test_un_paragraphe_de_deux_lignes_est_justifie(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        assert slot(sections(centered[0])["projects"], "item_text")["align"] == "justify"

    def test_le_nom_interlettre_garde_son_interlettrage(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        assert centered[0]["styles"]["header.full_name"]["letter_spacing_pt"] > 0.3

    def test_le_poste_synthetise_est_centre_sous_un_nom_centre(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        headline = next(e for e in centered[0]["header"]["elements"] if e["field"] == "headline")
        assert headline["align"] == "center"
        assert headline["cx_mm"] == pytest.approx(75.0, abs=0.5)

    def test_un_detourage_trop_large_rend_la_boite_de_limage(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        photo = centered[0]["header"]["photo"]
        assert photo["shape"] == "rect"
        assert (photo["x_mm"], photo["w_mm"], photo["h_mm"]) == (110.0, 38.0, 20.0)

    def test_lexemple_du_modele_centre(self, centered: tuple[Json, Json, dict[str, bytes]]) -> None:
        sample = centered[1]
        assert sample["identity"] == {"first_name": "Marie", "last_name": ""}
        assert sample["projects"][0]["description"] == "aaaa bbbb cccc dddd ee"
        assert sample["summary"] is None

    def test_deux_interets_sans_puce_restent_deux_entrees(
        self, centered: tuple[Json, Json, dict[str, bytes]]
    ) -> None:
        assert [i["name"] for i in centered[1]["interests"]] == ["Lecture", "Voyage"]


# ---------------------------------------------------------------------------
# Cas limites, sur des pages fabriquées à la main
# ---------------------------------------------------------------------------


def raw_line(lid: str, text: str, x: float, y: float, w: float = 20.0, **kw: Any) -> Json:
    return {
        "id": lid,
        "text": text,
        "font": kw.pop("font", REGULAR),
        "size": kw.pop("size", 9.0),
        "color": kw.pop("color", "#000000"),
        "x": x,
        "y": y,
        "w": w,
        "h": kw.pop("h", 3.6),
        "caps": kw.pop("caps", False),
        **kw,
    }


class FakeDoc:
    """Ce que `SpecBuilder` demande au PDF : la forme vue et le raster d'une zone."""

    def __init__(self, round_: bool = True) -> None:
        self.round = round_

    def is_round(self, _box: tuple[float, ...]) -> bool:
        return self.round

    def render_png(self, **_: Any) -> bytes:
        return png(4, 4)


def builder(
    lines: list[Json],
    roles: list[tuple[str, str, int]],
    *,
    drawings: list[Json] | None = None,
    images: list[Json] | None = None,
    meta: Json | None = None,
    fonts: FontSet | None = None,
    served: dict[str, tuple[str, int, bool]] | None = None,
    doc: Any = None,
) -> SpecBuilder:
    page = PdfPage(
        width_mm=210.0,
        height_mm=297.0,
        pages=1,
        lines=lines,
        drawings=drawings or [],
        images=images or [],
    )
    labels = {
        **(meta or {}),
        "lines": [
            {"line": ln["id"], "role": r, "section": s, "item": i}
            for ln, (r, s, i) in zip(lines, roles, strict=True)
        ],
    }
    return SpecBuilder(
        doc or FakeDoc(),
        page,
        labels,
        fonts if fonts is not None else cv_font_set(),
        SERVED if served is None else served,
    )


def drawing(kind: str, x: float, y: float, w: float, h: float, **kw: Any) -> Json:
    return {
        "kind": kind,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "fill": kw.get("fill"),
        "stroke": kw.get("stroke"),
        "stroke_width_pt": kw.get("stroke_width_pt", 0.75),
    }


class TestCasLimites:
    def test_sans_titre_de_rubrique_la_construction_echoue(self) -> None:
        b = builder([raw_line("L1", "Jean", 10, 10)], [("full_name", "header", 0)])
        with pytest.raises(ValueError, match="titre de rubrique"):
            b.build()

    def test_un_modele_sans_entete_recoit_le_poste_vise_au_dessus_des_rubriques(self) -> None:
        b = builder(
            [raw_line("L1", "PROFIL", 20, 50, size=11), raw_line("L2", "Texte", 20, 57)],
            [("section_title", "summary", 0), ("summary", "summary", 0)],
        )
        spec, _, _ = b.build()
        [headline] = spec["header"]["elements"]
        assert headline["field"] == "headline"
        assert headline["synthesized"] is True
        assert headline["x_mm"] == 20
        assert headline["y_mm"] < 50

    def test_une_ligne_sans_etiquette_est_ignoree(self) -> None:
        page = PdfPage(210, 297, 1, [raw_line("L1", "A", 1, 1)], [], [])
        b = SpecBuilder(FakeDoc(), page, {"lines": []}, cv_font_set(), SERVED)
        assert b.lines == []

    def test_le_fond_de_page_et_les_doublons_dun_filet_sont_ecartes(self) -> None:
        drawings = [
            drawing("rect", 0, 0, 210, 297, fill="#F0F0F0"),
            drawing("vline", 50, 10, 0, 20, stroke="#000000"),
            drawing("rect", 49.6, 10, 0.8, 20, fill="#000000"),
            drawing("rect", 100, 10, 0.8, 20, fill="#000000"),
            drawing("rect", 120, 10, 5, 5, fill="#000000"),
        ]
        b = builder([], [], drawings=drawings)
        kept = [(d["kind"], d["x"]) for d in b.drawings]
        assert kept == [("vline", 50), ("rect", 100), ("rect", 120)]

    def test_une_police_non_servie_na_pas_dinterlettrage_mesurable(self) -> None:
        b = builder(
            [raw_line("L1", "Ab", 10, 10, font="Inconnue"), raw_line("L2", "X", 10, 20)],
            [("skill", "skills", 0), ("skill", "skills", 0)],
            fonts=FontSet([]),
        )
        assert b.served_style("Inconnue") == ("sans-serif", 400, False)
        assert b.letter_spacing(b.lines[0]) is None
        b2 = builder([raw_line("L1", "X", 10, 20)], [("skill", "skills", 0)])
        assert b2.letter_spacing(b2.lines[0]) is None

    def test_lalignement_de_plusieurs_lignes(self) -> None:
        b = builder([], [])

        def lines(rights: list[float]) -> list[Line]:
            return [
                Line(
                    raw_line(f"L{i}", "t", 0, i * 5, w=r), {"role": "x", "section": "s", "item": 0}
                )
                for i, r in enumerate(rights)
            ]

        assert b.alignment(lines([50, 50, 50, 20])) == "justify"
        assert b.alignment(lines([50, 30, 40, 20])) == "left"
        b._col_right = 60
        assert b.alignment(lines([50, 20])) == "left"

    def test_lalignement_majoritaire(self) -> None:
        assert majority_alignment([]) == "left"
        assert majority_alignment(["right", "right", "left"]) == "right"
        assert majority_alignment(["right", "center", "left"]) == "left"
        assert majority_alignment(["justify", "justify"]) == "justify"

    def test_lalignement_a_droite_ou_centre(self) -> None:
        b = builder([], [])

        def lines(boxes: list[tuple[float, float]]) -> list[Line]:
            return [
                Line(
                    raw_line(f"L{i}", "t", x, i * 5, w=w), {"role": "x", "section": "s", "item": 0}
                )
                for i, (x, w) in enumerate(boxes)
            ]

        assert b.alignment(lines([(10, 40), (20, 30), (5, 45)])) == "right"
        assert b.alignment(lines([(10, 40), (15, 30), (5, 50)])) == "center"
        assert b.alignment(lines([(10, 40), (20, 10), (5, 30)])) == "left"

    def test_une_ligne_de_dates_ne_se_prolonge_jamais(self) -> None:
        b = builder([], [])
        lines = [
            Line(raw_line("L1", "2020", 10, 10), {"role": "item_dates", "section": "x", "item": 1}),
            Line(raw_line("L2", "2021", 10, 14), {"role": "item_dates", "section": "x", "item": 1}),
        ]
        assert len(b.elements(lines, {})) == 2

    def test_un_filet_a_gauche_dune_coordonnee_nest_pas_un_pictogramme(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "CONTACT", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "0612", 24, 57),
        ]
        b = builder(
            lines,
            [("full_name", "header", 0), ("section_title", "contact", 0), ("phone", "contact", 0)],
            drawings=[drawing("hline", 18, 59, 4, 0, stroke="#000000")],
        )
        spec, _, _ = b.build()
        assert sections(spec)["contact"]["icons"] == {}

    def test_un_nom_en_deux_lignes_et_un_poste_existant(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD, caps=True),
            raw_line("L2", "DUPONT", 20, 20, h=8, size=20, font=BOLD, caps=True),
            raw_line("L3", "Data analyst", 20, 32),
            raw_line("L4", "jean@mail.fr", 140, 12),
            raw_line("L5", "PROFIL", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L6", "Un texte", 20, 57),
            raw_line("L7", "Un autre", 20, 61.5),
        ]
        b = builder(
            lines,
            [
                ("first_name", "header", 0),
                ("last_name", "header", 0),
                ("headline", "header", 0),
                ("email", "header", 0),
                ("section_title", "summary", 0),
                ("item_text", "summary", 0),
                ("item_text", "summary", 0),
            ],
            images=[
                {
                    "x": 150.0,
                    "y": 5.0,
                    "w": 40.0,
                    "h": 30.0,
                    "png": png(),
                    "clip": {"x": 150.0, "y": 5.0, "w": 20.0, "h": 30.0},
                }
            ],
            meta={"photo_shape": "circle"},
        )
        spec, sample, _ = b.build()
        assert sample["identity"] == {
            "first_name": "Jean",
            "last_name": "Dupont",
            "headline": "Data analyst",
        }
        assert sample["contact"] == {"email": "jean@mail.fr"}
        assert sample["summary"] == "Un texte Un autre"
        fields = [e["field"] for e in spec["header"]["elements"]]
        assert fields.count("headline") == 1
        assert "synthesized" not in spec["styles"]["header.headline"]
        photo = spec["header"]["photo"]
        assert (photo["x_mm"], photo["y_mm"], photo["w_mm"]) == (150.0, 10.0, 20.0)
        assert photo["sample_object_position"] == "0.0% 50.0%"
        assert "icons" in sections(spec)["summary"]

    def test_un_poste_synthetise_sans_nom_suit_le_premier_element(self) -> None:
        lines = [
            raw_line("L1", "jean@mail.fr", 20, 10),
            raw_line("L2", "PROFIL", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Texte", 20, 57),
        ]
        b = builder(
            lines,
            [("email", "header", 0), ("section_title", "summary", 0), ("summary", "summary", 0)],
        )
        spec, _, _ = b.build()
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        assert headline["x_mm"] == 20.0
        assert spec["header"]["photo"] is None

    def test_des_listes_et_rubriques_variees_remplissent_lexemple(self) -> None:
        lines = [
            raw_line("L1", "JEAN DUPONT", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "LANGUES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Anglais", 20, 57),
            raw_line("L4", "Courant", 20, 70),
            raw_line("L5", "REFERENCES", 20, 90, size=11, font=BOLD, caps=True),
            raw_line("L6", "M. Martin", 20, 97),
            raw_line("L7", "Lyon", 20, 102),
            raw_line("L8", "AUTRE", 20, 120, size=11, font=BOLD, caps=True),
            raw_line("L9", "Divers", 20, 127),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "languages", 0),
                ("language", "languages", 0),
                ("item_text", "languages", 0),
                ("section_title", "references", 0),
                ("item_title", "references", 1),
                ("item_location", "references", 1),
                ("section_title", "other", 0),
                ("item_text", "other", 0),
            ],
        )
        _, sample, _ = b.build()
        assert sample["languages"] == [
            {"name": "Anglais", "category": None},
            {"name": "Courant", "category": None},
        ]
        assert sample["references"][0]["location"] == "Lyon"
        assert "other" not in sample

    def test_rubriques_voisines_rangees_dans_une_meme_colonne(self) -> None:
        """Deux titres à moins de 15 mm d'écart : une colonne ; un même genre deux fois : un style."""
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "CONTACT", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "0612", 20, 57, w=10),
            raw_line("L4", "a@b.fr", 40, 57, w=10),
            raw_line("L5", "Note", 20, 62),
            raw_line("L6", "COMPETENCES", 30, 80, size=11, font=BOLD, caps=True),
            raw_line("L7", "Python", 30, 87),
            raw_line("L8", "Hors liste", 30, 92, size=12),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "contact", 0),
                ("phone", "contact", 0),
                ("email", "contact", 0),
                ("item_text", "contact", 0),
                ("section_title", "skills", 0),
                ("skill", "skills", 0),
                ("item_title", "skills", 0),
            ],
        )
        spec, sample, _ = b.build()
        assert len(spec["bands"][0]["columns"]) == 1
        contact = sections(spec)["contact"]
        joined = slot(contact, "phone+email")
        assert joined["separator"] is None
        assert joined["gap_mm"] == pytest.approx(10.0)
        assert sample["contact"] == {"phone": "0612", "email": "a@b.fr"}
        assert sample["skills"] == [{"name": "Python", "category": None}]

    def test_un_meme_genre_deux_fois_garde_le_premier_style(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "COMPETENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Python", 20, 57, size=9),
            raw_line("L4", "COMPETENCES", 120, 50, size=11, font=BOLD, caps=True),
            raw_line("L5", "Excel", 120, 57, size=12),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "skills", 0),
                ("skill", "skills", 0),
                ("section_title", "skills", 0),
                ("skill", "skills", 0),
            ],
        )
        spec, _, _ = b.build()
        assert spec["styles"]["skills.skill"]["size_pt"] == 9.0

    def test_les_ornements_de_rubrique_hors_colonne_ou_hors_hauteur_sont_laisses(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "EXPERIENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Poste", 25, 57),
            raw_line("L4", "Mission", 25, 62),
        ]
        drawings = [
            drawing("rect", 150, 55, 10, 10, fill="#FF0000"),  # loin de la colonne
            drawing("rect", 20, 200, 10, 10, fill="#00FF00"),  # loin sous la rubrique
            drawing("hline", 20, 61, 50, 0, stroke="#0000FF"),  # filet dans l'élément
            drawing("rect", 22, 56, 2.5, 2.5, fill="#000000"),  # pastille hors ligne
            drawing("vline", 21, 56, 0, 2, stroke="#000000"),  # trait court d'élément
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "experiences", 0),
                ("item_title", "experiences", 1),
                ("bullet", "experiences", 1),
            ],
            drawings=drawings,
            doc=FakeDoc(round_=False),
        )
        spec, _, _ = b.build()
        decos = sections(spec)["experiences"]["decorations"]
        kinds = {(d["kind"], d["anchor"]) for d in decos}
        assert ("hline", "section") in kinds
        assert ("rect", "item") in kinds
        short = next(d for d in decos if d["kind"] == "vline")
        assert "stretch" not in short
        page = {d["fill"] for d in spec["page_decorations"]}
        assert page == {"#FF0000", "#00FF00"}

    def test_la_pastille_ronde_vue_devient_une_courbe(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "EXPERIENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Poste", 25, 57),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "experiences", 0),
                ("item_title", "experiences", 1),
            ],
            drawings=[drawing("rect", 21, 57.6, 2.4, 2.4, fill="#000000")],
            doc=FakeDoc(round_=True),
        )
        spec, _, _ = b.build()
        (dot,) = sections(spec)["experiences"]["decorations"]
        assert dot["kind"] == "curve"
        assert dot["attach"] == "row"


class TestSurUnVraiPdf:
    def test_une_police_servie_absente_laisse_un_interlettrage_nul(self) -> None:
        b = PdfBuilder(100, 60, embedded=cv_fonts())
        b.text("NOM", 10, 10, size=14, font=BOLD)
        b.text("PROFIL", 10, 30, size=11, font=BOLD)
        b.text("Texte", 10, 37, size=9, font=REGULAR)
        with PdfDocument(b.to_bytes()) as doc:
            page = doc.read()
            labels = labels_for(
                page.lines,
                {
                    "NOM": ("full_name", "header", 0),
                    "PROFIL": ("section_title", "summary", 0),
                    "Texte": ("summary", "summary", 0),
                },
            )
            spec, _, _ = SpecBuilder(doc, page, labels, FontSet([]), {}).build()
        assert spec["styles"]["section_title"]["family"] == "sans-serif"
        assert spec["styles"]["section_title"]["letter_spacing_pt"] == 0.0


class TestEtiquettesEtTitres:
    def _spec(self) -> tuple[Json, Json]:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "CONTACT", 20, 50, size=11, font=BOLD, caps=True, color="#FFFFFF"),
            raw_line("L3", "Phone", 20, 57, w=10, font=BOLD),
            raw_line("L4", "0612", 20, 61, w=12),
            raw_line("L5", "Email :", 20, 67, w=10, font=BOLD),
            raw_line("L6", "a@b.fr", 32, 67, w=14),
            raw_line("L7", "COMPETENCES", 20, 80, size=11, font=BOLD, caps=True, color="#336699"),
            raw_line("L8", "Python", 20, 87),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "contact", 0),
                ("label", "contact", 0),
                ("phone", "contact", 0),
                ("label", "contact", 0),
                ("email", "contact", 0),
                ("section_title", "skills", 0),
                ("skill", "skills", 0),
            ],
        )
        spec, sample, _ = b.build()
        return spec, sample

    def test_deux_etiquettes_consecutives_ne_se_recollent_pas(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "REFERENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Phone:", 20, 57, w=10, font=BOLD),
            raw_line("L4", "0612", 32, 57, w=10),
            raw_line("L5", "Email :", 20, 61, w=10, font=BOLD),
            raw_line("L6", "a@b.fr", 32, 61, w=14),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "references", 0),
                ("label", "references", 1),
                ("phone", "references", 1),
                ("label", "references", 1),
                ("email", "references", 1),
            ],
        )
        spec, _, _ = b.build()
        refs = sections(spec)["references"]
        assert slot(refs, "phone")["label"]["text"] == "Phone:"
        assert slot(refs, "email")["label"]["text"] == "Email :"

    def test_une_etiquette_au_dessus_et_une_etiquette_en_ligne(self) -> None:
        spec, sample = self._spec()
        contact = sections(spec)["contact"]
        assert contact["slot_order"] == ["phone", "email"]
        phone = slot(contact, "phone")["label"]
        assert phone == {"text": "Phone", "inline": False, "pitch_mm": 4.0, "dx_mm": 0.0}
        email = slot(contact, "email")["label"]
        assert email["inline"] is True
        assert email["text"] == "Email :"
        assert email["gap_mm"] == pytest.approx(2.0)
        # La rangée en ligne commence à l'étiquette ; la rangée empilée aussi (marges).
        assert slot(contact, "email")["dx_mm"] == 0.0
        assert slot(contact, "email")["margin_top_mm"] == pytest.approx(67 - (61 + 3.6), abs=0.01)
        assert contact["first_gap_mm"] == pytest.approx(57 - (50 + 3.6), abs=0.5)
        assert "contact.label" in spec["styles"]
        assert sample["contact"] == {"phone": "0612", "email": "a@b.fr"}

    def test_un_titre_dune_autre_couleur_garde_son_style(self) -> None:
        spec, _ = self._spec()
        assert spec["styles"]["section_title"]["color"] == "#FFFFFF"
        assert sections(spec)["contact"]["title_style"] == "section_title"
        assert sections(spec)["skills"]["title_style"] == "skills.section_title"
        assert spec["styles"]["skills.section_title"]["color"] == "#336699"

    def test_deux_elements_cote_a_cote_ne_donnent_pas_un_ecart_negatif(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "REFERENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "M. Martin", 20, 57),
            raw_line("L4", "Mme Durand", 80, 57),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "references", 0),
                ("item_title", "references", 1),
                ("item_title", "references", 2),
            ],
        )
        spec, _, _ = b.build()
        assert sections(spec)["references"]["item_gap_mm"] == 0.0

    def test_le_rendu_html_et_docx_des_etiquettes(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import (  # noqa: PLC0415
            HtmlRenderer,
            estimate,
        )

        spec, sample = self._spec()
        view = from_sample(spec, sample)
        html = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert html.count("Phone") == 1
        assert "Email :" in html
        assert '<span style="display:inline-block;width:2.00mm"></span>' in html
        assert "s-contact-label" in html
        # Le titre de la seconde rubrique porte son propre style.
        assert ".s-skills-section-title{" in html
        docx = DocxRenderer(spec, view, cv_font_set(), photo=None).render({})
        assert docx.startswith(b"PK")
        contact = sections(spec)["contact"]
        sec = next(x for x in view["sections"] if x["kind"] == "contact")
        assert estimate(spec["styles"], contact, sec, 60.0, cv_font_set()) > 10


class TestGrillesEtFerADroite:
    def _grid(self) -> tuple[Json, Json]:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "INTERETS", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Photo", 20, 57, w=14),
            raw_line("L4", "Lecture", 50, 57, w=14),
            raw_line("L5", "Cuisine", 20, 64, w=14),
            raw_line("L6", "Art", 50, 64, w=8),
            raw_line("L7", "Mode", 20, 71, w=10),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "interests", 0),
                ("interest", "interests", 0),
                ("interest", "interests", 0),
                ("interest", "interests", 0),
                ("interest", "interests", 0),
                ("interest", "interests", 0),
            ],
        )
        spec, sample, _ = b.build()
        return spec, sample

    def test_une_liste_en_grille_a_ses_colonnes_et_son_pas(self) -> None:
        spec, sample = self._grid()
        interests = sections(spec)["interests"]
        assert interests["slot_order"] == ["interest"]
        sl = slot(interests, "interest")
        assert sl["columns"] == 2
        assert sl["column_pitch_mm"] == pytest.approx(30.0)
        assert sl["dx_mm"] == 0.0
        assert sl["repeat_margin_mm"] == pytest.approx(7.0 - 3.6, abs=0.05)
        assert [i["name"] for i in sample["interests"]] == [
            "Photo",
            "Lecture",
            "Cuisine",
            "Art",
            "Mode",
        ]

    def test_le_rendu_dune_grille_range_les_entrees_cote_a_cote(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer  # noqa: PLC0415

        spec, sample = self._grid()
        view = from_sample(spec, sample)
        html = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert html.count("display:flex;flex-wrap:wrap") == 1
        assert html.count("width:30.00mm") == 5
        assert "Mode" in html
        assert DocxRenderer(spec, view, cv_font_set(), photo=None).render({}).startswith(b"PK")

    def test_des_dates_au_fer_a_droite_dune_ligne_composee(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer  # noqa: PLC0415

        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "EXPERIENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Chargée de communication", 20, 57, w=40, font=BOLD),
            raw_line("L4", "2024 - 2025", 80, 57, w=20),
            raw_line("L5", "Stage", 20, 70, w=12, font=BOLD),
            raw_line("L6", "2021", 90, 70, w=10),
        ]
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "experiences", 0),
                ("item_title", "experiences", 1),
                ("item_dates", "experiences", 1),
                ("item_title", "experiences", 2),
                ("item_dates", "experiences", 2),
            ],
        )
        spec, sample, _ = b.build()
        sl = slot(sections(spec)["experiences"], "item_title+item_dates")
        assert sl["flush_right"] is True
        view = from_sample(spec, sample)
        html = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert html.count('<span style="flex:1 1 auto;min-width:3mm"></span>') == 2
        assert DocxRenderer(spec, view, cv_font_set(), photo=None).render({}).startswith(b"PK")
        # Des droites qui diffèrent : l'écart mesuré, pas le fer à droite.
        lines[5] = raw_line("L6", "2021", 64, 70, w=10)
        b = builder(
            lines,
            [
                ("full_name", "header", 0),
                ("section_title", "experiences", 0),
                ("item_title", "experiences", 1),
                ("item_dates", "experiences", 1),
                ("item_title", "experiences", 2),
                ("item_dates", "experiences", 2),
            ],
        )
        spec, _, _ = b.build()
        assert slot(sections(spec)["experiences"], "item_title+item_dates")["flush_right"] is False


class TestGrilleDElementsEtParagraphes:
    """Références côte à côte, coordonnées d'un élément, espacement des mots, fer à droite."""

    def _references(self) -> tuple[Json, Json]:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "REFERENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Harumi", 20, 57, w=20, font=BOLD),
            raw_line("L4", "Bailey", 80, 57, w=20, font=BOLD),
            raw_line("L5", "Wardiere", 20, 61, w=20),
            raw_line("L6", "Wardiere", 80, 61, w=20),
            raw_line("L7", "Phone:", 20, 66, w=10, font=BOLD),
            raw_line("L8", "0612", 32, 66, w=10),
            raw_line("L9", "Phone:", 80, 66, w=10, font=BOLD),
            raw_line("L10", "0613", 92, 66, w=10),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "references", 0),
            ("item_title", "references", 1),
            ("item_title", "references", 2),
            ("item_subtitle", "references", 1),
            ("item_subtitle", "references", 2),
            ("label", "references", 1),
            ("phone", "references", 1),
            ("label", "references", 2),
            ("phone", "references", 2),
        ]
        spec, sample, _ = builder(lines, roles).build()
        return spec, sample

    def test_deux_references_cote_a_cote_font_une_grille_au_pas_mesure(self) -> None:
        spec, _ = self._references()
        refs = sections(spec)["references"]
        assert refs["item_columns"] == 2
        assert refs["item_pitch_mm"] == pytest.approx(60.0)
        # Deux voisins de rangée n'ont pas d'écart ; chaque emplacement se mesure
        # depuis la colonne de son élément (l'étiquette de la seconde référence
        # ne porte plus l'abscisse de la seconde colonne).
        assert refs["item_gap_mm"] == 0.0
        assert slot(refs, "item_title")["dx_mm"] == 0.0
        assert slot(refs, "phone")["label"]["dx_mm"] == 0.0
        assert slot(refs, "phone")["label"]["gap_mm"] == pytest.approx(2.0)
        assert slot(refs, "phone")["dx_mm"] == 0.0

    def test_les_coordonnees_dun_element_restent_dans_lexemple(self) -> None:
        _, sample = self._references()
        assert [e["extra"] for e in sample["references"]] == [{"phone": "0612"}, {"phone": "0613"}]

    def test_des_elements_empiles_ne_font_pas_de_grille(self, spec: Json) -> None:
        assert "item_columns" not in sections(spec)["experiences"]
        assert SpecBuilder.item_grid({}) == {}

    def test_un_paragraphe_au_fer_a_droite_reste_un_seul_element(self) -> None:
        b = builder([], [])
        lines = [
            Line(
                raw_line("L1", "aaa bbb", 10, 10, w=40),
                {"role": "summary", "section": "s", "item": 0},
            ),
            Line(
                raw_line("L2", "ccc", 30, 14, w=20), {"role": "summary", "section": "s", "item": 0}
            ),
            Line(
                raw_line("L3", "dd ee ff", 15, 18, w=35),
                {"role": "summary", "section": "s", "item": 0},
            ),
        ]
        (element,) = b.elements(lines, {})
        assert [ln.id for ln in element["lines"]] == ["L1", "L2", "L3"]
        centered = [
            Line(
                raw_line("L1", "aaa", 20, 10, w=40), {"role": "summary", "section": "s", "item": 0}
            ),
            Line(
                raw_line("L2", "bb", 30, 14, w=20), {"role": "summary", "section": "s", "item": 0}
            ),
        ]
        assert len(b.elements(centered, {})) == 1
        apart = [
            Line(
                raw_line("L1", "aaa", 20, 10, w=40), {"role": "summary", "section": "s", "item": 0}
            ),
            Line(
                raw_line("L2", "bb", 25, 14, w=20), {"role": "summary", "section": "s", "item": 0}
            ),
        ]
        assert len(b.elements(apart, {})) == 2

    def test_lespacement_des_mots_se_lit_sur_la_derniere_ligne_dun_paragraphe_justifie(
        self,
    ) -> None:
        fonts = cv_font_set()
        st = {
            "family": "Testa",
            "weight": 400,
            "italic": False,
            "size_pt": 9.0,
            "letter_spacing_pt": 0.0,
            "uppercase": False,
        }
        last = "un deux trois quatre cinq"
        natural = fonts.width_mm(last, st)
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "PROFIL", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "aaaa bbbb cccc dddd", 20, 57, w=80),
            raw_line("L4", "eeee ffff gggg hhhh", 20, 61, w=80),
            raw_line("L5", last, 20, 65, w=natural + 4 * 0.5),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "summary", 0),
            ("summary", "summary", 0),
            ("summary", "summary", 0),
            ("summary", "summary", 0),
        ]
        spec, _, _ = builder(lines, roles).build()
        assert spec["styles"]["summary.summary"]["word_spacing_mm"] == pytest.approx(0.5, abs=0.02)
        # Il entre dans la largeur rendue d'un texte de ce style.
        assert fonts.width_mm(last, spec["styles"]["summary.summary"]) == pytest.approx(
            natural + 2.0, abs=0.1
        )

    def test_sans_paragraphe_justifie_ou_avec_un_blanc_aberrant_pas_despacement(self) -> None:
        fonts = cv_font_set()
        st = {
            "family": "Testa",
            "weight": 400,
            "italic": False,
            "size_pt": 9.0,
            "letter_spacing_pt": 0.0,
            "uppercase": False,
        }
        b = builder([], [])
        natural = fonts.width_mm("un deux trois quatre", st)
        justified = [
            Line(
                raw_line("L1", "aaaa bbbb", 20, 57, w=80),
                {"role": "summary", "section": "s", "item": 0},
            ),
            Line(
                raw_line("L2", "cccc dddd", 20, 61, w=80),
                {"role": "summary", "section": "s", "item": 0},
            ),
            Line(
                raw_line("L3", "un deux trois quatre", 20, 65, w=natural + 3 * 5.0),
                {"role": "summary", "section": "s", "item": 0},
            ),
        ]
        b._col_right = 100.0
        assert b.word_spacing(b.elements(justified, {}), st) is None  # 5 mm par espace : aberrant
        short_last = [
            *justified[:2],
            Line(
                raw_line("L3", "un deux", 20, 65, w=natural),
                {"role": "summary", "section": "s", "item": 0},
            ),
        ]
        assert b.word_spacing(b.elements(short_last, {}), st) is None  # moins de trois espaces
        assert b.word_spacing(b.elements(justified[:1], {}), st) is None  # une ligne


class TestBarresDeNiveauEtPolicesSubstituees:
    def test_une_barre_de_niveau_dune_liste_est_propre_a_lexemple(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "LANGUES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Anglais", 20, 57, w=14),
            raw_line("L4", "Allemand", 20, 62, w=16),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "languages", 0),
            ("language", "languages", 0),
            ("language", "languages", 0),
        ]
        bars = [
            drawing("rect", 40, 58, 13, 1.9, fill="#EEEEEE"),
            drawing("rect", 40, 58, 8, 1.9, fill="#333333"),
            drawing("vline", 18, 50, 0, 20, stroke="#333333"),
            drawing(
                "rect", 14, 63.5, 1.0, 1.0, fill="#333333"
            ),  # une puce orpheline : pas une barre
        ]
        spec, _, _ = builder(lines, roles, drawings=bars, doc=FakeDoc(round_=False)).build()
        decorations = sections(spec)["languages"]["decorations"]
        flags = {(d["kind"], d["w_mm"], bool(d.get("sample_only"))) for d in decorations}
        assert ("rect", 13.0, True) in flags
        assert ("rect", 8.0, True) in flags
        assert not any(d.get("sample_only") for d in decorations if d["kind"] == "vline")
        assert not any(d.get("sample_only") for d in decorations if d["w_mm"] < 5)

    def test_lespacement_des_mots_ne_se_mesure_pas_sur_une_police_substituee(self) -> None:
        fonts = cv_font_set()
        st = {
            "family": "Testa",
            "weight": 400,
            "italic": False,
            "size_pt": 9.0,
            "letter_spacing_pt": 0.0,
            "uppercase": False,
        }
        last = "un deux trois quatre cinq"
        natural = fonts.width_mm(last, st)
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "PROFIL", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "aaaa bbbb cccc dddd", 20, 57, w=80),
            raw_line("L4", "eeee ffff gggg hhhh", 20, 61, w=80),
            raw_line("L5", last, 20, 65, w=natural + 4 * 0.5),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "summary", 0),
            ("summary", "summary", 0),
            ("summary", "summary", 0),
            ("summary", "summary", 0),
        ]
        b = builder(lines, roles)
        b.substituted = frozenset({"testa"})
        spec, _, _ = b.build()
        assert "word_spacing_mm" not in spec["styles"]["summary.summary"]


class TestTracesRasterises:
    def _spec(
        self, extra_lines: list[Json], images: list[Json], *, round_: bool = False
    ) -> tuple[Json, dict[str, bytes]]:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "COMPETENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Python", 20, 57),
            *extra_lines,
        ]
        roles = [("full_name", "header", 0), ("section_title", "skills", 0), ("skill", "skills", 0)]
        roles += [("item_text", "skills", 0)] * len(extra_lines)
        frame = drawing("curve", 150, 240, 30, 30, fill="#D8A4B5")
        small = drawing("curve", 100, 240, 3, 3, fill="#D8A4B5")
        b = builder(
            lines, roles, drawings=[frame, small], images=images, doc=FakeDoc(round_=round_)
        )
        spec, _, assets = b.build()
        return spec, assets

    def test_un_grand_trace_non_rond_sans_texte_dessous_devient_une_image(self) -> None:
        spec, assets = self._spec([], [])
        (frame,) = [d for d in spec["page_decorations"] if d["w_mm"] == 30]
        assert frame["asset"] == "shape_0.png"
        assert "shape_0.png" in assets
        assert "sample_only" not in frame
        (small,) = [d for d in spec["page_decorations"] if d["w_mm"] == 3]
        assert "asset" not in small

    def test_un_trace_rond_ou_sous_du_texte_reste_une_forme(self) -> None:
        spec, assets = self._spec([], [], round_=True)
        assert not any(d.get("asset") for d in spec["page_decorations"])
        under = raw_line("L4", "Mon portfolio", 140, 250, w=30)
        spec, assets = self._spec([under], [])
        assert not any(d.get("asset") for d in spec["page_decorations"])
        assert not any(name.startswith("shape_") for name in assets)

    def test_un_trace_autour_dune_image_ne_vaut_que_pour_lexemple(self) -> None:
        image = {"x": 155, "y": 245, "w": 20, "h": 20, "png": png(10, 10), "clip": None}
        spec, _ = self._spec([], [image])
        (frame,) = [d for d in spec["page_decorations"] if d["w_mm"] == 30]
        assert frame["sample_only"] is True

    def test_une_rubrique_sans_titre_ni_element_a_un_haut(self) -> None:
        b = builder([], [])
        s = {"title": [], "top": 42.0, "bottom": 50.0}
        assert b.anchored_decorations(s, {}, 20.0) == []


class TestCorpsPropreDunElement:
    def test_un_element_ecrit_plus_petit_garde_son_corps_dans_lexemple(self) -> None:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "EXPERIENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Poste", 20, 57, size=9),
            raw_line("L4", "Mission une", 25, 62, size=9),
            raw_line("L5", "Poste deux", 20, 80, size=9),
            raw_line("L6", "Mission petite", 25, 85, size=6),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "experiences", 0),
            ("item_title", "experiences", 1),
            ("bullet", "experiences", 1),
            ("item_title", "experiences", 2),
            ("bullet", "experiences", 2),
        ]
        _, sample, _ = builder(lines, roles).build()
        first, second = sample["experiences"]
        assert "sizes" not in first
        assert second["sizes"] == {"bullet": 6.0}
        # Sans style connu pour le rôle, rien à comparer.
        assert SpecBuilder.item_sizes("experiences", [{"role": "x", "lines": []}], {}) == {}


# ---------------------------------------------------------------------------
# Listes en cadres, rubrique centrée, étiquettes en pictogrammes
# ---------------------------------------------------------------------------

CV_PERSONNE: Json = {
    "identity": {"first_name": "Awa", "last_name": "Diallo", "headline": "Vendeuse"},
    "skills": [
        {"name": name}
        for name in ("Vente", "Caisse", "Visualisation de données", "Stock", "Accueil")
    ],
    "languages": [{"language": "Anglais", "level": "B2"}],
}


class TestListeEnCadres:
    """Deux rangées de cercles, une compétence au centre de chacun."""

    FRAMES = ((20.0, 60.0), (44.0, 60.0), (20.0, 84.0), (44.0, 84.0))

    def _builder(self, frames: tuple[tuple[float, float], ...] = FRAMES) -> SpecBuilder:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "COMPETENCES", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "HTML", 25, 68.2, w=10),
            raw_line("L4", "CSS", 50, 68.2, w=8),
            raw_line("L5", "Python", 24, 92.2, w=12),
            raw_line("L6", "Docker", 48, 92.2, w=12),
        ]
        roles = [("full_name", "header", 0), ("section_title", "skills", 0)]
        roles += [("skill", "skills", 0)] * 4
        return builder(
            lines,
            roles,
            drawings=[drawing("curve", x, y, 20, 20, stroke="#333333") for x, y in frames],
        )

    def test_chaque_entree_porte_son_cadre_au_pas_mesure(self) -> None:
        spec, sample, _ = self._builder().build()
        skills = sections(spec)["skills"]
        sl = slot(skills, "skill")
        assert sl["cell"] == {
            "kind": "curve",
            "w_mm": 20.0,
            "h_mm": 20.0,
            "fill": None,
            "stroke": "#333333",
            "stroke_pt": 0.75,
            "row_gap_mm": 4.0,
        }
        assert (sl["columns"], sl["column_pitch_mm"], sl["align"]) == (2, 24.0, "center")
        assert sl["dx_mm"] == 0.0
        # L'écart sous le titre se compte jusqu'au cadre, plus jusqu'au libellé.
        assert skills["first_gap_mm"] == pytest.approx(60 - (50 + 3.6), abs=0.5)
        # Les cadres ne sont plus des ornements de la rubrique ni de la page.
        assert skills["decorations"] == []
        assert spec["page_decorations"] == []
        assert [s["name"] for s in sample["skills"]] == ["HTML", "CSS", "Python", "Docker"]

    def test_une_entree_sans_cadre_laisse_les_formes_a_la_rubrique(self) -> None:
        spec, _, _ = self._builder(self.FRAMES[:3]).build()
        skills = sections(spec)["skills"]
        assert "cell" not in slot(skills, "skill")
        assert len(skills["decorations"]) + len(spec["page_decorations"]) == 3

    def test_des_cadres_de_tailles_differentes_ne_font_pas_une_grille(self) -> None:
        b = self._builder()
        b.drawings[3]["w"] = 26.0
        spec, _, _ = b.build()
        assert "cell" not in slot(sections(spec)["skills"], "skill")

    def test_lexemple_se_rend_avec_un_cadre_par_entree(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import (  # noqa: PLC0415
            HtmlRenderer,
            estimate,
        )

        spec, sample, _ = self._builder().build()
        view = from_sample(spec, sample)
        html = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert html.count("border-radius:50%;border:0.75pt solid #333333") == 4
        # Deux cadres par rangée ; la seconde rangée est à l'écart mesuré.
        assert "width:48.00mm" in html
        assert html.count("margin-top:calc(4.00mm*var(--k))") == 2
        assert DocxRenderer(spec, view, cv_font_set(), photo=None).render({}).startswith(b"PK")
        skills = sections(spec)["skills"]
        sec = next(x for x in view["sections"] if x["kind"] == "skills")
        # Titre, écart, deux rangées de 20 mm et leur écart de 4 mm.
        assert estimate(spec["styles"], skills, sec, 80.0, cv_font_set()) == pytest.approx(
            skills["margin_top_mm"] + 3.6 + skills["first_gap_mm"] + 44.0, abs=0.1
        )

    def test_une_personne_a_autant_de_cadres_que_de_competences(self) -> None:
        from karria.modules.documents.domain.view import from_cv  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer  # noqa: PLC0415

        spec, _, _ = self._builder().build()
        html = HtmlRenderer(spec, from_cv(spec, CV_PERSONNE), cv_font_set(), photo=None).render()
        # Cinq compétences, cinq cadres ; les langues, empruntées, n'en ont pas.
        assert html.count("border-radius:50%;border:0.75pt solid #333333") == 5
        assert "Anglais (B2)" in html
        # « Visualisation » ne tient pas dans 20 mm en 9 pt : tous les libellés
        # prennent le même corps réduit.
        assert html.count("font-size:calc(") - html.count("font-size:calc(9.0pt") >= 5

    def test_un_libelle_trop_long_reduit_son_corps(self) -> None:
        from karria.modules.documents.infrastructure.html_renderer import (  # noqa: PLC0415
            cell_inner_width,
            cell_text_scale,
            cell_text_width,
        )

        spec, _, _ = self._builder().build()
        sl = slot(sections(spec)["skills"], "skill")
        st = spec["styles"]["skills.skill"]
        fonts = cv_font_set()
        assert cell_text_scale(fonts, "HTML", st, sl["cell"]) == 1.0
        assert cell_text_scale(fonts, "Visualisation de données", st, sl["cell"]) < 1.0
        # Un mot que rien ne fait tenir prend le plus petit corps essayé.
        assert cell_text_scale(fonts, "Anticonstitutionnellement" * 3, st, sl["cell"]) == 0.6
        assert cell_text_width(fonts, "Anticonstitutionnellement" * 3, st, sl["cell"], 0.6) is None
        # Dans un rond, la corde se resserre avec le nombre de lignes ; un cadre
        # droit garde sa largeur, moins un millimètre de chaque côté.
        one, three = (cell_inner_width(sl["cell"], n, 3.6) for n in (1, 3))
        assert 14.0 < three < one < 18.0
        square = {**sl["cell"], "kind": "rect"}
        assert cell_inner_width(square, 3, 3.6) == 18.0
        assert cell_text_width(fonts, "HTML", st, square, 1.0) == 18.0


class TestRubriqueCentree:
    """Des coordonnées centrées les unes sous les autres, sous une photo."""

    def _lines(self, website: tuple[float, float] = (20.0, 60.0)) -> list[Json]:
        return [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "CONTACT", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "jean@mail.fr", 28, 60, w=34),
            raw_line("L4", "0612", 39, 67, w=12),
            raw_line("L5", "1 rue Haute", 34, 74, w=22),
            raw_line("L6", "Paris", 40, 78, w=10),
            raw_line("L7", "https://exemple.fr/jean", website[0], 85, w=website[1]),
        ]

    ROLES = (
        ("full_name", "header", 0),
        ("section_title", "contact", 0),
        ("email", "contact", 0),
        ("phone", "contact", 0),
        ("address", "contact", 0),
        ("address", "contact", 0),
        ("website", "contact", 0),
    )

    def test_les_lignes_centrees_se_rendent_centrees_dans_la_boite_de_laxe(self) -> None:
        spec, sample, _ = builder(self._lines(), list(self.ROLES)).build()
        contact = sections(spec)["contact"]
        assert contact["centered"] is True
        # L'axe est à 45 mm, la colonne commence à 20 mm : une boîte de 50 mm.
        assert contact["width_mm"] == pytest.approx(50.0)
        assert {(sl["align"], sl["dx_mm"]) for sl in contact["slots"]} == {("center", 0.0)}
        # Une adresse écrite « rue » puis « ville » garde son retour à la ligne.
        assert sample["contact"]["city"] == "1 rue Haute\nParis"

    def test_une_ligne_hors_de_laxe_qui_ne_deborde_pas_interdit_le_centrage(self) -> None:
        spec, _, _ = builder(self._lines(website=(20.0, 40.0)), list(self.ROLES)).build()
        assert "centered" not in sections(spec)["contact"]

    def test_des_lignes_alignees_a_gauche_ne_sont_pas_centrees(self, spec: Json) -> None:
        assert all("centered" not in s for s in sections(spec).values())

    def test_dans_lexemple_un_mot_trop_large_deborde_sans_se_couper(self) -> None:
        from karria.modules.documents.domain.view import from_cv, from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer  # noqa: PLC0415

        spec, sample, _ = builder(self._lines(), list(self.ROLES)).build()
        # L'adresse du site, plus large que la boîte une fois rendue.
        sample["contact"]["website"] = "https://exemple.fr/jean-dupont-developpeur"
        html = HtmlRenderer(spec, from_sample(spec, sample), cv_font_set(), photo=None).render()
        assert 'white-space:nowrap">https://exemple.fr/jean-dupont-developpeur' in html
        assert "text-align:center" in html
        # Une personne : son adresse se réduit avant de se couper, elle ne déborde pas.
        cv = {
            "identity": {"first_name": "Awa", "last_name": "Diallo"},
            "contact": {
                "email": "awa@example.com",
                "links": [{"url": "https://exemple.fr/jean-dupont-developpeur"}],
            },
        }
        person = HtmlRenderer(spec, from_cv(spec, cv), cv_font_set(), photo=None).render()
        assert 'white-space:nowrap">exemple.fr' not in person


class TestEtiquetteEnPictogrammes:
    def _spec(self) -> tuple[Json, Json, dict[str, bytes]]:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "CONTACT", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "a@b.fr", 20, 57, w=14),
            raw_line("L4", "  ", 22, 63, w=10, h=2.0),
            raw_line("L5", "0612", 20, 68, w=12),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "contact", 0),
            ("email", "contact", 0),
            ("label", "contact", 0),
            ("phone", "contact", 0),
        ]
        return builder(lines, roles).build()

    def test_un_texte_de_glyphes_prives_est_un_pictogramme(self) -> None:
        assert is_pictogram_text(" ")
        assert not is_pictogram_text("Phone")
        assert not is_pictogram_text(" 0612")
        assert not is_pictogram_text("  ")

    def test_letiquette_devient_une_image_de_la_source(self) -> None:
        spec, _, assets = self._spec()
        label = slot(sections(spec)["contact"], "phone")["label"]
        assert label["text"] == ""
        assert label["asset"] == "label_contact_phone.png"
        assert label["asset"] in assets
        # La boîte de la ligne (10 × 2 mm) et 35 % de sa hauteur de chaque côté.
        assert (label["w_mm"], label["h_mm"], label["pad_mm"]) == (11.4, 3.4, 0.7)
        assert label["pitch_mm"] == pytest.approx(5.0)

    def test_le_rendu_pose_limage_et_necrit_aucun_glyphe_prive(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer  # noqa: PLC0415

        spec, sample, _ = self._spec()
        view = from_sample(spec, sample)
        html = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert '<img src="label_contact_phone.png"' in html
        assert "left:-0.70mm" in html
        assert "" not in html
        assert DocxRenderer(spec, view, cv_font_set(), photo=None).render({}).startswith(b"PK")


class TestPucesTapeesEtFiletsDElement:
    """« · Mission » : une puce tapée, sans forme ; un filet sous l'intitulé de chaque élément."""

    def _builder(self, rules: tuple[float, ...] = (61.5, 81.5)) -> SpecBuilder:
        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "EXPERIENCE", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Vendeur", 22, 57, font=BOLD),
            raw_line("L4", "· Conseiller", 20, 64, w=22),
            raw_line("L5", "· Encaisser", 20, 69, w=20),
            raw_line("L6", "Caissier", 22, 77, font=BOLD),
            raw_line("L7", "· Rendre la monnaie", 20, 84, w=30),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "experiences", 0),
            ("item_title", "experiences", 1),
            ("bullet", "experiences", 1),
            ("bullet", "experiences", 1),
            ("item_title", "experiences", 2),
            ("bullet", "experiences", 2),
        ]
        return builder(
            lines,
            roles,
            drawings=[drawing("hline", 20, y, 170, 0, stroke="#E1E1E1") for y in rules],
        )

    def test_deux_puces_tapees_font_deux_missions_sans_leur_glyphe(self) -> None:
        spec, sample, _ = self._builder().build()
        exp = sections(spec)["experiences"]
        assert sample["experiences"][0]["bullets"] == ["Conseiller", "Encaisser"]
        marker = exp["marker"]
        assert (marker["shape"], marker["text"], marker["roles"]) == ("text", "·", ["bullet"])
        # Le texte commence après la puce ; la puce est à gauche de lui, à l'écart mesuré.
        bullet = slot(exp, "bullet")
        assert bullet["dx_mm"] > 0
        assert marker["dx_mm"] == pytest.approx(-bullet["dx_mm"], abs=0.01)
        assert bullet["repeat_margin_mm"] == pytest.approx(5.0 - 3.6, abs=0.05)

    def test_la_place_du_texte_se_lit_sur_ses_glyphes(self) -> None:
        b = self._builder()
        b.lines[3].raw["chars"] = [("·", 20.0, 20.6), ("C", 22.4, 24.0)]
        typed = b.typed_markers(b.lines)
        assert set(typed) == {"L4", "L5", "L7"}
        assert (b.lines[3].text, b.lines[3].x, b.lines[3].w) == ("Conseiller", 22.4, 19.6)

    def test_un_tiret_dans_un_texte_ou_un_autre_role_nest_pas_une_puce(self) -> None:
        b = self._builder()
        b.lines[2].text = "- Vendeur"
        b.lines[3].text = "Conseiller - vendre"
        assert set(b.typed_markers(b.lines)) == {"L5", "L7"}

    def test_le_filet_repete_sous_chaque_intitule_est_un_decor_delement(self) -> None:
        spec, _, _ = self._builder().build()
        decor = sections(spec)["experiences"]["decorations"]
        assert [(d["kind"], d["anchor"], d["dy_mm"]) for d in decor] == [("hline", "item", 4.5)]

    def test_un_filet_qui_manque_a_un_element_reste_a_la_rubrique(self) -> None:
        spec, _, _ = self._builder(rules=(61.5,)).build()
        decor = sections(spec)["experiences"]["decorations"]
        assert [d["anchor"] for d in decor] == ["section"]

    def test_des_filets_a_des_ecarts_differents_restent_a_la_rubrique(self) -> None:
        spec, _, _ = self._builder(rules=(61.5, 86.0)).build()
        decor = sections(spec)["experiences"]["decorations"]
        assert {d["anchor"] for d in decor} == {"section"}

    def test_le_rendu_repose_la_puce_devant_chaque_mission(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer  # noqa: PLC0415

        spec, sample, _ = self._builder().build()
        view = from_sample(spec, sample)
        html = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert html.count(">·</span>") == 3
        # Un filet par élément, dans l'élément.
        assert html.count("border-top:0.75pt solid #E1E1E1") == 2
        assert DocxRenderer(spec, view, cv_font_set(), photo=None).render({}).startswith(b"PK")


# ---------------------------------------------------------------------------
# Colonne qui commence par la photo, missions repliées, DOCX d'une liste en cadres
# ---------------------------------------------------------------------------


class TestTeteDeColonneSansPhoto:
    """Le portrait, puis « Contact » sous lui : sans photo, la rubrique remonte."""

    @staticmethod
    def _spec(photo: Json | None) -> Json:
        return {
            "header": {"photo": photo},
            "bands": [{"y_sample": 70.0}, {"y_sample": 200.0}],
        }

    COLUMN: Json = {  # noqa: RUF012 - une donnée de test, jamais modifiée
        "x_mm": 16.0,
        "w_mm": 66.0,
        "sections": [{"kind": "contact", "margin_top_mm": 71.5}],
    }
    PHOTO: Json = {"x_mm": 16.5, "y_mm": 70.0, "w_mm": 55.0, "h_mm": 66.0}  # noqa: RUF012

    def test_sans_photo_la_rubrique_prend_le_haut_du_portrait(self) -> None:
        from karria.modules.documents.infrastructure.html_renderer import head_margin  # noqa: PLC0415

        spec = self._spec(self.PHOTO)
        assert head_margin(spec, 0, self.COLUMN, photo=True) == 71.5
        assert head_margin(spec, 0, self.COLUMN, photo=False) == 0.0
        lower = self._spec({**self.PHOTO, "y_mm": 74.0})
        assert head_margin(lower, 0, self.COLUMN, photo=False) == 4.0

    def test_la_marge_reste_hors_de_ce_cas(self) -> None:
        from karria.modules.documents.infrastructure.html_renderer import head_margin  # noqa: PLC0415

        # Pas de photo dans le modèle, une autre bande, une colonne vide.
        assert head_margin(self._spec(None), 0, self.COLUMN, photo=False) == 71.5
        assert head_margin(self._spec(self.PHOTO), 1, self.COLUMN, photo=False) == 71.5
        assert (
            head_margin(self._spec(self.PHOTO), 0, {**self.COLUMN, "sections": []}, photo=False)
            == 0.0
        )
        # Une photo qui commence dans l'en-tête, au-dessus de la bande : la marge
        # de la rubrique ne lui doit pas tout, elle reste.
        above = self._spec({**self.PHOTO, "y_mm": 12.0, "h_mm": 83.0})
        assert head_margin(above, 0, self.COLUMN, photo=False) == 71.5
        # Une photo dans une autre colonne, ou qui descend plus bas que la rubrique.
        beside = self._spec({**self.PHOTO, "x_mm": 120.0})
        assert head_margin(beside, 0, self.COLUMN, photo=False) == 71.5
        tall = self._spec({**self.PHOTO, "h_mm": 90.0})
        assert head_margin(tall, 0, self.COLUMN, photo=False) == 71.5

    def test_la_premiere_rubrique_rendue_prend_la_tete_de_colonne_ou_sa_marge(self) -> None:
        from karria.modules.documents.infrastructure.html_renderer import column_head  # noqa: PLC0415

        spec = self._spec(self.PHOTO)
        contact = self.COLUMN["sections"][0]
        skills = {"kind": "skills", "margin_top_mm": 13.0}
        assert column_head(spec, 0, self.COLUMN, (contact, {}, False), photo=False) == 0.0
        assert column_head(spec, 0, self.COLUMN, (contact, {}, False), photo=True) == 71.5
        # Une rubrique empruntée venue en tête prend la tête de la colonne ; une
        # autre rubrique du modèle garde sa propre marge.
        assert column_head(spec, 0, self.COLUMN, (skills, {}, True), photo=True) == 71.5
        assert column_head(spec, 0, self.COLUMN, (skills, {}, False), photo=True) == 13.0


class TestMissionsReplieesEtPliure:
    def test_plusieurs_valeurs_de_texte_gardent_un_blanc_entre_elles(self) -> None:
        from karria.modules.documents.infrastructure.html_renderer import repeat_margin  # noqa: PLC0415

        st = {"pitch_mm": 5.0, "box_h_mm": 4.0}
        text = {"repeat_margin_mm": None, "margin_top_mm": 0.06}
        assert repeat_margin(text, "item_text", st) == 2.0
        # Une puce, un emplacement dont la répétition est mesurée : l'écart du modèle.
        assert repeat_margin(text, "bullet", st) == 0.06
        assert repeat_margin({**text, "repeat_margin_mm": 1.2}, "item_text", st) == 1.2
        assert repeat_margin({**text, "margin_top_mm": 3.0}, "item_text", st) == 3.0

    def test_le_rendu_espace_les_missions_dune_personne_pas_lexemple(self) -> None:
        from karria.modules.documents.domain.view import from_cv, from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415
        from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer  # noqa: PLC0415

        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "EXPERIENCE", 20, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Vendeur", 20, 57, font=BOLD),
            raw_line("L4", "Conseil en magasin.", 20, 61, w=30),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "experiences", 0),
            ("item_title", "experiences", 1),
            ("item_text", "experiences", 1),
        ]
        spec, sample, _ = builder(lines, roles).build()
        gap = "margin-top:calc(1.44mm*var(--k))"
        example = HtmlRenderer(spec, from_sample(spec, sample), cv_font_set(), photo=None).render()
        assert gap not in example
        cv = {
            "identity": {"first_name": "Awa", "last_name": "Diallo"},
            "experiences": [
                {
                    "position": "Vendeuse",
                    "company": "Zara",
                    "missions": ["Conseiller", "Encaisser", "Ranger"],
                }
            ],
        }
        view = from_cv(spec, cv)
        person = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        # Trois missions : deux blancs de 40 % de l'interligne (3,6 mm), aucun avant la première.
        assert person.count(gap) == 2
        assert DocxRenderer(spec, view, cv_font_set(), photo=None).render({}).startswith(b"PK")

    def test_le_titre_dune_rubrique_suit_son_premier_element_a_la_pliure(self) -> None:
        from karria.modules.documents.infrastructure.html_renderer import fit_script  # noqa: PLC0415

        script = fit_script(
            {"page": {"height_mm": 297.0}, "content_bottom_mm": 280.0, "first_band_top_mm": 60.0},
            live=False,
        )
        assert script.count("parentNode===e.parentNode") == 2


class TestDocxDuneListeEnCadres:
    def _docx(self, spec: Json, view: Json, assets: dict[str, bytes] | None = None) -> Any:
        import io  # noqa: PLC0415

        import docx  # noqa: PLC0415

        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415

        data = DocxRenderer(spec, view, cv_font_set(), photo=None).render(assets or {})
        return docx.Document(io.BytesIO(data))

    @staticmethod
    def _paragraphs(document: Any) -> dict[str, Any]:
        cells = [c for t in document.tables for r in t.rows for c in r.cells]
        return {p.text: p for c in cells for p in c.paragraphs if p.text}

    def test_les_competences_sortent_en_liste_simple_a_gauche(self) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415

        from karria.modules.documents.domain.view import from_cv  # noqa: PLC0415

        spec, _, _ = TestListeEnCadres()._builder().build()
        paragraphs = self._paragraphs(self._docx(spec, from_cv(spec, CV_PERSONNE)))
        for name in ("Vente", "Caisse", "Visualisation de données", "Stock", "Accueil"):
            assert name in paragraphs
            assert paragraphs[name].alignment in (None, WD_ALIGN_PARAGRAPH.LEFT)
            assert not paragraphs[name].paragraph_format.left_indent

    def test_letiquette_en_pictogrammes_pose_son_image(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415

        spec, sample, assets = TestEtiquetteEnPictogrammes()._spec()
        document = self._docx(spec, from_sample(spec, sample), assets)
        assert len(document.element.xpath("//wp:anchor//pic:pic")) == 1
        assert {"a@b.fr", "0612"} <= set(self._paragraphs(document))

    def _two_columns(self) -> tuple[Json, Json]:
        lines = [
            raw_line("L1", "JEAN", 90, 10, h=8, size=20, font=BOLD),
            raw_line("L2", "EXPERIENCE", 90, 50, size=11, font=BOLD, caps=True),
            raw_line("L3", "Vendeur", 90, 57),
            raw_line("L4", "CONTACT", 20, 90, size=11, font=BOLD, caps=True),
            raw_line("L5", "a@b.fr", 20, 97, w=14),
        ]
        roles = [
            ("full_name", "header", 0),
            ("section_title", "experiences", 0),
            ("item_title", "experiences", 1),
            ("section_title", "contact", 0),
            ("email", "contact", 0),
        ]
        spec, sample, _ = builder(lines, roles).build()
        spec["bands"][0]["columns"][0]["sections"][0]["margin_top_mm"] = 40.0
        # Le portrait : dans la colonne de gauche, du haut de la bande à « Contact ».
        spec["header"]["photo"] = {
            "x_mm": 20.0,
            "y_mm": 50.0,
            "w_mm": 40.0,
            "h_mm": 36.0,
            "shape": "rect",
            "sample_object_position": "50% 50%",
            "sample_file": "sample_photo.png",
        }
        return spec, sample

    def _lift(self, spec: Json, sample: Json, photo: bytes | None) -> tuple[Any, Any]:
        import io  # noqa: PLC0415

        import docx  # noqa: PLC0415

        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415

        view = from_sample(spec, sample)
        data = DocxRenderer(spec, view, cv_font_set(), photo=photo).render({})
        paragraphs = self._paragraphs(docx.Document(io.BytesIO(data)))
        return (
            paragraphs["CONTACT"].paragraph_format.space_before,
            paragraphs["EXPERIENCE"].paragraph_format.space_before,
        )

    def test_avec_la_photo_la_colonne_passe_sous_le_portrait(self) -> None:
        spec, sample = self._two_columns()
        contact, experience = self._lift(spec, sample, png(40, 36))
        # « Contact » commence 40 mm sous le haut de la bande, comme dans le modèle.
        assert contact.mm == pytest.approx(40.0, abs=0.1)
        assert not experience

    def test_sans_photo_la_colonne_commence_en_haut_de_la_bande(self) -> None:
        spec, sample = self._two_columns()
        contact, experience = self._lift(spec, sample, None)
        assert not contact
        assert not experience

    def test_une_photo_hors_de_la_colonne_ou_au_dessus_de_la_bande_ne_descend_rien(self) -> None:
        spec, sample = self._two_columns()
        spec["header"]["photo"]["x_mm"] = 150.0
        assert not self._lift(spec, sample, png(40, 36))[0]
        spec, sample = self._two_columns()
        # Toute dans l'en-tête : elle finit au-dessus du tableau de la bande.
        spec["header"]["photo"].update(y_mm=5.0, h_mm=30.0)
        assert not self._lift(spec, sample, png(40, 36))[0]

    def test_la_part_deja_prise_par_len_tete_se_retire(self) -> None:
        from karria.modules.documents.domain.view import from_sample  # noqa: PLC0415
        from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer  # noqa: PLC0415

        spec, sample = self._two_columns()
        renderer = DocxRenderer(spec, from_sample(spec, sample), cv_font_set(), photo=png(40, 36))
        column = spec["bands"][0]["columns"][0]
        first = (column["sections"][0], {}, False)
        renderer.header_bottom = 30.0
        assert renderer.photo_lift(0, column, first) == 40.0
        # Des coordonnées d'en-tête descendues jusqu'à 75 mm : le tableau part de là,
        # il ne reste que 15 mm à descendre pour commencer à 90 mm.
        renderer.header_bottom = 75.0
        assert renderer.photo_lift(0, column, first) == 15.0
        # Le tableau part déjà sous le portrait : rien.
        renderer.header_bottom = 88.0
        assert renderer.photo_lift(0, column, first) == 0.0
        # … ou à un demi-millimètre de son bas (86 mm) : la rubrique est déjà sous lui.
        renderer.header_bottom = 85.5
        assert renderer.photo_lift(0, column, first) == 0.0
