"""Spécification + vue → HTML imprimable : texte échappé, polices, ajustement, ornements."""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest

from documents.infrastructure.fakes import (
    CENTERED_ROLES,
    CV_ROLES,
    SERVED,
    centered_template_pdf,
    cv_font_set,
    cv_template_pdf,
    labels_for,
)
from karria.modules.documents.domain.view import from_cv, from_sample
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.html_renderer import (
    HtmlRenderer,
    deco_css,
    esc,
    estimate,
    extra_contact_slots,
    half,
    lh,
    parse_fit,
    placement,
    style_key,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.modules.documents.infrastructure.spec_builder import SpecBuilder

pytestmark = pytest.mark.unit

Json = dict[str, Any]


def _build(pdf: bytes, roles: dict[str, tuple[str, str, int]], shape: str | None) -> tuple:
    with PdfDocument(pdf) as doc:
        page = doc.read()
        labels = labels_for(page.lines, roles)
        labels["photo_shape"] = shape
        return SpecBuilder(doc, page, labels, cv_font_set(), SERVED).build()


@pytest.fixture(scope="module")
def cv_built() -> tuple:
    return _build(cv_template_pdf(), CV_ROLES, "circle")


@pytest.fixture(scope="module")
def centered_built() -> tuple:
    return _build(centered_template_pdf(), CENTERED_ROLES, None)


@pytest.fixture(scope="module")
def fonts() -> FontSet:
    return cv_font_set()


@pytest.fixture
def spec(cv_built: tuple) -> Json:
    return copy.deepcopy(cv_built[0])


@pytest.fixture
def centered(centered_built: tuple) -> Json:
    return copy.deepcopy(centered_built[0])


CV: Json = {
    "identity": {"first_name": "Ada", "last_name": "Lovelace", "headline": "Ingénieure <data>"},
    "contact": {
        "phone": "06 00 00 00 00",
        "email": "ada@example.org",
        "city": "Londres",
        "links": [{"url": "https://www.linkedin.com/in/ada/"}],
    },
    "summary": "Je code <script>alert(1)</script> & j'aime ça.",
    "experiences": [
        {
            "position": "Analyste",
            "company": "Babbage & Co",
            "start_date": "2020-01",
            "end_date": "2023-06",
            "missions": ["Machine analytique", "Notes célèbres"],
        },
        {
            "position": "Stagiaire",
            "company": "Royal Society",
            "start_date": "2019",
            "is_current": True,
            "description": "Lecture de mémoires",
        },
    ],
    "educations": [{"degree": "Mathématiques", "school": "Maison", "start_date": "2015"}],
    "skills": [
        {"name": "Python", "category": "Technique"},
        {"name": "Écoute", "category": "Humain"},
    ],
    "languages": [{"language": "Français", "level": "Natif"}],
    "interests": [{"name": "Poésie"}],
    "projects": [{"title": "Karria", "technologies": ["Python"], "description": "Un CV."}],
}


def render(spec: Json, view: Json, fonts: FontSet, photo: str | None = None) -> str:
    return HtmlRenderer(spec, view, fonts, photo=photo).render()


# ---------------------------------------------------------------------- petites fonctions


class TestOutils:
    def test_le_texte_est_echappe_attributs_compris(self) -> None:
        assert esc('<b a="1">&') == "&lt;b a=&quot;1&quot;&gt;&amp;"
        assert esc(None) == ""

    def test_linterligne_retombe_sur_la_boite_sans_pas_mesure(self) -> None:
        assert lh({"pitch_mm": None, "box_h_mm": 4.0}) == 4.0
        assert half({"pitch_mm": 5.0, "box_h_mm": 4.0}) == 0.5

    def test_le_style_dun_role_suit_lordre_de_repli(self) -> None:
        styles = {
            "section_title": {},
            "experiences.item_title": {},
            "skills.skill": {},
            "header.headline": {},
        }
        assert style_key(styles, "experiences", "item_title") == "experiences.item_title"
        assert style_key(styles, "projects", "item_title") == "experiences.item_title"
        assert style_key(styles, "languages", "language") == "skills.skill"
        assert style_key(styles, "projects", "item_location") == "experiences.item_title"
        assert style_key(styles, "projects", "headline") == "section_title"
        assert style_key({"section_title": {}}, "x", "item_text") == "section_title"

    def test_une_coordonnee_sans_emplacement_prend_le_style_dune_coordonnee_voisine(self) -> None:
        styles = {"section_title": {}, "contact.email": {}, "header.phone": {}}
        assert style_key(styles, "contact", "website") == "contact.email"
        assert (
            style_key({"section_title": {}, "header.phone": {}}, "contact", "website")
            == "header.phone"
        )
        assert style_key({"section_title": {}}, "contact", "website") == "section_title"

    def test_un_filet_vertical_est_une_bordure_gauche(self) -> None:
        css = deco_css({"kind": "vline", "h_mm": 10, "stroke": "#111111"}, 1, 2)
        assert "border-left:0.75pt solid #111111" in css
        assert "height:10mm" in css

    def test_un_filet_horizontal_prend_la_couleur_de_remplissage_a_defaut(self) -> None:
        css = deco_css({"kind": "hline", "w_mm": 9, "fill": "#222222", "stroke_pt": 2}, 0, 0)
        assert "border-top:2pt solid #222222" in css

    def test_une_courbe_carree_est_un_rond_une_autre_un_arrondi(self) -> None:
        rond = deco_css({"kind": "curve", "w_mm": 2, "h_mm": 2, "fill": "#333333"}, 0, 0)
        ovale = deco_css({"kind": "curve", "w_mm": 4, "h_mm": 2}, 0, 0)
        assert "border-radius:50%" in rond
        assert "background:#333333" in rond
        assert "border-radius:30%" in ovale
        assert "background:transparent" in ovale

    def test_un_cadre_trace_a_une_bordure_interieure(self) -> None:
        css = deco_css({"kind": "rect", "w_mm": 4, "h_mm": 2, "stroke": "#444444"}, 0, 0, "x:y;")
        assert "border-radius:0" in css
        assert "box-sizing:border-box" in css
        assert css.endswith("x:y;")

    def test_le_titre_de_document_donne_lajustement(self) -> None:
        assert parse_fit("fit k=0.8 f=0.96 pages=2") == (0.8, 0.96, 2)
        assert parse_fit("CV") == (1.0, 1.0, 1)
        assert parse_fit("fit k=abc") == (1.0, 1.0, 1)
        assert parse_fit("fit bruit k=0.7") == (0.7, 1.0, 1)


# ---------------------------------------------------------------------- placement


class TestPlacement:
    def test_une_rubrique_absente_emprunte_le_gabarit_dune_soeur(
        self, spec: Json, fonts: FontSet
    ) -> None:
        view = {"header": {}, "sections": [{"kind": "interests", "title": "Loisirs", "items": []}]}
        bands = placement(spec, view, fonts)
        borrowed = [t for b in bands for col in b["columns"] for t in col]
        assert len(borrowed) == 1
        clone, _, flag = borrowed[0]
        assert flag is True
        assert clone["kind"] == "interests"
        assert clone["slot_order"] == ["interest"]
        assert clone["slots"][0]["roles"] == ["interest"]

    def test_un_emprunt_va_dans_la_colonne_la_plus_courte(self, spec: Json, fonts: FontSet) -> None:
        view = from_cv(spec, CV)
        bands = placement(spec, view, fonts)
        kinds = [[[s["kind"] for s, _, _ in col] for col in b["columns"]] for b in bands]
        assert "projects" in kinds[0][0] + kinds[0][1]
        assert "interests" in kinds[1][0] + kinds[1][1]

    def test_une_rubrique_sans_soeur_dans_le_modele_est_ignoree(
        self, centered: Json, fonts: FontSet
    ) -> None:
        view = {"header": {}, "sections": [{"kind": "contact", "title": "Contact", "items": []}]}
        bands = placement(centered, view, fonts)
        assert bands[0]["columns"] == [[]]

    def test_sans_ecart_mesure_lemprunt_prend_dix_millimetres(
        self, centered: Json, fonts: FontSet
    ) -> None:
        centered["bands"][0]["columns"][0]["sections"].pop()
        view = {"header": {}, "sections": [{"kind": "skills", "title": "Comp", "items": []}]}
        clone = placement(centered, view, fonts)[0]["columns"][0][0][0]
        assert clone["margin_top_mm"] == 10.0

    def test_lestimation_compte_titre_et_lignes(self, spec: Json, fonts: FontSet) -> None:
        exp = spec["bands"][0]["columns"][1]["sections"][1]
        sec = {
            "kind": "experiences",
            "title": "Expériences",
            "items": [
                {"item_title": "A", "item_dates": "2020", "bullet": ["x", "y"]},
                {"item_title": "B", "item_subtitle": "C"},
            ],
        }
        h = estimate(spec["styles"], exp, sec, 80.0, fonts)
        assert h > exp["margin_top_mm"] + 10

    def test_une_coordonnee_sans_emplacement_ajoute_une_ligne(self, spec: Json) -> None:
        contact = spec["bands"][0]["columns"][0]["sections"][0]
        sec = {"kind": "contact", "items": [{"email": "a@b", "phone": "01", "website": "x.fr"}]}
        slots, order = extra_contact_slots(contact, sec)
        assert order == ["email", "address", "phone", "website"]
        assert slots["phone"]["repeat_margin_mm"] is None
        assert extra_contact_slots(contact, {"kind": "skills", "items": []})[1] == [
            "email",
            "address",
        ]


# ---------------------------------------------------------------------- rendu complet


class TestRendu:
    def test_le_texte_de_la_personne_est_echappe(self, spec: Json, fonts: FontSet) -> None:
        html = render(spec, from_cv(spec, CV), fonts)
        assert "<script>alert" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; j&#x27;aime ça." in html
        assert "Ingénieure &lt;data&gt;" in html

    def test_les_polices_voyagent_en_font_face(self, spec: Json, fonts: FontSet) -> None:
        html = render(spec, from_cv(spec, CV), fonts)
        assert "@font-face{font-family:'Testa';src:url('Testa-400.ttf');font-weight:400" in html
        assert "src:url('Testa-700.ttf')" in html
        assert "font-family:'Testa','Testa',sans-serif" in html

    def test_le_script_dajustement_ecrit_letat_dans_le_titre(
        self, spec: Json, fonts: FontSet
    ) -> None:
        html = render(spec, from_cv(spec, CV), fonts)
        assert "document.fonts.ready" in html
        assert "window.fitDone=true" in html
        assert "var K=[1,0.9,0.8,0.7,0.6],F=[1,0.96,0.92,0.88]" in html
        assert html.startswith('<!doctype html><html lang="fr">')

    def test_sans_poste_vise_lemplacement_est_omis(self, spec: Json, fonts: FontSet) -> None:
        cv = {**CV, "identity": {"first_name": "Ada", "last_name": "Lovelace"}}
        html = render(spec, from_cv(spec, cv), fonts)
        assert 'class="s-header-headline"' not in html

    def test_le_poste_vise_synthetise_est_centre_sous_un_nom_centre(
        self, centered: Json, fonts: FontSet
    ) -> None:
        html = render(centered, from_cv(centered, CV), fonts)
        assert re.search(
            r'class="s-header-headline" style="position:absolute;left:75\.0mm;'
            r"transform:translateX\(-50%\)",
            html,
        )

    def test_la_photo_suit_le_cadre_et_la_forme(self, spec: Json, fonts: FontSet) -> None:
        html = render(spec, from_cv(spec, CV), fonts, photo="photo.jpg")
        assert 'src="photo.jpg"' in html
        assert "border-radius:50%" in html
        assert "object-position:50% 30%" in html

    def test_lexemple_garde_la_position_de_sa_photo(self, cv_built: tuple, fonts: FontSet) -> None:
        spec, sample, _ = copy.deepcopy(cv_built)
        spec["header"]["photo"]["shape"] = "rounded"
        html = render(spec, from_sample(spec, sample), fonts, photo="sample_photo.png")
        assert "object-position:50.0% 50.0%" in html
        assert "border-radius:8%" in html

    def test_sans_fichier_photo_aucun_cadre(self, spec: Json, fonts: FontSet) -> None:
        assert "object-fit:cover" not in render(spec, from_cv(spec, CV), fonts)

    def test_lexemple_rend_puces_frise_separateur_et_pictogrammes(
        self, cv_built: tuple, fonts: FontSet
    ) -> None:
        spec, sample, _ = copy.deepcopy(cv_built)
        html = render(spec, from_sample(spec, sample), fonts)
        assert 'src="icon_email.png"' in html
        assert 'src="icon_phone.png"' in html
        assert "border-radius:50%;background:#336699" in html  # puce ronde
        assert "border-radius:0;background:#222222" in html  # puce carrée
        assert "border-left:0.5pt solid #999999" in html  # séparateur « | »
        assert "height:auto;bottom:" in html  # frise étirée
        assert "border-top:1.0pt solid #336699" in html  # filet entre bandes
        assert "background:#EEEEEE" in html  # ornement de page
        assert "text-align:justify" in html

    def test_titre_sur_deux_lignes_et_filet_de_rubrique_etire(
        self, centered_built: tuple, fonts: FontSet
    ) -> None:
        spec, sample, _ = copy.deepcopy(centered_built)
        html = render(spec, from_sample(spec, sample), fonts)
        assert "line-height:5.000mm;top:" in html
        assert "CENTRES INTERETS" in html
        assert "border-left:0.75pt solid #AA3300" in html
        assert "height:auto;bottom:-2.00mm" in html

    def test_deux_categories_de_liste_gardent_lecart_titre_liste(
        self, spec: Json, fonts: FontSet
    ) -> None:
        html = render(spec, from_cv(spec, CV), fonts)
        assert html.count('class="t s-skills-subsection_title"') == 2

    def test_un_element_vide_ne_produit_rien(self, spec: Json, fonts: FontSet) -> None:
        view = {
            "header": {},
            "sections": [
                {"kind": "experiences", "title": "Exp", "items": [{}, {"item_title": "A"}]}
            ],
        }
        html = render(spec, view, fonts)
        assert html.count('class="item"') == 1

    def test_une_bande_vide_est_sautee(self, spec: Json, fonts: FontSet) -> None:
        view = {
            "header": {},
            "sections": [
                {"kind": "languages", "title": "Langues", "items": [{"language": ["FR"]}]}
            ],
        }
        html = render(spec, view, fonts)
        assert html.count('class="band"') == 1
        assert "margin-top:calc(24.71mm*var(--k))" in html

    def test_une_premiere_colonne_vide_part_du_haut(self, spec: Json, fonts: FontSet) -> None:
        spec["bands"][0]["columns"][0]["sections"] = []
        html = render(spec, from_cv(spec, CV), fonts)
        assert 'class="flow"' in html

    def test_le_dernier_element_sans_trait_propre_na_pas_de_frise(
        self, cv_built: tuple, fonts: FontSet
    ) -> None:
        spec, sample, _ = copy.deepcopy(cv_built)
        exp = spec["bands"][0]["columns"][1]["sections"][1]
        frise = exp["decorations"][0]
        html_avec = render(spec, from_sample(spec, sample), fonts)
        frise["last_bottom_inset_mm"] = None
        html_sans = render(spec, from_sample(spec, sample), fonts)
        assert html_avec.count("border-left:1.0pt solid #336699") == 2
        assert html_sans.count("border-left:1.0pt solid #336699") == 1
        frise.pop("stretch")
        frise.pop("connector")
        assert "border-left:1.0pt solid #336699" in render(spec, from_sample(spec, sample), fonts)

    def test_un_champ_reuni_sans_separateur_prend_lecart_mesure(
        self, spec: Json, fonts: FontSet
    ) -> None:
        slot = spec["bands"][0]["columns"][1]["sections"][1]["slots"][0]
        slot["separator"] = None
        slot["gap_mm"] = None
        html = render(spec, from_cv(spec, CV), fonts)
        assert '<span style="display:inline-block;width:3.00mm"></span>' in html


# ---------------------------------------------------------------------- en-tête


class TestEnTete:
    def test_la_place_sarrete_avant_la_photo_et_le_voisin(self, spec: Json, fonts: FontSet) -> None:
        view = from_cv(spec, CV)
        renderer = HtmlRenderer(spec, view, fonts, photo="p.jpg")
        name, phone = spec["header"]["elements"][0], spec["header"]["elements"][1]
        st = spec["styles"][name["style"]]
        assert renderer.header_limit(name, st) == pytest.approx(phone["x_mm"] - 4 - name["x_mm"])
        st_phone = spec["styles"][phone["style"]]
        photo = spec["header"]["photo"]
        assert renderer.header_limit(phone, st_phone) == pytest.approx(
            photo["x_mm"] - 4 - phone["x_mm"]
        )

    @pytest.mark.parametrize(
        ("avail", "expected"),
        [
            (500.0, ""),
            (None, ";letter-spacing:"),
            (None, ";font-size:"),
            (1.0, ";white-space:normal;width:1.00mm"),
        ],
    )
    def test_un_texte_trop_long_est_resserre_reduit_puis_replie(
        self, spec: Json, fonts: FontSet, avail: float | None, expected: str
    ) -> None:
        renderer = HtmlRenderer(spec, {"header": {}, "sections": []}, fonts, photo=None)
        st = {**spec["styles"]["header.full_name"], "letter_spacing_pt": 2.0}
        text = "JEAN DUPONT"
        natural = fonts.width_mm(text, st)
        if avail is None and expected == ";letter-spacing:":
            avail = fonts.width_mm(text, st, ls_pt=2.0 * 0.5)
        elif avail is None:
            avail = fonts.width_mm(text, st, size_pt=st["size_pt"] * 0.9, ls_pt=0.6)
        css = renderer.fit_header_text(text, st, avail)
        assert expected in css
        if expected == ";letter-spacing:":
            assert "font-size" not in css
        assert avail < natural or css == ""

    def test_aucun_style_aucune_famille_principale(self, fonts: FontSet) -> None:
        renderer = HtmlRenderer({"styles": {}}, {}, fonts, photo=None)
        assert renderer.main_family == "sans-serif"
