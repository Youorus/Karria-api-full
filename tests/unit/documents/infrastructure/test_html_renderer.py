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
    css_class,
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
    def test_un_nom_de_classe_ne_garde_que_lettres_chiffres_et_tirets(self) -> None:
        assert css_class("experiences.bullet") == "s-experiences-bullet"
        assert (
            css_class("other:réalisations clés.item_text") == "s-other-r-alisations-cl-s-item-text"
        )
        assert css_class("header.full_name") == "s-header-full-name"
        assert css_class('x" onload="y') == "s-x-onload-y"

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
        assert slots["website"]["label"] is None  # pas l'étiquette « Address » du dernier
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
        # L'exemple pose l'image à son échelle réelle ; sans cette mesure, l'ancienne position.
        assert "object-fit" not in html
        assert "position:absolute;left:0.0mm;top:-2.5mm;width:25.0mm;height:30.0mm" in html
        assert "border-radius:8%" in html
        del spec["header"]["photo"]["sample_image"]
        html = render(spec, from_sample(spec, sample), fonts, photo="sample_photo.png")
        assert "object-position:50.0% 50.0%" in html

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
        assert html.count('class="t s-skills-subsection-title"') == 2

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

    def test_un_element_a_droite_ou_centre_setend_vers_la_gauche(
        self, spec: Json, fonts: FontSet
    ) -> None:
        view = from_cv(spec, CV)
        renderer = HtmlRenderer(spec, view, fonts, photo=None)
        name, phone = spec["header"]["elements"][0], spec["header"]["elements"][1]
        st = spec["styles"][phone["style"]]
        margin = min(c["x_mm"] for b in spec["bands"] for c in b["columns"])
        right = {**phone, "align": "right", "rx_mm": 190.0}
        # Le nom est sur la même ligne, à gauche : la place s'arrête après lui.
        assert renderer.header_limit(right, st) == pytest.approx(
            190.0 - (name["x_mm"] + name["w_mm"] + 4)
        )
        centered = {**phone, "align": "center", "cx_mm": 150.0, "y_mm": phone["y_mm"] + 30}
        assert renderer.header_limit(centered, st) == pytest.approx(
            2 * min(150.0 - margin, 210.0 - margin - 150.0)
        )
        # Un élément sous la ligne du téléphone (boîtes disjointes) n'est pas son voisin.
        below = {**name, "y_mm": phone["y_mm"] + st["box_h_mm"]}
        assert renderer.header_limit(below, spec["styles"][name["style"]]) > phone["x_mm"]
        # À droite de la photo, la place commence après elle.
        with_photo = HtmlRenderer(spec, view, fonts, photo="p.jpg")
        photo = spec["header"]["photo"]
        beside = {**phone, "x_mm": photo["x_mm"] + photo["w_mm"] + 1, "y_mm": photo["y_mm"] + 2}
        assert with_photo.header_limit(beside, st) == pytest.approx(210.0 - margin - beside["x_mm"])
        beside_right = {**beside, "align": "right", "rx_mm": 205.0}
        assert with_photo.header_limit(beside_right, st) == pytest.approx(
            205.0 - (photo["x_mm"] + photo["w_mm"] + 4)
        )

    def test_un_nom_se_reduit_jusqua_la_moitie_avant_de_se_replier(
        self, spec: Json, fonts: FontSet
    ) -> None:
        renderer = HtmlRenderer(spec, {"header": {}, "sections": []}, fonts, photo=None)
        st = spec["styles"]["header.full_name"]
        text = "MOREAU-LEFEBVRE"
        natural = fonts.width_mm(text, st)
        field = renderer.fit_header_text(text, st, natural * 0.5)
        assert "white-space:normal" in field  # un champ ordinaire se replie sous 60 %
        # À 65 % de place, il se réduit encore au lieu de se replier (13/11 : un
        # poste visé replié recouvrait le paragraphe).
        assert "white-space:normal" not in renderer.fit_header_text(text, st, natural * 0.65)
        name = renderer.fit_header_text(text, st, natural * 0.6, name=True)
        assert "white-space:normal" not in name
        assert ";font-size:" in name
        # Jusqu'à 35 % (un nom composé de quinze lettres dans un modèle en 48 pt).
        assert "white-space:normal" not in renderer.fit_header_text(
            text, st, natural * 0.36, name=True
        )
        wrapped = renderer.fit_header_text(text, st, natural * 0.3, name=True)
        assert "white-space:normal" in wrapped
        assert f";font-size:{float(st['size_pt']) * 0.35:.2f}pt" in wrapped

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


