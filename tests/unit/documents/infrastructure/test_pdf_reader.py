"""Lecture déterministe d'un PDF : lignes stylées, formes, images, polices, raster.

Chaque PDF est construit en mémoire (pypdfium2) : aucun binaire versionné.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image

from documents.infrastructure.fakes import BOLD, MM, REGULAR, PdfBuilder, cv_fonts, png
from karria.modules.documents.infrastructure import pdf_reader
from karria.modules.documents.infrastructure.pdf_reader import (
    EmbeddedFont,
    PdfDocument,
    VerticalMetrics,
    base_font_name,
    hex_color,
    merge_baselines,
    style_from_name,
    words_from_glyphs,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fonctions pures
# ---------------------------------------------------------------------------


class TestFonctionsPures:
    def test_le_prefixe_de_sous_ensemble_est_retire(self) -> None:
        assert base_font_name("ABCDEF+Raleway-Bold") == "Raleway-Bold"
        assert base_font_name("Raleway") == "Raleway"
        assert base_font_name("") == ""

    @pytest.mark.parametrize(
        ("color", "expected"),
        [
            (None, None),
            ((), None),
            (0.5, "#808080"),
            ((1,), "#FFFFFF"),
            ([1, 0, 0], "#FF0000"),
            ((0, 0, 0, 1), "#000000"),
            ((1, 0, 0, 0), "#00FFFF"),
        ],
    )
    def test_une_couleur_pdfminer_devient_hexadecimale(self, color: Any, expected: Any) -> None:
        assert hex_color(color) == expected

    def test_la_boite_verticale_est_ascendante_moins_descendante(self) -> None:
        assert VerticalMetrics(0.8, -0.2).box == pytest.approx(1.0)
        assert VerticalMetrics().box == pytest.approx(1.2)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Raleway-BoldItalic", (True, True)),
            ("Lato-Black", (True, False)),
            ("Lato-Oblique", (False, True)),
            ("Lato-Regular", (False, False)),
        ],
    )
    def test_la_graisse_et_le_style_se_lisent_dans_le_nom(
        self, name: str, expected: tuple[bool, bool]
    ) -> None:
        assert style_from_name(name) == expected

    def test_les_mots_se_reconstruisent_par_lecart_entre_glyphes(self) -> None:
        def g(t: str, x0: float, x1: float) -> dict[str, Any]:
            return {"text": t, "x0": x0, "x1": x1, "size": 10}

        assert words_from_glyphs([]) == ""
        assert words_from_glyphs([g("A", 0, 5)]) == "A"
        glyphs = [g("A", 0, 5), g("B", 6, 11), g("C", 12, 17), g("D", 30, 35)]
        assert words_from_glyphs(glyphs) == "ABC D"

    def test_un_chevauchement_de_glyphes_ne_rend_pas_lecart_usuel_negatif(self) -> None:
        glyphs = [
            {"text": "A", "x0": 0, "x1": 5, "size": 10},
            {"text": "B", "x0": 3, "x1": 8, "size": 10},
        ]
        assert words_from_glyphs(glyphs) == "AB"

    def test_les_morceaux_dune_ligne_justifiee_sont_recolles(self) -> None:
        def ln(text: str, x: float, **kw: Any) -> dict[str, Any]:
            base = {
                "id": "",
                "text": text,
                "font": "F",
                "size": 9.0,
                "color": "#000000",
                "x": x,
                "y": 10.0,
                "w": 10.0,
                "h": 4.0,
                "baseline": 13.0,
                "chars": [(text, x, x + 10)],
                "caps": False,
            }
            return {**base, **kw}

        merged = merge_baselines(
            [ln("AB", 0), ln("CD", 12, h=5.0), ln("ef", 50), ln("gh", 62, color="#FF0000")]
        )
        assert [m["text"] for m in merged] == ["AB CD", "ef", "gh"]
        assert merged[0]["caps"] is True
        assert merged[0]["w"] == 22
        assert merged[0]["h"] == 5.0
        assert [m["id"] for m in merged] == ["L1", "L2", "L3"]


class TestOutilsInternes:
    def test_composer_applique_la_matrice_interieure_puis_lexterieure(self) -> None:
        inner = (2.0, 0.0, 0.0, 2.0, 1.0, 1.0)
        outer = (1.0, 0.0, 0.0, 1.0, 10.0, 20.0)
        assert pdf_reader._compose(inner, outer) == (2.0, 0.0, 0.0, 2.0, 11.0, 21.0)

    def test_une_boite_transformee_reste_ordonnee(self) -> None:
        rotate = (0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
        assert pdf_reader._transform_box((0, 0, 2, 1), rotate) == (-1.0, 0.0, 0.0, 2.0)

    def test_une_matrice_degeneree_garde_une_echelle_unitaire(self) -> None:
        assert pdf_reader._scale((0.0, 0.0, 0.0, 0.0, 0.0, 0.0)) == 1.0
        assert pdf_reader._scale((2.0, 0.0, 0.0, 2.0, 0.0, 0.0)) == 2.0

    def test_une_chaine_vide_de_pdfium_donne_une_chaine_vide(self) -> None:
        assert pdf_reader._font_string(lambda *_: 0, None) == ""

    def test_une_couleur_illisible_donne_none(self) -> None:
        assert pdf_reader._color(lambda *_: 0, None) is None


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def text_pdf() -> bytes:
    b = PdfBuilder(100, 60, embedded=cv_fonts())
    b.text("Bonjour Monde", 10, 15, size=12, font=REGULAR, color="#112233")
    b.text("TITRE", 10, 25, size=10, font=BOLD)
    b.text("A", 10, 35, size=10)
    b.text("B", 60, 35, size=10)
    b.text("Ab", 10, 45, size=4)
    b.text("cd", 15.5, 45, size=4)
    b.text("Mi", 10, 55, size=10, font="Helvetica")
    b.text("xte", 13.9, 55, size=10, font="Helvetica-Bold")
    return b.to_bytes()


@pytest.fixture(scope="module")
def text_doc(text_pdf: bytes) -> Any:
    doc = PdfDocument(text_pdf)
    yield doc
    doc.close()


class TestLignesDeTexte:
    def test_les_lignes_sortent_en_mm_dans_lordre_de_lecture(self, text_doc: PdfDocument) -> None:
        lines = text_doc.text_lines()
        first = lines[0]
        assert first["id"] == "L1"
        assert first["text"] == "Bonjour Monde"
        assert first["font"] == REGULAR
        assert first["size"] == 12.0
        assert first["color"] == "#112233"
        assert first["x"] == 10.0
        assert first["baseline"] == pytest.approx(15.0, abs=0.01)
        assert first["caps"] is False
        assert first["spans"] is None
        # Boîte posée avec les métriques incorporées (ascendante 0,9).
        assert first["y"] == pytest.approx(15.0 - 0.9 * 12 / MM, abs=0.02)

    def test_le_gras_vient_du_nom_de_la_police(self, text_doc: PdfDocument) -> None:
        title = next(ln for ln in text_doc.text_lines() if ln["text"] == "TITRE")
        assert title["bold"] is True
        assert title["caps"] is True

    def test_un_grand_blanc_coupe_une_ligne_en_deux(self, text_doc: PdfDocument) -> None:
        texts = [ln["text"] for ln in text_doc.text_lines()]
        assert "A" in texts
        assert "B" in texts

    def test_un_petit_blanc_de_justification_est_recolle(self, text_doc: PdfDocument) -> None:
        texts = [ln["text"] for ln in text_doc.text_lines()]
        assert "Ab cd" in texts

    def test_une_ligne_a_deux_polices_garde_ses_morceaux(self, text_doc: PdfDocument) -> None:
        mixed = next(ln for ln in text_doc.text_lines() if ln["text"].startswith("Mi"))
        assert mixed["spans"] is not None
        assert {s["font"] for s in mixed["spans"]} == {"Helvetica", "Helvetica-Bold"}

    def test_les_metriques_donnees_posent_la_boite(self, text_doc: PdfDocument) -> None:
        def metrics(font: str) -> VerticalMetrics | None:
            return VerticalMetrics(0.5, -0.5) if font == REGULAR else None

        lines = text_doc.text_lines(metrics=metrics)
        first = lines[0]
        assert first["h"] == pytest.approx(12 / MM, abs=0.01)
        assert first["y"] == pytest.approx(first["baseline"] - 0.5 * 12 / MM, abs=0.01)

    def test_sans_police_connue_les_metriques_par_defaut_servent(
        self, text_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with PdfDocument(text_pdf) as doc:
            monkeypatch.setattr(doc, "fonts", lambda index=0: {})
            first = doc.text_lines()[0]
        assert first["h"] == pytest.approx(VerticalMetrics().box * 12 / MM, abs=0.01)

    def test_la_police_incorporee_complete_graisse_et_italique(
        self, text_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        heavy = EmbeddedFont(REGULAR, "Testa", 700, True, True, None, 0, 0.0, 0.0)
        with PdfDocument(text_pdf) as doc:
            monkeypatch.setattr(doc, "fonts", lambda index=0: {REGULAR: heavy})
            first = doc.text_lines()[0]
        assert first["bold"] is True
        assert first["italic"] is True
        # Ascendante nulle : métriques par défaut.
        assert first["h"] == pytest.approx(VerticalMetrics().box * 12 / MM, abs=0.01)

    def test_les_caracteres_couches_et_les_lignes_blanches_sont_ignores(
        self, text_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with PdfDocument(text_pdf) as doc:
            real = doc.chars()
            extra = [
                {**real[0], "upright": False, "text": "Z"},
                {**real[0], "text": " ", "matrix": (1, 0, 0, 1, 0, 1)},
            ]
            monkeypatch.setattr(doc, "chars", lambda index=0: real + extra)
            texts = [ln["text"] for ln in doc.text_lines()]
        assert "Z" not in texts
        assert all(t.strip() for t in texts)

    def test_des_blancs_suivis_dun_grand_ecart_ne_font_pas_planter(self) -> None:
        b = PdfBuilder(100, 30)
        b.text("     ", 10, 20, size=10)
        b.text("B", 60, 20, size=10)
        with PdfDocument(b.to_bytes()) as doc:
            assert [ln["text"] for ln in doc.text_lines()] == ["B"]


class TestPolices:
    def test_les_polices_incorporees_sont_decrites(self, text_doc: PdfDocument) -> None:
        fonts = text_doc.fonts()
        regular = fonts[REGULAR]
        assert regular.embedded is True
        assert regular.data is not None
        assert regular.family == "Testa"
        assert regular.ascent == pytest.approx(0.9, abs=0.01)
        assert regular.italic is False
        helv = fonts["Helvetica"]
        assert helv.embedded is False
        assert helv.data is None

    def test_des_octets_absents_donnent_none(
        self, text_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdfium_c, "FPDFFont_GetFontData", lambda *_: 0)
        with PdfDocument(text_pdf) as doc:
            assert doc.fonts()[REGULAR].data is None

    def test_un_objet_texte_sans_police_est_ignore(
        self, text_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdfium_c, "FPDFTextObj_GetFont", lambda *_: None)
        with PdfDocument(text_pdf) as doc:
            assert doc.fonts() == {}


# ---------------------------------------------------------------------------
# Formes et images
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shapes_pdf() -> bytes:
    b = PdfBuilder(100, 80)
    b.rect(5, 10, 50, 0.3, fill="#FF0000")  # filet plein horizontal
    b.rect(60, 10, 0.3, 40, fill="#00AA00")  # filet plein vertical
    b.line(70, 10, 70, 60, stroke="#0000FF", stroke_pt=1)  # filet tracé
    b.circle(80, 70, 1, fill="#123456")  # pastille
    b.rect(5, 60, 10, 10, fill="#FFFFFF", alpha=0)  # invisible
    b.rect(20, 60, 10, 10, fill="#ABCDEF", stroke="#000000", stroke_pt=0.5)  # cadre
    b.rect(35, 60, 10, 10, fill="#FEDCBA", stroke="#000000", alpha=0)  # transparent
    b.image(png(), 60, 5, 20, 15)
    return b.to_bytes()


@pytest.fixture(scope="module")
def shapes(shapes_pdf: bytes) -> list[dict[str, Any]]:
    with PdfDocument(shapes_pdf) as doc:
        return doc.drawings()


class TestFormes:
    def test_un_rectangle_fin_plein_devient_un_filet_horizontal(
        self, shapes: list[dict[str, Any]]
    ) -> None:
        hline = next(d for d in shapes if d["kind"] == "hline")
        assert hline["stroke"] == "#FF0000"
        assert hline["fill"] is None
        assert hline["stroke_width_pt"] == pytest.approx(0.3 * MM, abs=0.02)
        assert (hline["x"], hline["y"], hline["w"]) == (5.0, 10.0, 50.0)

    def test_un_rectangle_fin_vertical_devient_un_filet_vertical(
        self, shapes: list[dict[str, Any]]
    ) -> None:
        vlines = [d for d in shapes if d["kind"] == "vline"]
        assert {d["stroke"] for d in vlines} == {"#00AA00", "#0000FF"}

    def test_les_bornes_dun_trait_reviennent_au_trace(self, shapes: list[dict[str, Any]]) -> None:
        stroked = next(d for d in shapes if d["stroke"] == "#0000FF")
        assert stroked["x"] == pytest.approx(70.0, abs=0.01)
        assert stroked["w"] == 0.0
        assert stroked["stroke_width_pt"] == 1.0

    def test_une_pastille_de_courbes_est_une_courbe(self, shapes: list[dict[str, Any]]) -> None:
        curve = next(d for d in shapes if d["kind"] == "curve")
        assert curve["fill"] == "#123456"
        assert curve["w"] == pytest.approx(2.0, abs=0.01)

    def test_une_forme_transparente_nexiste_pas(self, shapes: list[dict[str, Any]]) -> None:
        assert all(d["fill"] != "#FFFFFF" for d in shapes)
        assert all(d["fill"] != "#FEDCBA" for d in shapes)

    def test_un_cadre_garde_remplissage_et_contour(self, shapes: list[dict[str, Any]]) -> None:
        frame = next(d for d in shapes if d["fill"] == "#ABCDEF")
        assert frame["kind"] == "rect"
        assert frame["stroke"] == "#000000"
        assert frame["x"] == pytest.approx(20.0, abs=0.01)
        assert frame["w"] == pytest.approx(10.0, abs=0.01)

    def test_un_formulaire_est_deplie_avec_sa_transformation(self) -> None:
        src = PdfBuilder(50, 50)
        src.rect(5, 5, 10, 10, fill="#FF0000")
        src.text("Hi", 5, 30)
        source = pdfium.PdfDocument(src.to_bytes())
        dst = pdfium.PdfDocument.new()
        page = dst.new_page(100 * MM, 100 * MM)
        xobject = pdfium_c.FPDF_NewXObjectFromPage(dst.raw, source.raw, 0)
        form = pdfium_c.FPDF_NewFormObjectFromXObject(xobject)
        pdfium_c.FPDFPageObj_Transform(form, 2, 0, 0, 2, 10 * MM, 0)
        pdfium_c.FPDFPage_InsertObject(page.raw, form)
        pdfium_c.FPDFPage_GenerateContent(page.raw)
        pdfium_c.FPDF_CloseXObject(xobject)
        buffer = io.BytesIO()
        dst.save(buffer)
        with PdfDocument(buffer.getvalue()) as doc:
            (rect,) = doc.drawings()
            fonts = doc.fonts()
        assert (rect["x"], rect["y"], rect["w"], rect["h"]) == (20.0, 10.0, 20.0, 20.0)
        assert rect["stroke_width_pt"] == 2.0
        assert "Helvetica" in fonts

    def test_un_trait_plus_large_que_sa_forme_se_reduit_a_son_centre(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        b = PdfBuilder(30, 30)
        b.rect(10, 10, 0.5, 0.5, stroke="#000000", stroke_pt=3)
        data = b.to_bytes()
        # Des bornes qui débordent moins que l'épaisseur du trait.
        monkeypatch.setattr(pdf_reader._Placed, "bounds", lambda _self: (10.0, 20.0, 11.0, 21.0))
        with PdfDocument(data) as doc:
            (dot,) = doc.drawings()
        assert (dot["w"], dot["h"]) == (0.0, 0.0)
        assert dot["x"] == pytest.approx(10.5 / MM, abs=0.01)

    def test_le_plus_petit_chemin_de_detourage_est_retenu(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Chemin vide ignoré, point illisible ignoré, plus petite aire gagnante."""
        paths = {
            0: [],
            1: [(0, 0), (100, 100)],
            2: [(10, 10), None, (20, 30)],
            3: [(0, 0), (50, 50)],
        }

        def get_point(segment: Any, x: Any, y: Any) -> int:
            if segment is None:
                return 0
            x._obj.value, y._obj.value = segment
            return 1

        monkeypatch.setattr(pdfium_c, "FPDFPageObj_GetClipPath", lambda *_: 1)
        monkeypatch.setattr(pdfium_c, "FPDFClipPath_CountPaths", lambda *_: len(paths))
        monkeypatch.setattr(pdfium_c, "FPDFClipPath_CountPathSegments", lambda _c, p: len(paths[p]))
        monkeypatch.setattr(pdfium_c, "FPDFClipPath_GetPathSegment", lambda _c, p, s: paths[p][s])
        monkeypatch.setattr(pdfium_c, "FPDFPathSegment_GetPoint", get_point)
        assert pdf_reader._clip_box(None, pdf_reader._IDENTITY) == (10.0, 10.0, 20.0, 30.0)

    def test_sans_detourage_pas_de_cadre(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfium_c, "FPDFPageObj_GetClipPath", lambda *_: None)
        assert pdf_reader._clip_box(None, pdf_reader._IDENTITY) is None

    def test_une_matrice_illisible_vaut_lidentite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfium_c, "FPDFPageObj_GetMatrix", lambda *_: 0)
        assert pdf_reader._matrix(None) == pdf_reader._IDENTITY


