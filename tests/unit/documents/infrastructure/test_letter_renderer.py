"""La lettre depuis sa spécification de lettre : blocs d'en-tête, corps, HTML ancré, DOCX (ADR-0036)."""

from __future__ import annotations

import copy
import io
import re
import zipfile
from datetime import date
from typing import Any

import pytest
from docx import Document

from documents.infrastructure.fakes import cv_font_set, letter_spec_testa, png
from karria.modules.documents.domain.letter import from_letter
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.letter_renderer import (
    LetterDocxRenderer,
    LetterHtmlRenderer,
    block_texts,
    half,
    header_path,
    header_value,
)

pytestmark = pytest.mark.unit

Json = dict[str, Any]

LETTER: Json = {
    "recipient": {"company": "Acme & Fils", "city": "Lyon"},
    "subject": "Candidature au poste de vendeuse",
    "paragraphs": [
        {"kind": "interet_entreprise", "text": "Acme m'attire <vraiment>."},
        {"kind": "competences", "text": "Je sais vendre."},
    ],
    "closing": "Cordialement.",
    "sender": {
        "first_name": "Awa",
        "last_name": "Diallo",
        "headline": "Vendeuse",
        "email": "awa@example.com",
        "phone": "06 12",
        "city": "Montreuil",
    },
}


@pytest.fixture
def spec() -> Json:
    return letter_spec_testa()


@pytest.fixture(scope="module")
def fonts() -> FontSet:
    return cv_font_set()


@pytest.fixture
def view(spec: Json) -> Json:
    return from_letter(spec, LETTER, today=date(2026, 11, 5))


class TestLecturesDeLaVue:
    def test_un_champ_d_en_tete_se_lit_dans_l_expediteur_le_destinataire_ou_la_date(
        self, view: Json
    ) -> None:
        assert header_value(view, "full_name") == "Awa Diallo"
        assert header_value(view, "company") == "Acme & Fils"
        assert header_value(view, "date_line") == "Montreuil, le 5 novembre 2026"
        assert header_value(view, "website") == ""
        assert header_path(view, "headline") == "sender.headline"
        assert header_path(view, "city") == "recipient.city"
        assert header_path(view, "date_line") is None

    def test_les_blocs_du_corps_rendent_leurs_textes_et_chemins(self, view: Json) -> None:
        subject = block_texts(view, {"role": "subject", "prefix": "OBJET - "})
        assert subject == [("OBJET - Candidature au poste de vendeuse", "subject")]
        assert block_texts(view, {"role": "subject"})[0][0].startswith("Objet\u00a0: ")
        assert block_texts(view, {"role": "paragraph"}) == [
            ("Acme m'attire <vraiment>.", "paragraphs.0.text"),
            ("Je sais vendre.", "paragraphs.1.text"),
        ]
        assert block_texts(view, {"role": "signature"}) == [("Awa Diallo", "sender.full_name")]
        assert block_texts({**view, "subject": ""}, {"role": "subject"}) == []

    def test_le_demi_blanc_suit_le_pas_donne(self) -> None:
        st = {"box_h_mm": 4.0, "pitch_mm": 6.0}
        assert half(st) == 1.0
        assert half(st, 10.0) == 3.0
        assert half({"box_h_mm": 4.0, "pitch_mm": None}) == 0.0


