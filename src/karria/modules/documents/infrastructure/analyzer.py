"""Analyse d'un PDF de modèle : polices résolues, plan de mise en page, spécification.

Contrat
-------
L'ordre compte, et il est fixé ici :

1. **préparer** (`prepare`) : lire les polices du PDF et les **résoudre**
   (`FontLibrary.resolve_group`, une famille source → une famille servie) ;
   la géométrie des lignes se pose avec les métriques verticales de la police
   **servie**, celles qu'emploiera Chromium ; lire la page (lignes, formes,
   images) et la rastériser ;
2. demander le **plan** de mise en page au modèle de vision
   (`LayoutPlanner`, ADR-0037) : qui est quoi, qui s'ancre à quoi ; une ligne
   que le plan ne place pas bloque la publication (elle est signalée) ;
3. **assembler** (`assemble`) la spécification d'après le plan et les mesures
   (`SpecAssembler`), puis compléter les polices servies avec les graisses
   que la spécification a ajoutées (le poste visé synthétisé).

`prepare` et `assemble` sont séparés pour que la boucle d'ingestion
(`ingestion_loop.py`) assemble plusieurs plans sur une même lecture ;
`analyze` les enchaîne pour un appel simple.

Le rapport de polices (`fonts_report`) dit, pour chaque police du PDF, ce qui
est servi et pourquoi : il part dans la revue du modèle.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Final

from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.fonts import (
    FontResolution,
    SourceFont,
    family_from_name,
    italic_from_name,
    weight_from_name,
)
from karria.modules.documents.infrastructure.fonts import (
    FontFacts,
    FontFile,
    FontLibrary,
    FontSet,
    facts_of,
    source_font,
)
from karria.modules.documents.infrastructure.layout_planner import (
    LayoutPlan,
    LayoutPlanner,
    measured_inputs,
)
from karria.modules.documents.infrastructure.pdf_reader import (
    PdfDocument,
    PdfPage,
    VerticalMetrics,
)
from karria.modules.documents.infrastructure.spec_assembler import SpecAssembler
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Au-delà de cette surface (≈ A2), une page n'est pas un CV et sa rastérisation
#: à 150 dpi pèserait des centaines de Mio.
MAX_PAGE_AREA_MM2: Final = 250_000.0

Json = dict[str, Any]


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
    plan: LayoutPlan | None = None
    #: Les passes de la boucle d'ingestion (score, verdict, jetons), par passe.
    passes: list[Json] = field(default_factory=list)


@dataclass(slots=True)
class PreparedTemplate:
    """Une page lue, ses polices résolues, son image : de quoi assembler un plan.

    Tient le PDF ouvert (pdfium) : à fermer (`close`) ou à employer comme
    gestionnaire de contexte.
    """

    doc: PdfDocument
    page: PdfPage
    page_png: bytes
    resolutions: dict[str, FontResolution]
    files: dict[str, FontFile]

    @property
    def measured(self) -> Json:
        """Ce que le planificateur reçoit."""
        return measured_inputs(self.page)

    def __enter__(self) -> PreparedTemplate:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Libère les ressources natives du PDF."""
        self.doc.close()


class TemplateAnalyzer:
    """PDF de modèle → spécification, avec ses polices."""

    def __init__(self, library: FontLibrary, planner: LayoutPlanner) -> None:
        self._library = library
        self._planner = planner

    async def resolve_fonts(
        self, doc: PdfDocument
    ) -> tuple[dict[str, FontResolution], dict[str, FontFile]]:
        """Chaque police du PDF → la police servie (résolution et fichier), famille par famille."""
        resolutions: dict[str, FontResolution] = {}
        files: dict[str, FontFile] = {}
        embedded = doc.fonts()
        names = sorted({c["font"] for c in doc.text_lines()})
        groups: dict[str, list[SourceFont]] = defaultdict(list)
        for name in names:
            font = embedded.get(name)
            # La graisse lue dans la police incorporée (OS/2) ; pdfium la devine
            # mal (mesuré : 1016 pour un italique régulier, 265 pour un romain).
            facts = embedded_facts(font.data) if font and font.data else None
            default = facts.weight if facts and facts.weight else 400
            weight = weight_from_name(name, default=default)
            italic = italic_from_name(name) or bool(facts.italic if facts else font and font.italic)
            family = family_from_name(name)
            groups[family].append(
                source_font(
                    name, font.data if font else None, weight=weight, italic=italic, family=family
                )
            )
        for sources in groups.values():
            for resolution in await self._library.resolve_group(sources):
                resolutions[resolution.source] = resolution
                served = await self._library.file(
                    resolution.family, resolution.weight, resolution.italic, download=True
                )
                if served is not None:
                    files[resolution.source] = served
        return resolutions, files

    async def prepare(self, pdf: bytes) -> PreparedTemplate:
        """Lit le PDF (une page), résout ses polices, pose sa géométrie, le rastérise.

        Raises:
            TemplateIngestionError: Plusieurs pages, ou une page trop grande.
        """
        doc = PdfDocument(pdf)
        try:
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
        except BaseException:
            doc.close()
            raise
        return PreparedTemplate(doc, page, page_png, resolutions, files)

    async def assemble(self, prepared: PreparedTemplate, plan: LayoutPlan) -> AnalyzedTemplate:
        """La spécification d'un plan sur une page préparée."""
        files = prepared.files
        served = {name: (f.family, f.weight, f.italic) for name, f in files.items()}
        font_set = FontSet(
            list({(f.family, f.weight, f.italic): f for f in files.values()}.values())
        )
        assembler = SpecAssembler(prepared.doc, prepared.page, plan, font_set, served)
        spec, sample, assets = assembler.build()
        all_files = {(f.family, f.weight, f.italic): f for f in files.values()}
        for ref in spec["fonts"]:
            key = (ref["family"], ref["weight"], ref["italic"])
            if key not in all_files:
                extra = await self._library.file(*key)
                if extra is not None:
                    all_files[(extra.family, extra.weight, extra.italic)] = extra
                    ref["weight"], ref["italic"] = extra.weight, extra.italic
        report = fonts_report(prepared.resolutions)
        spec["fonts_report"] = report
        return AnalyzedTemplate(
            spec=spec,
            sample=sample,
            labels=assembler.labels,
            assets=assets,
            fonts=list(all_files.values()),
            fonts_report=report,
            page_png=prepared.page_png,
            unlabeled=list(assembler.check.unplaced),
            plan=plan,
        )

    async def analyze(self, pdf: bytes, *, plan: LayoutPlan | None = None) -> AnalyzedTemplate:
        """Analyse un PDF de modèle d'une page : préparation, plan (sauf s'il est donné), assemblage."""
        with await self.prepare(pdf) as prepared:
            if plan is None:
                plan = (await self._planner.plan(prepared.page_png, prepared.measured)).plan
            return await self.assemble(prepared, plan)


def embedded_facts(data: bytes) -> FontFacts | None:
    """Ce que fontTools lit d'une police incorporée, ou rien si elle ne se lit pas."""
    try:
        return facts_of(data)
    except Exception:
        return None


def fonts_report(resolutions: dict[str, FontResolution]) -> list[Json]:
    """Pour chaque police du PDF, ce qui est servi et pourquoi (journalisé)."""
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
    return report
