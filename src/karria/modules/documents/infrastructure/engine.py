"""Implémente `DocumentEngine` : analyse, rendus, mesures, aperçus, lecture ATS.

Contrat
-------
- Les polices de chaque rendu viennent de la bibliothèque (`FontLibrary`,
  bucket `templates`, `fonts/…`) d'après `spec["fonts"]` : jamais d'Internet
  pendant un rendu, une police déjà là est relue du stockage (puis gardée en
  mémoire par le processus).
- Le calcul lourd et bloquant (mesure numpy, rastérisation pdfium, DOCX)
  part dans un fil (`asyncio.to_thread`) : la boucle d'événements de l'API
  reste libre pendant un export.
- Une vue `kind: "letter"` (ADR-0030) prend les moteurs de lettre, qui lisent
  une **spécification de lettre** (ADR-0036 : la lettre appariée du modèle ou
  la lettre sobre) ; le reste de la chaîne (polices, Gotenberg, fichiers
  joints) est le même. `analyze_letter` mesure la lettre appariée d'un modèle.
- `measure` reçoit le rapport de polices de l'analyse : une substitution
  déclarée n'est pas comptée comme une infidélité de police.
- `render_html` (ADR-0029) rend la page de l'atelier : polices par URL
  publique stable, pictogrammes et logo en `data:`, photo par l'URL reçue,
  ancres `data-chemin`, nombre de pages estimé sans navigateur.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from karria.modules.documents.application.ports import (
    Analysis,
    Previews,
    RenderedHtml,
    RenderedPdf,
)
from karria.modules.documents.domain.ats import ExtractedDocument
from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.gate import Fidelity
from karria.modules.documents.domain.templates import Fit
from karria.modules.documents.infrastructure.analyzer import LineLabeler, TemplateAnalyzer
from karria.modules.documents.infrastructure.ats_reader import read_docx, read_pdf
from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer
from karria.modules.documents.infrastructure.fidelity import measure, served_families
from karria.modules.documents.infrastructure.fonts import FontLibrary, FontSet
from karria.modules.documents.infrastructure.gotenberg import GotenbergClient
from karria.modules.documents.infrastructure.html_renderer import (
    HtmlRenderer,
    estimate_pages,
    parse_fit,
)
from karria.modules.documents.infrastructure.letter_analyzer import (
    LetterAnalyzer,
    LetterLineLabeler,
)
from karria.modules.documents.infrastructure.letter_renderer import (
    LOGO_FILE,
    LetterDocxRenderer,
    LetterHtmlRenderer,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.shared.images import blurhash, image_size, resize_to_png, sniff_content_type, to_webp


class KarriaDocumentEngine:
    """Le moteur de documents : PDF par Chromium (Gotenberg), DOCX par python-docx."""

    def __init__(
        self,
        library: FontLibrary,
        labeler: LineLabeler,
        gotenberg: GotenbergClient,
        letter_labeler: LetterLineLabeler | None = None,
    ) -> None:
        """`letter_labeler` : l'étiqueteur des lignes d'une lettre appariée (ADR-0036) ;
        sans lui, `analyze_letter` refuse."""
        self._library = library
        self._analyzer = TemplateAnalyzer(library, labeler)
        self._letters = LetterAnalyzer(library, letter_labeler) if letter_labeler else None
        self._gotenberg = gotenberg

    async def fonts(self, spec: dict[str, Any]) -> FontSet:
        """Les polices servies d'une spécification."""
        files = []
        for ref in spec.get("fonts", []):
            found = await self._library.file(ref["family"], int(ref["weight"]), bool(ref["italic"]))
            if found is not None:
                files.append(found)
        return FontSet(files)

    async def analyze(self, pdf: bytes) -> Analysis:
        analyzed = await self._analyzer.analyze(pdf)
        return Analysis(
            spec=analyzed.spec,
            sample=analyzed.sample,
            labels=analyzed.labels,
            assets=analyzed.assets,
            fonts=[
                {"family": f.family, "weight": f.weight, "italic": f.italic} for f in analyzed.fonts
            ],
            fonts_report=analyzed.fonts_report,
            unlabeled=analyzed.unlabeled,
        )

    async def analyze_letter(self, pdf: bytes, *, accent: str | None = None) -> Analysis:
        """La lettre appariée d'un modèle → sa spécification de lettre (ADR-0036)."""
        if self._letters is None:
            raise TemplateIngestionError("aucun étiqueteur de lettre n'est branché")
        analyzed = await self._letters.analyze(pdf, accent=accent)
        return Analysis(
            spec=analyzed.spec,
            sample=analyzed.sample,
            labels=analyzed.labels,
            assets={},
            fonts=[
                {"family": f.family, "weight": f.weight, "italic": f.italic} for f in analyzed.fonts
            ],
            fonts_report=analyzed.fonts_report,
            unlabeled=analyzed.unlabeled,
        )

    async def render_pdf(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo: bytes | None,
    ) -> RenderedPdf:
        fonts = await self.fonts(spec)
        # Chromium reconnaît une image locale à son extension : on la tire des octets.
        photo_file = None
        if photo:
            photo_file = "photo.png" if sniff_content_type(photo) == "image/png" else "photo.jpg"
        if view.get("kind") == "letter":
            html = LetterHtmlRenderer(
                spec, view, fonts, logo=LOGO_FILE if LOGO_FILE in assets else None
            ).render()
        else:
            html = HtmlRenderer(spec, view, fonts, photo=photo_file).render()
        files = {**assets, **{f.file_name: f.data for f in fonts.files}}
        if photo and photo_file:
            files[photo_file] = photo
        data = await self._gotenberg.html_to_pdf(html, files)
        title = await asyncio.to_thread(_title, data)
        k, f, pages = parse_fit(title)
        return RenderedPdf(data=data, fit=Fit(spacing=k, font_scale=f, pages=pages))

    async def render_html(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo_url: str | None,
        anchored: bool,
    ) -> RenderedHtml:
        fonts = await self.fonts(spec)
        urls = {f.file_name: await self._library.public_url(f) for f in fonts.files}
        urls.update({name: _data_url(data) for name, data in assets.items()})
        if photo_url:
            urls["photo"] = photo_url

        def url_for(name: str) -> str:
            return urls.get(name, name)

        if view.get("kind") == "letter":
            html = LetterHtmlRenderer(
                spec,
                view,
                fonts,
                logo=LOGO_FILE if LOGO_FILE in assets else None,
                url_for=url_for,
                anchored=anchored,
            ).render()
            return RenderedHtml(html=html, pages=1)
        html = HtmlRenderer(
            spec,
            view,
            fonts,
            photo="photo" if photo_url else None,
            url_for=url_for,
            anchored=anchored,
        ).render()
        return RenderedHtml(html=html, pages=estimate_pages(spec, view, fonts))

    async def render_docx(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo: bytes | None,
        fit: Fit,
    ) -> bytes:
        fonts = await self.fonts(spec)
        if view.get("kind") == "letter":
            letter = LetterDocxRenderer(
                spec, view, fonts, logo=assets.get(LOGO_FILE), k=fit.spacing, f=fit.font_scale
            )
            return await asyncio.to_thread(letter.render)
        renderer = DocxRenderer(spec, view, fonts, photo=photo, k=fit.spacing, f=fit.font_scale)
        return await asyncio.to_thread(renderer.render, assets)

    async def docx_to_pdf(self, docx: bytes) -> bytes:
        return await self._gotenberg.docx_to_pdf(docx)

    async def measure(
        self,
        source_pdf: bytes,
        rendered_pdf: bytes,
        *,
        fonts_report: list[dict[str, Any]] | None = None,
    ) -> Fidelity:
        font_map = served_families(fonts_report) if fonts_report else None
        return await asyncio.to_thread(measure, source_pdf, rendered_pdf, font_map=font_map)

    async def previews(self, pdf: bytes) -> Previews:
        return await asyncio.to_thread(_previews, pdf)

    async def page_images(self, pdf: bytes) -> list[tuple[bytes, int, int]]:
        return await asyncio.to_thread(_pages, pdf)

    def read(self, data: bytes, fmt: str) -> ExtractedDocument:
        return read_pdf(data) if fmt == "pdf" else read_docx(data)


def _data_url(data: bytes) -> str:
    """Un fichier joint en `data:` : un pictogramme ou un logo de quelques kilo-octets."""
    kind = sniff_content_type(data) or "application/octet-stream"
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"


def _title(pdf: bytes) -> str:
    with PdfDocument(pdf) as doc:
        return doc.metadata_title()


def _previews(pdf: bytes) -> Previews:
    """Pleine page 150 dpi, aperçu ~1 240 px de haut, vignette WebP 480 px (ADR-0020 § 5)."""
    with PdfDocument(pdf) as doc:
        full = doc.render_png(0, dpi=150)
    return Previews(
        full_png=full,
        preview_png=resize_to_png(full, max_edge=1240),
        thumbnail_webp=to_webp(full, max_edge=480),
        blurhash=blurhash(full),
    )


def _pages(pdf: bytes) -> list[tuple[bytes, int, int]]:
    """Chaque page à 150 dpi (1 240 × 1 754 en A4), avec ses dimensions."""
    out = []
    with PdfDocument(pdf) as doc:
        for index in range(doc.page_count):
            png = doc.render_png(index, dpi=150)
            width, height = image_size(png)
            out.append((png, width, height))
    return out
