"""Le HTML de l'atelier : ancres `data-chemin`, résolveur d'adresses, pages (ADR-0029)."""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest

from documents.infrastructure.fakes import (
    CV_ROLES,
    SERVED,
    cv_font_set,
    cv_template_pdf,
    labels_for,
)
from karria.modules.documents.domain.view import from_cv, from_sample
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.html_renderer import (
    HtmlRenderer,
    anchor,
    estimate_pages,
    fit_script,
    page_limit_mm,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.modules.documents.infrastructure.spec_builder import SpecBuilder

pytestmark = pytest.mark.unit

Json = dict[str, Any]

CV: Json = {
    "identity": {"first_name": "Ada", "last_name": "Lovelace", "headline": "Ingénieure <data>"},
    "contact": {"phone": "06 00 00 00 00", "email": "ada@example.org", "city": "Londres"},
    "summary": "Je code & j'aime ça.",
    "experiences": [
        {
            "position": "Analyste",
            "company": "Babbage & Co",
            "start_date": "2020-01",
            "end_date": "2023-06",
            "missions": ["Machine analytique", "Notes célèbres"],
        }
    ],
    "educations": [{"degree": "Mathématiques", "school": "Maison", "start_date": "2015"}],
    "skills": [{"name": "Python", "category": "Technique"}, {"name": "Écoute"}],
    "languages": [{"language": "Français", "level": "Natif"}],
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


def chemins(html: str) -> list[str]:
    return re.findall(r'data-chemin="([^"]+)"', html)


class TestAncres:
    def test_chaque_texte_du_cv_porte_son_chemin(self, spec: Json, fonts: FontSet) -> None:
        html = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None, anchored=True).render()
        found = chemins(html)
        assert "identity.headline" in found
        assert "identity.full_name" in found
        assert "experiences.0.missions.0" in found
        assert "experiences.0.missions.1" in found
        assert "experiences.0.dates" in found
        assert "skills.0" in found
        assert "skills.1" in found
        assert "languages.0" in found
        assert "summary" in found
        assert "educations.0.degree" in found

    def test_un_champ_compose_sur_une_ligne_ancre_chaque_morceau(
        self, spec: Json, fonts: FontSet
    ) -> None:
        html = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None, anchored=True).render()
        spans = re.findall(r'<span class="s-experiences-([a-z_]+)" data-chemin="([^"]+)"', html)
        # Le modèle de test réunit des champs sur une ligne : chaque morceau garde son chemin.
        assert ("item_title", "experiences.0.position") in spans
        assert ("item_dates", "experiences.0.dates") in spans

    def test_le_rendu_imprime_n_a_aucune_ancre(self, spec: Json, fonts: FontSet) -> None:
        html = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo=None).render()
        assert chemins(html) == []
        assert "border-top:1px dashed" not in html

    def test_l_exemple_du_modele_n_en_a_pas_non_plus(self, spec: Json, fonts: FontSet) -> None:
        sample = from_sample(spec, {"identity": {"first_name": "J", "last_name": "D"}})
        html = HtmlRenderer(spec, sample, fonts, photo=None, anchored=True).render()
        assert chemins(html) == []

    def test_le_chemin_est_echappe(self) -> None:
        assert anchor('a"b') == ' data-chemin="a&quot;b"'
        assert anchor(None) == ""


class TestAdresses:
    def test_le_resolveur_traduit_polices_pictogrammes_et_photo(
        self, spec: Json, fonts: FontSet
    ) -> None:
        urls = {
            f.file_name: f"https://storage.karria.fr/assets/fonts/{f.file_name}"
            for f in fonts.files
        }
        urls["photo"] = "https://storage.karria.fr/media/photo.jpg?sig=1"
        html = HtmlRenderer(
            spec, from_cv(spec, CV), fonts, photo="photo", url_for=lambda n: urls.get(n, n)
        ).render()
        assert "src:url('https://storage.karria.fr/assets/fonts/Testa-400.ttf')" in html
        assert 'src="https://storage.karria.fr/media/photo.jpg?sig=1"' in html
        assert "src:url('Testa-400.ttf')" not in html

    def test_par_defaut_les_fichiers_joints_gardent_leur_nom(
        self, spec: Json, fonts: FontSet
    ) -> None:
        html = HtmlRenderer(spec, from_cv(spec, CV), fonts, photo="photo.png").render()
        assert "src:url('Testa-400.ttf')" in html
        assert 'src="photo.png"' in html


class TestPages:
    def test_le_script_de_l_atelier_pose_des_filets_et_karria_fit(self, spec: Json) -> None:
        live = fit_script(spec, live=True)
        printed = fit_script(spec, live=False)
        assert "window.karriaFit=" in live
        assert "border-top:1px dashed" in live
        assert "window.karriaFit=" in printed
        assert "border-top:1px dashed" not in printed
        assert f"lim={page_limit_mm(spec):.2f}*mm" in printed

    def test_un_cv_court_tient_sur_une_page_un_cv_long_non(
        self, spec: Json, fonts: FontSet
    ) -> None:
        assert estimate_pages(spec, from_cv(spec, CV), fonts) == 1
        long_cv = copy.deepcopy(CV)
        long_cv["experiences"] = [
            {
                "position": f"Poste {i}",
                "company": "Entreprise",
                "start_date": "2010",
                "missions": ["Une mission assez longue pour prendre de la place"] * 6,
            }
            for i in range(12)
        ]
        assert estimate_pages(spec, from_cv(spec, long_cv), fonts) >= 2

    def test_un_modele_sans_bande_tient_sur_une_page(self, spec: Json, fonts: FontSet) -> None:
        empty = {**spec, "bands": []}
        assert estimate_pages(empty, {"header": {}, "sections": []}, fonts) == 1
