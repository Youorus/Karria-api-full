"""Le moteur de documents : polices de la bibliothèque, rendus, ajustement relu, aperçus."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pypdfium2 as pdfium
import pytest

from documents.infrastructure.fakes import PdfBuilder, blank_pdf, png
from karria.modules.documents.domain.templates import Fit
from karria.modules.documents.infrastructure import engine as engine_module
from karria.modules.documents.infrastructure.analyzer import AnalyzedTemplate
from karria.modules.documents.infrastructure.engine import KarriaDocumentEngine
from karria.modules.documents.infrastructure.fonts import FontFile, FontSet
from karria.shared.images import image_size

pytestmark = pytest.mark.unit

SPEC: dict[str, Any] = {
    "fonts": [
        {"family": "Testa", "weight": 400, "italic": False},
        {"family": "Absente", "weight": 700, "italic": True},
    ]
}


class FakeLibrary:
    """Ne connaît que Testa 400."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, int, bool]] = []

    async def file(self, family: str, weight: int, italic: bool) -> FontFile | None:
        self.asked.append((family, weight, italic))
        if family == "Testa":
            return FontFile("Testa", weight, italic, b"FONT")
        return None


class FakeGotenberg:
    def __init__(self, pdf: bytes) -> None:
        self.pdf = pdf
        self.html: list[tuple[str, dict[str, bytes]]] = []
        self.docx: list[bytes] = []

    async def html_to_pdf(self, html: str, assets: dict[str, bytes]) -> bytes:
        self.html.append((html, assets))
        return self.pdf

    async def docx_to_pdf(self, docx: bytes) -> bytes:
        self.docx.append(docx)
        return self.pdf


class FakeLabeler:
    async def label(self, page_png: bytes, lines: list[dict[str, Any]]) -> dict[str, Any]:
        raise AssertionError("non appelé")


class FakeHtmlRenderer:
    seen: dict[str, Any]

    def __init__(self, spec: Any, view: Any, fonts: FontSet, *, photo: str | None) -> None:
        FakeHtmlRenderer.seen = {"fonts": fonts, "photo": photo}

    def render(self) -> str:
        return "<html>"


def _engine(pdf: bytes = b"") -> tuple[KarriaDocumentEngine, FakeLibrary, FakeGotenberg]:
    library, gotenberg = FakeLibrary(), FakeGotenberg(pdf)
    engine = KarriaDocumentEngine(library, FakeLabeler(), gotenberg)  # type: ignore[arg-type]
    return engine, library, gotenberg


async def test_les_polices_servies_viennent_de_la_bibliotheque() -> None:
    engine, library, _ = _engine()
    fonts = await engine.fonts(SPEC)
    assert [f.family for f in fonts.files] == ["Testa"]
    assert library.asked == [("Testa", 400, False), ("Absente", 700, True)]
    assert (await engine.fonts({})).files == []