class TestEtiquettesGrilleEtFlux:
    """Ce que le lot cv1/cv5/cv9 a appris au rendu : étiquette au pas, grille d'éléments,
    anneau de frise, espacement des mots, poste visé de flux, en-tête resserré."""

    @staticmethod
    def _section(spec: Json, kind: str) -> Json:
        return next(
            s
            for b in spec["bands"]
            for c in b["columns"]
            for s in c["sections"]
            if s["kind"] == kind
        )

    def test_une_etiquette_au_dessus_pose_la_valeur_au_pas_mesure(
        self, spec: Json, fonts: FontSet
    ) -> None:
        contact = self._section(spec, "contact")
        slot = contact["slots"][0]
        slot["label"] = {"text": "Phone", "inline": False, "pitch_mm": 4.9, "dx_mm": 0.0}
        spec["styles"]["contact.label"] = {
            **spec["styles"][f"contact.{slot['roles'][0]}"],
            "box_h_mm": 4.3,
        }
        renderer = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None)
        st0 = spec["styles"][style_key(spec["styles"], "contact", slot["roles"][0])]
        html = renderer.labelled_row(
            "contact", slot, slot["roles"][0], "0612", None, margin=0.0, width=40.0, st0=st0
        )
        # Haut en haut : le pas moins la boîte de l'étiquette (4,9 − 4,3), pas le pas entier.
        assert "margin-top:calc(0.60mm*var(--k))" in html
        assert "Phone" in html
        assert "0612" in html

    def test_deux_pastilles_sur_une_ligne_se_dessinent_toutes(self) -> None:
        row_decor = {
            "item_dates": [
                {
                    "kind": "rect",
                    "dx_mm": 0.0,
                    "h_mm": 2.8,
                    "w_mm": 2.8,
                    "fill": "#333333",
                    "center_offset_mm": 0.0,
                },
                {
                    "kind": "rect",
                    "dx_mm": 0.5,
                    "h_mm": 1.8,
                    "w_mm": 1.8,
                    "fill": "#FFFFFF",
                    "center_offset_mm": 0.0,
                },
            ]
        }
        done: set[str] = set()
        html = HtmlRenderer.row_ornament(row_decor, done, "item_dates", 6.0)
        assert html.count("<div") == 2
        assert "background:#333333" in html
        assert "background:#FFFFFF" in html
        assert HtmlRenderer.row_ornament(row_decor, done, "item_dates", 6.0) == ""

    def test_des_elements_en_colonnes_se_rangent_en_grille(
        self, spec: Json, fonts: FontSet
    ) -> None:
        exp = self._section(spec, "experiences")
        exp["item_columns"] = 2
        exp["item_pitch_mm"] = 62.0
        html = render(spec, from_cv(spec, CV), fonts)
        assert "display:flex;flex-wrap:wrap;align-items:flex-start;width:124.00mm" in html
        assert html.count("flex:none;width:62.00mm") >= 2

    def test_lespacement_des_mots_dun_style_passe_en_css(self, spec: Json, fonts: FontSet) -> None:
        spec["styles"]["summary.summary"]["word_spacing_mm"] = 0.49
        html = render(spec, from_cv(spec, CV), fonts)
        assert "word-spacing:0.49mm}" in html

    def test_lexemple_garde_les_retours_a_la_ligne_dun_intitule(
        self, cv_built: tuple, fonts: FontSet
    ) -> None:
        spec, sample, _ = copy.deepcopy(cv_built)
        example = HtmlRenderer(spec, from_sample(spec, sample), fonts, photo=None)
        html = example.text(
            "experiences.item_title", "Licence\nUniversité", margin=0, dx=0, width=50
        )
        assert 'white-space:pre-line">Licence\nUniversité' in html
        # Une personne n'a pas de retour à la ligne dans ses intitulés ; sans retour, rien.
        person = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None)
        assert "pre-line" not in person.text(
            "experiences.item_title", "Licence\nUniversité", margin=0, dx=0, width=50
        )
        assert "pre-line" not in example.text(
            "experiences.item_title", "Licence", margin=0, dx=0, width=50
        )

    def test_le_titre_dune_personne_se_replie_celui_de_lexemple_non(
        self, cv_built: tuple, fonts: FontSet
    ) -> None:
        spec, sample, _ = copy.deepcopy(cv_built)
        person = render(spec, from_cv(spec, CV), fonts)
        example = render(spec, from_sample(spec, sample), fonts)
        assert 'position:relative;white-space:nowrap">COMPETENCES' in example
        assert 'white-space:nowrap">Compétences' not in person
        assert ">Compétences" in person

    def test_une_barre_de_niveau_de_lexemple_ne_se_dessine_pas_pour_une_personne(
        self, cv_built: tuple, fonts: FontSet
    ) -> None:
        spec, sample, _ = copy.deepcopy(cv_built)
        skills = self._section(spec, "skills")
        skills["decorations"] = [
            {
                "kind": "rect",
                "anchor": "section",
                "dx_mm": 20.0,
                "dy_mm": 8.0,
                "w_mm": 13.0,
                "h_mm": 1.9,
                "fill": "#ABCDEF",
                "stroke": None,
                "stroke_pt": 0.0,
                "sample_only": True,
            },
            {
                "kind": "rect",
                "anchor": "item",
                "dx_mm": 20.0,
                "dy_mm": 1.0,
                "w_mm": 13.0,
                "h_mm": 1.9,
                "fill": "#FEDCBA",
                "stroke": None,
                "stroke_pt": 0.0,
                "sample_only": True,
            },
        ]
        assert "#ABCDEF" in render(spec, from_sample(spec, sample), fonts)
        person = render(spec, from_cv(spec, CV), fonts)
        assert "#ABCDEF" not in person
        assert "#FEDCBA" not in person

    def test_une_coordonnee_trop_longue_dans_sa_colonne_se_reduit(
        self, spec: Json, fonts: FontSet
    ) -> None:
        cv = copy.deepcopy(CV)
        cv["contact"]["email"] = "une.adresse.vraiment.tres.longue@exemple-de-domaine.org"
        html = render(spec, from_cv(spec, cv), fonts)
        assert (
            re.search(r"font-size:[\d.]+pt[^>]*>une\.adresse", html) or "white-space:normal" in html
        )

    def test_le_poste_vise_de_flux_ouvre_le_flux_pour_une_personne_seulement(
        self, cv_built: tuple, fonts: FontSet
    ) -> None:
        spec, sample, _ = copy.deepcopy(cv_built)
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        headline.update(flow=True, gap_mm=3.0)
        person = render(spec, from_cv(spec, CV), fonts)
        assert 'class="flow"' in person
        assert 'margin-bottom:3.00mm">Ingénieure &lt;data&gt;</div>' in person
        assert (
            "position:absolute;left:" + str(headline["x_mm"])
            not in person.split('class="flow"')[0].split("Ingénieure")[-1]
        )
        example = render(spec, from_sample(spec, sample), fonts)
        assert "margin-bottom:3.00mm" not in example
        view = from_cv(spec, {**CV, "identity": {"first_name": "A", "last_name": "B"}})
        assert HtmlRenderer(spec, view, fonts, photo=None).flow_headline() == ""

    def test_les_coordonnees_se_resserrent_quand_le_site_entre_dans_la_rubrique(
        self, spec: Json, fonts: FontSet
    ) -> None:
        elements = spec["header"]["elements"]
        phone = next(e for e in elements if e["field"] == "phone")
        website = {**phone, "field": "website", "y_mm": 400.0, "synthesized": True}
        elements.append(website)
        view = from_cv(spec, CV)
        renderer = HtmlRenderer(spec, view, fonts, photo=None)
        tight = renderer.tight_contact(elements)
        assert set(tight) == {"phone", "website"}
        assert tight["phone"] == phone["y_mm"]
        assert tight["website"] < 400.0
        assert tight["website"] >= tight["phone"] + lh(spec["styles"][phone["style"]])
        html = renderer.render()
        assert f"top:{tight['website'] - half(spec['styles'][phone['style']]):.2f}mm" in html
        # Sans site synthétisé, ou quand tout tient : rien ne bouge.
        website["synthesized"] = False
        assert renderer.tight_contact(elements) == {}
        website.update(synthesized=True, y_mm=phone["y_mm"] + 1.0)
        assert renderer.tight_contact(elements) == {}
        # La réserve d'en-tête au-dessus de la première rubrique de la colonne.
        column = spec["bands"][0]["columns"][0]
        reserve = renderer.header_reserve(column, float(spec["bands"][0]["y_sample"]))
        assert reserve >= 0.0
        assert renderer.column_top_under(-500.0) == spec["first_band_top_mm"]

    def test_un_filet_vertical_borne_un_element_dentete(self, spec: Json, fonts: FontSet) -> None:
        name = spec["header"]["elements"][0]
        st = spec["styles"][name["style"]]
        renderer = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None)
        free = renderer.header_limit(name, st)
        spec["page_decorations"].append(
            {
                "kind": "vline",
                "anchor": "page",
                "x_mm": name["x_mm"] + 30.0,
                "y_mm": 0.0,
                "h_mm": 297.0,
                "w_mm": 0.0,
                "stroke": "#000000",
            }
        )
        assert renderer.header_limit(name, st) == pytest.approx(min(free, 28.0))
        spec["page_decorations"][-1].update(x_mm=name["x_mm"] - 10.0)
        assert renderer.header_limit(name, st) == pytest.approx(free)
        spec["page_decorations"][-1].update(y_mm=250.0, h_mm=10.0, x_mm=name["x_mm"] + 30.0)
        assert renderer.header_limit(name, st) == pytest.approx(free)
        spec["page_decorations"][-1] = {
            "kind": "vline",
            "anchor": "flow",
            "x_mm": name["x_mm"] + 30.0,
            "dy_mm": -500.0,
            "h_mm": 900.0,
            "w_mm": 0.0,
            "stroke": "#000000",
        }
        assert renderer.header_limit(name, st) == pytest.approx(min(free, 28.0))