class TestHtml:
    def test_la_lettre_porte_ses_textes_echappes_ses_ancres_et_ses_blocs(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        html = LetterHtmlRenderer(spec, view, fonts, logo=None, anchored=True).render()
        assert "Acme &amp; Fils" in html
        assert "Acme m&#x27;attire &lt;vraiment&gt;." in html
        assert "Objet\u00a0: Candidature au poste de vendeuse" in html
        assert "Montreuil, le 5 novembre 2026" in html
        found = re.findall(r'data-chemin="([^"]+)"', html)
        assert {"subject", "paragraphs.0.text", "paragraphs.1.text", "closing"} <= set(found)
        assert "sender.full_name" in found
        assert "recipient.company" in found
        # Trois blocs d'en-tête : l'expéditeur à gauche, le destinataire et la date à droite.
        assert html.count('class="hb"') == 3
        assert "right:25.00mm;text-align:right" in html
        assert "text-align:justify" in html
        assert "<img" not in html
        assert "window.karriaFit=" in html
        assert "@font-face{font-family:'Testa'" in html

    def test_une_ligne_vide_disparait_sans_blanc(self, spec: Json, fonts: FontSet) -> None:
        letter = copy.deepcopy(LETTER)
        letter["sender"].update(headline=None, phone=None)
        view = from_letter(spec, letter, today=date(2026, 11, 5))
        html = LetterHtmlRenderer(spec, view, fonts, logo=None, anchored=True).render()
        assert 'class="t l-header-headline"' not in html
        assert 'class="t l-header-phone"' not in html
        assert 'class="t l-header-email"' in html

    def test_un_bloc_sans_aucun_texte_n_est_pas_rendu(self, spec: Json, fonts: FontSet) -> None:
        letter = {**LETTER, "recipient": {"company": "", "city": ""}}
        view = from_letter(spec, letter, today=date(2026, 11, 5))
        html = LetterHtmlRenderer(spec, view, fonts, logo=None).render()
        assert html.count('class="hb"') == 2

    def test_un_bloc_centre_et_un_texte_trop_long_sont_ajustes(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        spec["header"]["blocks"][2] = {
            "anchor": "center",
            "cx_mm": 105.0,
            "y_mm": 80.0,
            "max_w_mm": 30.0,
            "elements": [{"field": "date_line", "style": "date_line", "pitch_mm": 5.4}],
        }
        html = LetterHtmlRenderer(spec, view, fonts, logo=None).render()
        assert "left:105.00mm;transform:translateX(-50%);text-align:center" in html
        assert "white-space:normal;width:30.00mm" in html

    def test_sans_ancre_pour_l_impression_et_avec_le_logo_au_dessus_du_destinataire(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        html = LetterHtmlRenderer(
            spec, view, fonts, logo="logo.png", url_for=lambda n: f"u/{n}"
        ).render()
        assert "data-chemin" not in html
        assert 'src="u/logo.png"' in html
        assert "top:45.00mm;right:25.00mm" in html
        assert "src:url('u/Testa-400.ttf')" in html
        assert "border-top:1px dashed" not in html

    def test_sans_bloc_droit_le_logo_va_en_haut_a_droite_de_la_colonne(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        spec["header"]["blocks"] = [spec["header"]["blocks"][0]]
        html = LetterHtmlRenderer(spec, view, fonts, logo="logo.png").render()
        assert "top:10.00mm;right:25.00mm" in html

    def test_les_decorations_de_page_et_d_en_tete_sont_posees(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        spec["page_decorations"] = [
            {"kind": "hline", "x_mm": 25, "y_mm": 55, "w_mm": 160, "h_mm": 0, "stroke": "#999999"}
        ]
        spec["header"]["decorations"] = [
            {"kind": "rect", "x_mm": 0, "y_mm": 0, "w_mm": 210, "h_mm": 8, "fill": "#1A5276"}
        ]
        html = LetterHtmlRenderer(spec, view, fonts, logo=None).render()
        assert "border-top:0.75pt solid #999999" in html
        assert "background:#1A5276" in html


class TestDocx:
    def test_le_docx_porte_le_texte_les_polices_et_le_logo(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        data = LetterDocxRenderer(spec, view, fonts, logo=png(), k=0.9, f=0.96).render()
        doc = Document(io.BytesIO(data))
        texts = [p.text for p in doc.paragraphs]
        cells = [p.text for p in doc.tables[0].rows[0].cells[0].paragraphs] + [
            p.text for p in doc.tables[0].rows[0].cells[1].paragraphs
        ]
        assert "Awa Diallo" in cells
        assert "Vendeuse" in cells
        assert "Acme & Fils" in cells
        assert "Montreuil, le 5 novembre 2026" in cells
        assert "Objet\u00a0: Candidature au poste de vendeuse" in texts
        assert "Je sais vendre." in texts
        assert texts[-1] == "Awa Diallo"
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(n.startswith("word/fonts/") and n.endswith(".odttf") for n in names)
        assert any(n.startswith("word/media/") for n in names)
        section = doc.sections[0]
        assert round(section.left_margin.mm) == 25
        assert round(section.right_margin.mm) == 25
        assert doc.core_properties.author == "Awa Diallo"

    def test_sans_logo_aucune_image_et_les_lignes_sont_exactes(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        data = LetterDocxRenderer(spec, view, fonts, logo=None).render()
        assert not any(
            n.startswith("word/media/") for n in zipfile.ZipFile(io.BytesIO(data)).namelist()
        )
        doc = Document(io.BytesIO(data))
        body = [p for p in doc.paragraphs if p.text]
        assert all(abs(p.paragraph_format.line_spacing.mm - 5.4) < 0.05 for p in body)
        # Le second paragraphe du corps est séparé du premier d'une ligne de blanc.
        second = next(p for p in body if p.text == "Je sais vendre.")
        assert second.paragraph_format.space_before is not None
        assert 3.0 < second.paragraph_format.space_before.mm < 6.0

    def test_une_ligne_de_base_trop_haute_raccourcit_la_ligne_au_lieu_de_descendre(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        renderer = LetterDocxRenderer(spec, view, fonts, logo=None)
        ascent, descent, box = renderer.metrics("header.full_name")
        assert box > ascent > descent > 0
        # Excédent au-dessus : la ligne de base tombe à L − D.
        assert renderer.baseline_offset(box + 2.0, ascent, descent) == pytest.approx(
            box + 2.0 - descent
        )
        # Ligne plus courte que la boîte : à l'échelle.
        assert renderer.baseline_offset(box / 2, ascent, descent) == pytest.approx(ascent / 2)
        line, space, bottom = renderer.place(30.0, 10.0, cursor=5.0, key="header.full_name")
        assert line == 10.0
        assert space == pytest.approx(30.0 - renderer.baseline_offset(10.0, ascent, descent) - 5.0)
        assert bottom == pytest.approx(
            30.0 - renderer.baseline_offset(10.0, ascent, descent) + 10.0
        )
        # Trop haut : la ligne se raccourcit, le blanc vaut zéro.
        line2, space2, _ = renderer.place(30.0, 10.0, cursor=28.0, key="header.full_name")
        assert space2 == 0.0
        assert line2 < 10.0
        # Sans police servie : des métriques par défaut.
        a, d, b = LetterDocxRenderer(spec, view, FontSet([]), logo=None).metrics("paragraph")
        assert (a, d) == (pytest.approx(0.8 * b), pytest.approx(0.2 * b))

    def test_le_retour_a_la_ligne_estime_compte_les_lignes(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        renderer = LetterDocxRenderer(spec, view, fonts, logo=None)
        assert renderer.wrap("", "paragraph", 160.0) == 1
        assert renderer.wrap("court", "paragraph", 160.0) == 1
        assert renderer.wrap(" ".join(["mot"] * 120), "paragraph", 160.0) > 1

    def test_sans_aucun_bloc_d_en_tete_la_marge_haute_est_celle_du_corps(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        spec["header"]["blocks"] = []
        data = LetterDocxRenderer(spec, view, fonts, logo=None).render()
        doc = Document(io.BytesIO(data))
        assert round(doc.sections[0].top_margin.mm) == 86


class TestChampsVides:
    def test_un_objet_vide_et_un_destinataire_vide_ne_laissent_aucun_blanc(
        self, spec: Json, fonts: FontSet
    ) -> None:
        letter = {**LETTER, "subject": "", "recipient": {"company": "", "city": ""}}
        view = from_letter(spec, letter, today=date(2026, 11, 5))
        html = LetterHtmlRenderer(spec, view, fonts, logo=None).render()
        assert "Objet" not in html
        data = LetterDocxRenderer(spec, view, fonts, logo=None).render()
        doc = Document(io.BytesIO(data))
        texts = [p.text for p in doc.paragraphs if p.text]
        assert texts[0] == "Madame, Monsieur,"
        cells = doc.tables[0].rows[0].cells
        assert [p.text for p in cells[1].paragraphs if p.text] == ["Montreuil, le 5 novembre 2026"]

    def test_un_interlettrage_de_style_passe_dans_le_docx(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        spec["styles"]["header.full_name"]["letter_spacing_pt"] = 1.5
        data = LetterDocxRenderer(spec, view, fonts, logo=None).render()
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
        assert b'<w:spacing w:val="30"/>' in xml