class TestImages:
    def test_une_image_sort_avec_sa_boite_et_ses_octets(self, shapes_pdf: bytes) -> None:
        with PdfDocument(shapes_pdf) as doc:
            (image,) = doc.images()
        assert (image["x"], image["y"], image["w"], image["h"]) == (60.0, 5.0, 20.0, 15.0)
        assert image["clip"] is None
        assert Image.open(io.BytesIO(image["png"])).format == "PNG"

    def test_le_cadre_de_detourage_est_lu(self) -> None:
        b = PdfBuilder(100, 80)
        b.image(png(), 60, 5, 20, 15)
        b.clip(62, 6, 10, 10)
        with PdfDocument(b.to_bytes()) as doc:
            (image,) = doc.images()
        assert image["clip"] == {"x": 62.0, "y": 6.0, "w": 10.0, "h": 10.0}

    def test_une_image_sans_bitmap_est_ignoree(
        self, shapes_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdfium_c, "FPDFImageObj_GetBitmap", lambda *_: None)
        with PdfDocument(shapes_pdf) as doc:
            assert doc.images() == []


# ---------------------------------------------------------------------------
# Raster, lecture complète, métadonnées
# ---------------------------------------------------------------------------


class TestRasterEtLecture:
    def test_la_page_en_niveaux_de_gris(self, shapes_pdf: bytes) -> None:
        with PdfDocument(shapes_pdf) as doc:
            gray = doc.render_gray(dpi=36)
        assert gray.ndim == 2
        assert gray.shape[0] == pytest.approx(80 * 36 / 25.4, abs=1)
        assert gray.shape[1] == pytest.approx(100 * 36 / 25.4, abs=1)

    def test_un_raster_a_trois_dimensions_est_reduit_a_un_canal(
        self, shapes_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import numpy as np  # noqa: PLC0415

        with PdfDocument(shapes_pdf) as doc:
            page = doc._pdf[0]
            fake = SimpleNamespace(to_numpy=lambda: np.zeros((4, 5, 3), dtype=np.uint8))
            monkeypatch.setattr(type(page), "render", lambda *_a, **_k: fake)
            assert doc.render_gray().shape == (4, 5)

    def test_une_zone_se_rasterise_seule(self, shapes_pdf: bytes) -> None:
        with PdfDocument(shapes_pdf) as doc:
            full = Image.open(io.BytesIO(doc.render_png(dpi=72)))
            zone = Image.open(io.BytesIO(doc.render_png(dpi=72, box_mm=(10, 10, 20, 20))))
        assert full.size[0] == pytest.approx(100 * MM, abs=1)
        assert zone.size[0] == pytest.approx(20 * MM, abs=1)
        assert zone.size[1] == pytest.approx(20 * MM, abs=1)

    def test_un_rond_vu_est_un_rond_et_un_carre_non(self) -> None:
        b = PdfBuilder(30, 30)
        b.circle(10, 10, 1, fill="#000000")
        b.rect(19, 9, 2, 2, fill="#000000")
        with PdfDocument(b.to_bytes()) as doc:
            assert doc.is_round((9, 9, 2, 2)) is True
            assert doc.is_round((19, 9, 2, 2)) is False

    def test_la_lecture_complete_reunit_tout(self, text_pdf: bytes) -> None:
        b = PdfBuilder(60, 40)
        b.text("Salut", 5, 10)
        b.rect(5, 20, 10, 10, fill="#FF0000")
        b.image(png(), 30, 20, 10, 10)
        data = b.to_bytes(producer="Canva")
        with PdfDocument(data) as doc:
            page = doc.read()
            assert doc.page_count == 1
        assert (page.width_mm, page.height_mm, page.pages) == (60.0, 40.0, 1)
        assert [ln["text"] for ln in page.lines] == ["Salut"]
        assert len(page.drawings) == 1
        assert len(page.images) == 1
        assert "Helvetica" in page.fonts
        assert page.producer == "Canva"

    def test_le_titre_du_document_est_relu(self) -> None:
        data = PdfBuilder(20, 20).to_bytes(title="fit k=0.8 f=1 pages=1")
        with PdfDocument(data) as doc:
            assert doc.metadata_title() == "fit k=0.8 f=1 pages=1"
        with PdfDocument(PdfBuilder(20, 20).to_bytes()) as doc:
            assert doc.metadata_title() == ""

    def test_un_titre_qui_nest_pas_une_chaine_est_converti(self) -> None:
        with PdfDocument(PdfBuilder(20, 20).to_bytes()) as doc:
            real = doc._plumber
            doc._plumber = SimpleNamespace(metadata={"Title": 42}, close=real.close)
            assert doc.metadata_title() == "42"

    def test_le_gestionnaire_de_contexte_libere_les_pages(self, shapes_pdf: bytes) -> None:
        with PdfDocument(shapes_pdf) as doc:
            doc.drawings()
            assert doc._pages
        assert doc._pages == {}
