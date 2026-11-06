"""Le pied Karria des documents : une ligne discrète, un lien, sur le PDF comme sur le Word."""

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
    letter_spec_testa,
)
from karria.modules.documents.domain.letter import from_letter
from karria.modules.documents.domain.view import from_cv
from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.footer import (
    FOOTER_SIZE_PT,
    footer_html,
    footer_text,
    footer_url,
)
from karria.modules.documents.infrastructure.html_renderer import HtmlRenderer, estimate_pages
from karria.modules.documents.infrastructure.letter_renderer import (
    LetterDocxRenderer,
    LetterHtmlRenderer,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.modules.documents.infrastructure.spec_builder import SpecBuilder

pytestmark = pytest.mark.unit

Json = dict[str, Any]

CV: Json = {
    "identity": {"first_name": "Ada", "last_name": "Lovelace", "headline": "Ingénieure"},
    "contact": {"email": "ada@example.org", "city": "Londres"},
    "summary": "Je code.",
    "experiences": [{"position": "Analyste", "company": "Babbage", "start_date": "2020-01"}],
    "educations": [{"degree": "Mathématiques", "school": "Maison", "start_date": "2015"}],
}

LETTER: Json = {
    "recipient": {"company": "Acme", "city": "Lyon"},
    "subject": "Candidature",
    "paragraphs": [{"kind": "libre", "text": "Bonjour."}],
    "closing": "Cordialement.",
    "sender": {"first_name": "Awa", "last_name": "Diallo", "headline": None},
}


@pytest.fixture(scope="module")
def built() -> tuple:
    with PdfDocument(cv_template_pdf()) as doc:
        page = doc.read()
        labels = labels_for(page.lines, CV_ROLES)
        labels["photo_shape"] = None
        return SpecBuilder(doc, page, labels, cv_font_set(), SERVED).build()


@pytest.fixture
def spec(built: tuple) -> Json:
    return copy.deepcopy(built[0])


@pytest.fixture(scope="module")
def fonts() -> FontSet:
    return cv_font_set()


def unzip(docx: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        return {n: z.read(n) for n in z.namelist()}


class TestLaLigne:
    def test_le_texte_et_le_lien_disent_d_ou_vient_le_document(self) -> None:
        assert footer_text("cv") == "CV réalisé avec Karria · karria.fr/app"
        assert footer_text("letter") == "Lettre réalisée avec Karria · karria.fr/app"
        assert footer_url("cv") == "https://karria.fr/app?utm_source=document&utm_medium=cv"
        assert footer_url("letter").endswith("utm_medium=letter")

    def test_en_html_elle_est_en_absolu_au_bas_de_la_feuille_petite_et_grise(self) -> None:
        html = footer_html("cv")
        assert "position:absolute" in html
        assert "bottom:3.0mm" in html
        assert f"font:{FOOTER_SIZE_PT}pt" in html
        assert FOOTER_SIZE_PT <= 7
        assert 'href="https://karria.fr/app?utm_source=document&amp;utm_medium=cv"' in html
        assert "data-chemin" not in html
        assert 'class="t"' not in html


class TestSurLeCv:
    def test_le_html_porte_la_ligne_sans_changer_la_pagination(
        self, spec: Json, fonts: FontSet
    ) -> None:
        view = from_cv(spec, CV)
        html = HtmlRenderer(spec, view, fonts, photo=None).render()
        assert "CV réalisé avec Karria" in html
        assert html.count('class="karria-pied"') == 1
        # La ligne est dans `.page`, après le flux : elle ne déplace rien.
        assert (
            html.index('class="flow"') < html.index('class="karria-pied"') < html.index("<script>")
        )
        assert estimate_pages(spec, view, fonts) == 1

    def test_l_atelier_ancre_la_voit_aussi_mais_ne_l_ancre_pas(
        self, spec: Json, fonts: FontSet
    ) -> None:
        html = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None, anchored=True).render()
        pied = re.search(r'<div class="karria-pied".*?</div>', html)
        assert pied is not None
        assert "data-chemin" not in pied.group(0)

    def test_le_docx_porte_la_ligne_en_pied_de_section_avec_le_lien(
        self, spec: Json, fonts: FontSet
    ) -> None:
        data = DocxRenderer(spec, from_cv(spec, CV), fonts, photo=None).render({})
        doc = Document(io.BytesIO(data))
        pied = doc.sections[0].footer
        assert pied.paragraphs[0].text == "CV réalisé avec Karria · karria.fr/app"
        xml = unzip(data)
        rels = next(v for k, v in xml.items() if k.startswith("word/_rels/footer"))
        assert b"https://karria.fr/app?utm_source=document&amp;utm_medium=cv" in rels
        assert b'<w:sz w:val="14"/>' in next(
            v for k, v in xml.items() if k.startswith("word/footer")
        )


class TestSurLaLettre:
    def test_le_html_et_le_docx_de_la_lettre_disent_lettre(self, fonts: FontSet) -> None:
        spec = letter_spec_testa()
        view = from_letter(spec, LETTER, today=date(2026, 11, 5))
        html = LetterHtmlRenderer(spec, view, fonts, logo=None).render()
        assert "Lettre réalisée avec Karria" in html
        assert html.index('class="sheet"') < html.index('class="karria-pied"')
        data = LetterDocxRenderer(spec, view, fonts, logo=None).render()
        doc = Document(io.BytesIO(data))
        assert (
            doc.sections[0].footer.paragraphs[0].text
            == "Lettre réalisée avec Karria · karria.fr/app"
        )
