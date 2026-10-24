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
    med,
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
        assert skills["marker"]["size_mm"] == pytest.approx(1.2, abs=0.01)
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

    def test_une_ligne_de_dates_ne_se_prolonge_jamais(self) -> None:
        b = builder([], [])
        lines = [
            Line(raw_line("L1", "2020", 10, 10), {"role": "item_dates", "section": "x", "item": 1}),
            Line(raw_line("L2", "2021", 10, 14), {"role": "item_dates", "section": "x", "item": 1}),
        ]
        assert len(b.elements(lines, {})) == 2

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