def test_un_trace_rasterise_se_rend_en_image_et_pas_pour_une_personne_sil_entoure_une_image(
    cv_built: tuple, fonts: FontSet
) -> None:
    spec, sample, _ = copy.deepcopy(cv_built)
    spec["page_decorations"].append(
        {
            "kind": "curve",
            "anchor": "page",
            "x_mm": 150.0,
            "y_mm": 240.0,
            "w_mm": 30.0,
            "h_mm": 30.0,
            "fill": "#D8A4B5",
            "stroke": None,
            "asset": "shape_9.png",
            "sample_only": True,
        }
    )
    example = render(spec, from_sample(spec, sample), fonts)
    assert (
        '<img src="shape_9.png" alt="" style="position:absolute;left:150.0mm;top:240.0mm;width:30.0mm;height:30.0mm">'
        in example
    )
    assert "shape_9.png" not in render(spec, from_cv(spec, CV), fonts)
    del spec["page_decorations"][-1]["sample_only"]
    assert "shape_9.png" in render(spec, from_cv(spec, CV), fonts)


def test_lexemple_rend_un_element_a_son_propre_corps(cv_built: tuple, fonts: FontSet) -> None:
    spec, sample, _ = copy.deepcopy(cv_built)
    sample["experiences"][0]["sizes"] = {"item_subtitle": 6.5}
    html = render(spec, from_sample(spec, sample), fonts)
    assert ";font-size:6.5pt" in html
    view = from_cv(spec, CV)
    view["sections"][2]["items"][0]["sizes"] = {"item_subtitle": 6.5}
    assert ";font-size:6.5pt" not in render(spec, view, fonts)