async def test_l_analyse_rend_le_contrat_du_port(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _, _ = _engine()
    analyzed = AnalyzedTemplate(
        spec={"s": 1},
        sample={"x": 1},
        labels={"lines": []},
        assets={"a.png": b"A"},
        fonts=[FontFile("Testa", 700, True, b"F")],
        fonts_report=[{"source": "Testa-Bold"}],
        page_png=b"P",
        unlabeled=["L3"],
    )

    async def fake_analyze(pdf: bytes) -> AnalyzedTemplate:
        assert pdf == b"%PDF"
        return analyzed

    monkeypatch.setattr(engine._analyzer, "analyze", fake_analyze)
    out = await engine.analyze(b"%PDF")
    assert out.spec == {"s": 1}
    assert out.fonts == [{"family": "Testa", "weight": 700, "italic": True}]
    assert out.fonts_report == [{"source": "Testa-Bold"}]
    assert out.unlabeled == ["L3"]
    assert out.assets == {"a.png": b"A"}


class TestRenduPdf:
    @pytest.fixture(autouse=True)
    def _renderer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_module, "HtmlRenderer", FakeHtmlRenderer)

    async def test_l_ajustement_est_relu_dans_le_titre(self) -> None:
        pdf = blank_pdf(title="fit k=0.8 f=0.96 pages=2")
        engine, _, gotenberg = _engine(pdf)
        photo = png()
        out = await engine.render_pdf(SPEC, {}, assets={"icon.png": b"I"}, photo=photo)
        assert out.data == pdf
        assert out.fit == Fit(spacing=0.8, font_scale=0.96, pages=2)
        html, files = gotenberg.html[0]
        assert html == "<html>"
        assert files == {"icon.png": b"I", "Testa-400.ttf": b"FONT", "photo.png": photo}
        assert FakeHtmlRenderer.seen["photo"] == "photo.png"

    async def test_une_photo_jpeg_garde_son_extension(self) -> None:
        from doubles import sample_image  # noqa: PLC0415

        engine, _, gotenberg = _engine(blank_pdf())
        out = await engine.render_pdf(SPEC, {}, assets={}, photo=sample_image())
        assert "photo.jpg" in gotenberg.html[0][1]
        assert out.fit == Fit()

    async def test_sans_photo(self) -> None:
        engine, _, gotenberg = _engine(blank_pdf(title="autre chose"))
        out = await engine.render_pdf(SPEC, {}, assets={}, photo=None)
        assert FakeHtmlRenderer.seen["photo"] is None
        assert set(gotenberg.html[0][1]) == {"Testa-400.ttf"}
        assert out.fit == Fit()


async def test_le_docx_part_aux_facteurs_du_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeDocx:
        def __init__(self, spec: Any, view: Any, fonts: FontSet, **kw: Any) -> None:
            seen.update(kw, fonts=[f.family for f in fonts.files])

        def render(self, assets: dict[str, bytes]) -> bytes:
            seen["assets"] = assets
            return b"DOCX"

    monkeypatch.setattr(engine_module, "DocxRenderer", FakeDocx)
    engine, _, _ = _engine()
    out = await engine.render_docx(
        SPEC, {}, assets={"a": b"1"}, photo=b"P", fit=Fit(spacing=0.7, font_scale=0.92)
    )
    assert out == b"DOCX"
    assert seen == {"photo": b"P", "k": 0.7, "f": 0.92, "fonts": ["Testa"], "assets": {"a": b"1"}}


async def test_docx_vers_pdf_par_gotenberg() -> None:
    engine, _, gotenberg = _engine(b"%PDF")
    assert await engine.docx_to_pdf(b"DOCX") == b"%PDF"
    assert gotenberg.docx == [b"DOCX"]


async def test_la_mesure_compare_les_deux_pdf() -> None:
    engine, _, _ = _engine()
    b = PdfBuilder(40, 30)
    b.text("Jean", 5, 10)
    page = b.to_bytes()
    fidelity = await engine.measure(page, page)
    assert fidelity.words_matched == 1.0


async def test_les_apercus() -> None:
    engine, _, _ = _engine()
    previews = await engine.previews(blank_pdf(60, 40))
    assert image_size(previews.full_png) == (355, 237)  # 150 dpi, arrondi par pdfium
    assert previews.preview_png.startswith(b"\x89PNG")
    assert previews.thumbnail_webp[8:12] == b"WEBP"
    assert previews.blurhash is not None
    assert len(previews.blurhash) == 6 + 2 * (4 * 5 - 1)


async def test_chaque_page_en_image() -> None:
    doc = pdfium.PdfDocument.new()
    doc.new_page(100, 50)
    doc.new_page(50, 100)
    buffer = io.BytesIO()
    doc.save(buffer)
    engine, _, _ = _engine()
    pages = await engine.page_images(buffer.getvalue())
    assert [(w, h) for _, w, h in pages] == [(209, 105), (105, 209)]


def test_lecture_ats_selon_le_format() -> None:
    engine, _, _ = _engine()
    b = PdfBuilder(40, 30)
    b.text("Jean", 5, 10)
    assert engine.read(b.to_bytes(), "pdf").format == "pdf"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", "<w:p><w:r><w:t>Jean</w:t></w:r></w:p>")
    assert engine.read(out.getvalue(), "docx").text == "Jean"
