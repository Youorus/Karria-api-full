"""Analyse d'un PDF de modèle : polices résolues, rôles des lignes, spécification.

Contrat
-------
L'ordre compte, et il est fixé ici :

1. lire les polices du PDF et les **résoudre** (`FontLibrary.resolve`) : la
   géométrie des lignes se pose avec les métriques verticales de la police
   **servie**, celles qu'emploiera Chromium ;
2. lire la page (lignes, formes, images) ;
3. demander les rôles des lignes au modèle de vision (`LineLabeler`) ; une
   ligne sans rôle bloque la publication (elle est signalée dans le rapport) ;
4. construire la spécification, puis compléter les polices servies avec les
   graisses que la spécification a ajoutées (le poste visé synthétisé).

Le rapport de polices (`fonts_report`) dit, pour chaque police du PDF, ce qui
est servi et pourquoi : il part dans la revue du modèle.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.fonts import (
    FontResolution,
    family_from_name,
    italic_from_name,
    weight_from_name,
)
from karria.modules.documents.infrastructure.fonts import (
    FontFile,
    FontLibrary,
    FontSet,
    facts_of,
    source_font,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument, VerticalMetrics
from karria.modules.documents.infrastructure.spec_builder import SpecBuilder
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Au-delà de cette surface (≈ A2), une page n'est pas un CV et sa rastérisation
#: à 150 dpi pèserait des centaines de Mio.
MAX_PAGE_AREA_MM2: Final = 250_000.0

Json = dict[str, Any]


class LineLabeler(Protocol):
    """Donne un rôle à chaque ligne d'un modèle (modèle de vision)."""

    async def label(self, page_png: bytes, lines: list[Json]) -> Json:
        """`{title, category, description_fr, tags, photo_shape, columns, lines: [{line, role, section, item}]}`."""
        ...


@dataclass(slots=True)
class AnalyzedTemplate:
    """Tout ce que l'analyse d'un PDF produit."""

    spec: Json
    sample: Json
    labels: Json
    assets: dict[str, bytes]
    fonts: list[FontFile]
    fonts_report: list[Json]
    page_png: bytes
    unlabeled: list[str] = field(default_factory=list)


class TemplateAnalyzer:
    """PDF de modèle → spécification, avec ses polices."""

    def __init__(self, library: FontLibrary, labeler: LineLabeler) -> None:
        self._library = library
        self._labeler = labeler

    async def resolve_fonts(
        self, doc: PdfDocument
    ) -> tuple[dict[str, FontResolution], dict[str, FontFile]]:
        """Chaque police du PDF → la police servie (résolution et fichier)."""
        resolutions: dict[str, FontResolution] = {}
        files: dict[str, FontFile] = {}
        embedded = doc.fonts()
        names = sorted({c["font"] for c in doc.text_lines()})
        for name in names:
            font = embedded.get(name)
            weight = weight_from_name(name, default=font.weight if font and font.weight else 400)
            italic = italic_from_name(name) or bool(font and font.italic)
            source = source_font(
                name,
                font.data if font else None,
                weight=weight,
                italic=italic,
                family=family_from_name(name),
            )
            resolution = await self._library.resolve(source)
            resolutions[name] = resolution
            served = await self._library.file(
                resolution.family, resolution.weight, resolution.italic, download=True
            )
            if served is not None:
                files[name] = served
        return resolutions, files

    async def analyze(self, pdf: bytes, *, labels: Json | None = None) -> AnalyzedTemplate:
        """Analyse un PDF de modèle d'une page."""
        with PdfDocument(pdf) as doc:
            if doc.page_count != 1:
                raise TemplateIngestionError(
                    f"{doc.page_count} pages : un modèle de CV tient sur une seule page"
                )
            width_mm, height_mm = doc.page_size_mm(0)
            if width_mm * height_mm > MAX_PAGE_AREA_MM2:
                raise TemplateIngestionError(
                    f"page de {width_mm:.0f} × {height_mm:.0f} mm : trop grande pour un CV"
                )
            resolutions, files = await self.resolve_fonts(doc)

            def metrics(name: str) -> VerticalMetrics | None:
                served = files.get(name)
                if served is None:
                    return None
                facts = facts_of(served.data)
                return VerticalMetrics(facts.ascent, facts.descent)

            # Lecture et rastérisation : plusieurs secondes de CPU, hors de la boucle.
            page = await asyncio.to_thread(doc.read, metrics=metrics)
            page_png = await asyncio.to_thread(doc.render_png, dpi=100)
            if labels is None:
                compact = [
                    {k: ln[k] for k in ("id", "text", "font", "size", "bold", "x", "y", "w")}
                    for ln in page.lines
                ]
                labels = await self._labeler.label(page_png, compact)
            known = {ln["id"] for ln in page.lines}
            got = {lb["line"] for lb in labels.get("lines", [])}
            unlabeled = sorted(known - got, key=lambda s: int(s[1:]) if s[1:].isdigit() else 0)
            served = {name: (f.family, f.weight, f.italic) for name, f in files.items()}
            font_set = FontSet(
                list({(f.family, f.weight, f.italic): f for f in files.values()}.values())
            )
            spec, sample, assets = SpecBuilder(doc, page, labels, font_set, served).build()
        all_files = {(f.family, f.weight, f.italic): f for f in files.values()}
        for ref in spec["fonts"]:
            key = (ref["family"], ref["weight"], ref["italic"])
            if key not in all_files:
                extra = await self._library.file(*key)
                if extra is not None:
                    all_files[(extra.family, extra.weight, extra.italic)] = extra
                    ref["weight"], ref["italic"] = extra.weight, extra.italic
        report = [
            {
                "source": r.source,
                "served": f"{r.family} {r.weight}{' italique' if r.italic else ''}",
                "family": r.family,
                "weight": r.weight,
                "italic": r.italic,
                "origin": r.origin.value,
                "licence": r.licence,
                "distance": r.distance,
                "note": r.note,
            }
            for r in resolutions.values()
        ]
        for line in report:
            logger.info("documents.font_resolved", **{k: v for k, v in line.items() if k != "note"})
        spec["fonts_report"] = report
        return AnalyzedTemplate(
            spec=spec,
            sample=sample,
            labels=labels,
            assets=assets,
            fonts=list(all_files.values()),
            fonts_report=report,
            page_png=page_png,
            unlabeled=unlabeled,
        )