def test_la_reserve_dentete_ignore_un_pied_de_page(spec: Json, fonts: FontSet) -> None:
    column = spec["bands"][0]["columns"][0]
    band_top = float(spec["bands"][0]["y_sample"])
    spec["header"]["elements"].append(
        {
            **spec["header"]["elements"][0],
            "field": "website",
            "y_mm": 280.0,
            "x_mm": column["x_mm"] + 1.0,
        }
    )
    cv = copy.deepcopy(CV)
    renderer = HtmlRenderer(spec, from_cv(spec, cv), fonts, photo=None)
    assert renderer.header_reserve(column, band_top) < 100.0


class TestVraiCvEtEnTete:
    """cv2, meridian, cv5 : ce que le rendu d'une personne a appris de l'en-tête en absolu."""

    def test_la_premiere_bande_garde_la_hauteur_que_lentete_exige(
        self, spec: Json, fonts: FontSet
    ) -> None:
        band = spec["bands"][0]
        renderer = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None)
        assert renderer.band_floor(0) == 0.0  # rien de l'en-tête ne descend dans la bande
        name = spec["header"]["elements"][0]
        name["y_mm"] = float(band["y_sample"]) + 20.0  # le nom descend dans la bande
        floor = renderer.band_floor(0)
        assert floor > 0.0
        html = renderer.render()
        assert f"min-height:{floor:.2f}mm" in html
        # Un pied de page (plus bas que la bande suivante) ne compte pas.
        name["y_mm"] = float(spec["bands"][1]["y_sample"]) + 5.0
        assert renderer.band_floor(0) == 0.0

    def test_une_rubrique_reserve_la_place_de_lentete_dans_son_css(
        self, spec: Json, fonts: FontSet
    ) -> None:
        renderer = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None)
        s = spec["bands"][0]["columns"][0]["sections"][0]
        sec = {"kind": s["kind"], "title": "T", "items": []}
        html = renderer.section(s, sec, 60.0, reserve_mm=12.0)
        assert "margin-top:max(calc(" in html
        # Un texte d'en-tête au-dessus de la rubrique, dans sa colonne, fait une réserve.
        column = spec["bands"][0]["columns"][0]
        band_top = float(spec["bands"][0]["y_sample"])
        column["sections"][0]["margin_top_mm"] = 30.0
        name = spec["header"]["elements"][0]
        name.update(x_mm=column["x_mm"] + 1.0, y_mm=band_top + 2.0)
        assert renderer.header_reserve(column, band_top) > 0.0
        name["y_mm"] = band_top - 50.0  # finit au-dessus de la bande : rien
        assert renderer.header_reserve(column, band_top) == 0.0

    def test_une_colonne_de_la_premiere_bande_borne_un_champ_dentete(
        self, spec: Json, fonts: FontSet
    ) -> None:
        renderer = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None)
        name = spec["header"]["elements"][0]
        st = spec["styles"][name["style"]]
        band = spec["bands"][0]
        free = renderer.header_limit(name, st)
        name["y_mm"] = float(band["y_sample"]) + 1.0  # à la hauteur de la bande
        column = band["columns"][-1]
        assert (
            renderer.header_limit(name, st) <= min(free, column["x_mm"] - 4.0 - name["x_mm"]) + 0.01
        )
        name["x_mm"] = column["x_mm"] + column["w_mm"] + 20.0  # à droite de toutes les colonnes
        assert renderer.header_limit(name, st) > 0.0

    def test_le_titre_dune_personne_se_resserre_sans_se_couper(
        self, spec: Json, fonts: FontSet
    ) -> None:
        view = from_cv(spec, CV)
        for section in view["sections"]:
            if section["kind"] == "skills":
                section["title"] = "Compétences techniques et humaines"
        html = render(spec, view, fonts)
        assert "overflow-wrap:normal" in html
        assert re.search(r"overflow-wrap:normal;letter-spacing:[\d.]+pt;font-size:[\d.]+pt", html)
