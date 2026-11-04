"""Le moteur, côté atelier et lettre : adresses publiques, `data:`, aiguillage des moteurs."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from documents.infrastructure.fakes import (
    CV_ROLES,
    SERVED,
    blank_pdf,
    cv_fonts,
    cv_template_pdf,
    labels_for,
    png,
)
from documents.infrastructure.test_engine import FakeGotenberg, FakeLabeler
from karria.modules.documents.domain.letter import from_letter
from karria.modules.documents.domain.templates import Fit
from karria.modules.documents.domain.view import from_cv
from karria.modules.documents.infrastructure.engine import KarriaDocumentEngine, _data_url
from karria.modules.documents.infrastructure.fonts import FontFile, FontLibrary, FontSet
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.modules.documents.infrastructure.spec_builder import SpecBuilder
from karria.shared.config import StorageSettings
from karria.shared.storage import Bucket, InMemoryObjectStorage

pytestmark = pytest.mark.unit

Json = dict[str, Any]
CV: Json = {
    "identity": {"first_name": "Ada", "last_name": "Lovelace", "headline": "Ingénieure"},
    "contact": {"email": "ada@example.org"},
    "experiences": [{"position": "Analyste", "company": "Babbage", "missions": ["Compter"]}],
}
LETTER: Json = {
    "recipient": {"company": "Acme"},
    "subject": "Candidature",
    "paragraphs": [{"kind": "libre", "text": "Bonjour."}],
    "sender": {"first_name": "Ada", "last_name": "Lovelace"},
}


class ServedLibrary:
    """Sert les polices de test et publie leurs adresses."""

    def __init__(self) -> None:
        self.published: list[str] = []
        files = cv_fonts()
        self._files = {
            (400, False): FontFile("Testa", 400, False, files["Testa-Regular"]),
            (700, False): FontFile("Testa", 700, False, files["Testa-Bold"]),
        }

    async def file(self, family: str, weight: int, italic: bool) -> FontFile | None:
        if family != "Testa":
            return None
        return self._files.get((weight, False)) or self._files[(400, False)]

    async def public_url(self, file: FontFile) -> str:
        self.published.append(file.file_name)
        return f"https://storage.karria.fr/karria-test-assets/fonts/{file.file_name}"


@pytest.fixture(scope="module")
def built() -> tuple:
    with PdfDocument(cv_template_pdf()) as doc:
        page = doc.read()
        labels = labels_for(page.lines, CV_ROLES)
        labels["photo_shape"] = "circle"
        return SpecBuilder(doc, page, labels, FontSet([]), SERVED).build()


@pytest.fixture
def spec(built: tuple) -> Json:
    return copy.deepcopy(built[0])


def _engine(pdf: bytes = b"") -> tuple[KarriaDocumentEngine, ServedLibrary, FakeGotenberg]:
    library, gotenberg = ServedLibrary(), FakeGotenberg(pdf)
    engine = KarriaDocumentEngine(library, FakeLabeler(), gotenberg)  # type: ignore[arg-type]
    return engine, library, gotenberg


class TestRenduHtml:
    async def test_le_cv_est_ancre_avec_polices_publiques_pictogrammes_data_et_photo(
        self, spec: Json
    ) -> None:
        engine, library, _ = _engine()
        out = await engine.render_html(
            spec,
            from_cv(spec, CV),
            assets={"icon_phone.png": png()},
            photo_url="https://storage.karria.fr/media/p.jpg?s=1",
            anchored=True,
        )
        assert "https://storage.karria.fr/karria-test-assets/fonts/Testa-400.ttf" in out.html
        assert sorted(set(library.published)) == ["Testa-400.ttf", "Testa-700.ttf"]
        assert 'data-chemin="identity.headline"' in out.html
        assert 'src="https://storage.karria.fr/media/p.jpg?s=1"' in out.html
        assert out.pages == 1

    async def test_la_lettre_prend_le_moteur_de_lettre_et_le_logo_en_data(self, spec: Json) -> None:
        engine, _, _ = _engine()
        view = from_letter(spec, LETTER, today=date(2026, 11, 5))
        out = await engine.render_html(
            spec, view, assets={"logo.png": png()}, photo_url=None, anchored=True
        )
        assert "Objet : Candidature" in out.html
        assert 'src="data:image/png;base64,' in out.html
        assert 'data-chemin="paragraphs.0.text"' in out.html
        assert out.pages == 1

    def test_un_fichier_joint_devient_une_adresse_data(self) -> None:
        assert _data_url(png()).startswith("data:image/png;base64,")
        assert _data_url(b"\x00\x01").startswith("data:application/octet-stream;base64,")


class TestLettreImprimee:
    async def test_le_pdf_d_une_lettre_passe_par_gotenberg_avec_le_logo_joint(
        self, spec: Json
    ) -> None:
        pdf = blank_pdf(title="fit k=1 f=1 pages=1")
        engine, _, gotenberg = _engine(pdf)
        view = from_letter(spec, LETTER, today=date(2026, 11, 5))
        out = await engine.render_pdf(spec, view, assets={"logo.png": png()}, photo=None)
        html, files = gotenberg.html[0]
        assert "Objet : Candidature" in html
        assert 'src="logo.png"' in html
        assert "logo.png" in files
        assert "Testa-400.ttf" in files
        assert out.fit == Fit()

    async def test_le_docx_d_une_lettre_prend_le_moteur_de_lettre(self, spec: Json) -> None:
        engine, _, _ = _engine()
        view = from_letter(spec, LETTER, today=date(2026, 11, 5))
        data = await engine.render_docx(spec, view, assets={}, photo=None, fit=Fit())
        assert data.startswith(b"PK")


class TestPolicesPubliques:
    async def test_une_police_est_copiee_une_fois_dans_le_bucket_public(self) -> None:
        storage = InMemoryObjectStorage(StorageSettings(), "test")
        library = FontLibrary(storage, shared_cache=False)
        file = FontFile("Testa", 400, False, cv_fonts()["Testa-Regular"])

        first = await library.public_url(file)
        second = await library.public_url(file)

        assert first == second
        assert first.endswith("/karria-test-assets/fonts/Testa-400.ttf")
        assert await storage.exists(Bucket.ASSETS, "fonts/Testa-400.ttf")
        assert sum(1 for b, _ in storage.objects if b is Bucket.ASSETS) == 1
