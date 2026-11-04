"""La lettre dans la grammaire du modèle : cadre, styles, HTML ancré, DOCX (ADR-0030)."""

from __future__ import annotations

import copy
import io
import re
import zipfile
from datetime import date
from typing import Any

import pytest
from docx import Document

from documents.infrastructure.fakes import (
    CV_ROLES,
    SERVED,
    cv_font_set,
    cv_template_pdf,
    labels_for,
    png,
)
from karria.modules.documents.domain.letter import from_letter
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.letter_renderer import (
    BODY_MAX_PT,
    BODY_MIN_PT,
    LetterDocxRenderer,
    LetterHtmlRenderer,
    letter_frame,
    letter_styles,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.modules.documents.infrastructure.spec_builder import SpecBuilder

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


@pytest.fixture(scope="module")
def built() -> tuple:
    with PdfDocument(cv_template_pdf()) as doc:
        page = doc.read()
        labels = labels_for(page.lines, CV_ROLES)
        labels["photo_shape"] = "circle"
        return SpecBuilder(doc, page, labels, cv_font_set(), SERVED).build()


@pytest.fixture
def spec(built: tuple) -> Json:
    return copy.deepcopy(built[0])


@pytest.fixture(scope="module")
def fonts() -> FontSet:
    return cv_font_set()


@pytest.fixture
def view(spec: Json) -> Json:
    return from_letter(spec, LETTER, today=date(2026, 11, 5))


class TestCadreEtStyles:
    def test_les_marges_exterieures_du_modele_font_celles_de_la_lettre(self, spec: Json) -> None:
        left, width, right = letter_frame(spec)
        assert left == 15.0  # la première colonne du modèle de test
        assert right == 25.0  # la droite réelle (43 mm) est bornée
        assert width == 210.0 - left - right

    def test_sans_colonne_des_marges_classiques_et_un_plancher(self, spec: Json) -> None:
        assert letter_frame({**spec, "bands": []}) == (20.0, 170.0, 20.0)
        tight = {**spec, "bands": [{"columns": [{"x_mm": 4.0, "w_mm": 200.0, "sections": []}]}]}
        assert letter_frame(tight) == (12.0, 186.0, 12.0)

    def test_le_corps_est_borne_et_l_objet_prend_la_couleur_d_accent(self, spec: Json) -> None:
        styles = letter_styles(spec)
        assert BODY_MIN_PT <= styles["body"]["size_pt"] <= BODY_MAX_PT
        assert styles["subject"]["color"] == spec["styles"]["section_title"]["color"]
        assert styles["subject"]["uppercase"] is False
        assert styles["name"]["family"] == spec["styles"]["header.full_name"]["family"]
        assert styles["signature"]["size_pt"] <= 13.0


class TestHtml:
    def test_la_lettre_porte_ses_textes_echappes_et_ses_ancres(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        html = LetterHtmlRenderer(spec, view, fonts, logo=None, anchored=True).render()
        assert "Acme &amp; Fils" in html
        assert "Acme m&#x27;attire &lt;vraiment&gt;." in html
        assert "Objet : Candidature au poste de vendeuse" in html
        assert "Montreuil, le 5 novembre 2026" in html
        found = re.findall(r'data-chemin="([^"]+)"', html)
        assert {"subject", "paragraphs.0.text", "paragraphs.1.text", "closing"} <= set(found)
        assert "sender.full_name" in found
        assert "recipient.company" in found
        assert "<img" not in html
        assert "window.karriaFit=" in html

    def test_sans_ancre_pour_l_impression_et_avec_le_logo(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        html = LetterHtmlRenderer(
            spec, view, fonts, logo="logo.png", url_for=lambda n: f"u/{n}"
        ).render()
        assert "data-chemin" not in html
        assert 'src="u/logo.png"' in html
        assert "src:url('u/Testa-400.ttf')" in html
        assert "border-top:1px dashed" not in html


class TestDocx:
    def test_le_docx_porte_le_texte_les_polices_et_le_logo(
        self, spec: Json, view: Json, fonts: FontSet
    ) -> None:
        data = LetterDocxRenderer(spec, view, fonts, logo=png(), k=0.9, f=0.96).render()
        doc = Document(io.BytesIO(data))
        texts = [p.text for p in doc.paragraphs]
        assert "Awa Diallo" in texts
        assert "Objet : Candidature au poste de vendeuse" in texts
        assert "Je sais vendre." in texts
        assert texts[-1] == "Awa Diallo"
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(n.startswith("word/fonts/") and n.endswith(".odttf") for n in names)
        assert any(n.startswith("word/media/") for n in names)
        section = doc.sections[0]
        assert round(section.left_margin.mm) == 15
        assert doc.core_properties.author == "Awa Diallo"

    def test_sans_logo_aucune_image(self, spec: Json, view: Json, fonts: FontSet) -> None:
        data = LetterDocxRenderer(spec, view, fonts, logo=None).render()
        assert not any(
            n.startswith("word/media/") for n in zipfile.ZipFile(io.BytesIO(data)).namelist()
        )
