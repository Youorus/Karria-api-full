"""La boucle d'ingestion bornée : plan → spécification → rendu → mesure, deux passes au plus (ADR-0037 § 3).

Contrat
-------
- Passe 1 : le modèle de vision dessine le plan ; l'assembleur en tire la
  spécification ; l'exemple du modèle est rendu (Gotenberg) et mesuré contre
  la source (`fidelity.measure`), la porte juge (`domain/gate.py`).
- Si la porte échoue (ou qu'une ligne n'est pas placée), passe 2 : le modèle
  reçoit le côte à côte source | rendu, les lignes hors de leur place, les
  formes manquantes, les lignes non placées, et rend un plan corrigé ; on
  réassemble, on rend, on remesure. **La meilleure passe est gardée** (le
  score, puis le moins de lignes non placées). Jamais plus de deux requêtes
  au modèle par version.
- Chaque passe est journalisée et rendue (`AnalyzedTemplate.passes`) : score,
  composantes, verdict, jetons, latence, modèle. Une panne de la passe 2
  (modèle injoignable, plan hors schéma) ne perd pas la passe 1.
- Ce qui n'atteint pas le seuil reste en revue : la boucle ne publie rien,
  elle rend le meilleur assemblage et ses mesures au cas d'usage.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

from karria.modules.documents.application.ports import RenderedPdf
from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.gate import Fidelity, evaluate
from karria.modules.documents.domain.view import from_sample
from karria.modules.documents.infrastructure.analyzer import (
    AnalyzedTemplate,
    PreparedTemplate,
    TemplateAnalyzer,
)
from karria.modules.documents.infrastructure.diagnostics import misplaced_lines, missing_shapes
from karria.modules.documents.infrastructure.layout_planner import (
    LayoutPlanner,
    PlanFeedback,
    PlanResult,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.shared.images import side_by_side
from karria.shared.logging import get_logger

logger = get_logger(__name__)

Json = dict[str, Any]
Render = Callable[..., Awaitable[RenderedPdf]]
Measure = Callable[[bytes, bytes], Awaitable[Fidelity]]

#: Requêtes au modèle par version, au plus : le plan, puis une correction.
MAX_PASSES: Final = 2


class IngestionLoop:
    """Enchaîne plan, assemblage, rendu et mesure, et garde la meilleure passe."""

    def __init__(
        self,
        analyzer: TemplateAnalyzer,
        planner: LayoutPlanner,
        *,
        render: Render,
        measure: Measure,
        max_passes: int = MAX_PASSES,
    ) -> None:
        """
        Args:
            render: `(spec, view, assets=…, photo=…) -> RenderedPdf` — le moteur.
            measure: `(source_pdf, rendered_pdf) -> Fidelity` — la mesure.
        """
        self._analyzer = analyzer
        self._planner = planner
        self._render = render
        self._measure = measure
        self._max_passes = max(1, max_passes)

    async def run(self, pdf: bytes) -> AnalyzedTemplate:
        """Le meilleur assemblage d'un PDF de modèle, avec le journal de ses passes."""
        with await self._analyzer.prepare(pdf) as prepared:
            started = time.monotonic()
            result = await self._planner.plan(prepared.page_png, prepared.measured)
            best, fidelity, rendered = await self._assemble_and_measure(prepared, result.plan, pdf)
            best.passes = [self._record(1, result, best, fidelity, started)]
            if self._settled(best, fidelity) or self._max_passes < 2:  # noqa: PLR2004
                return best
            started = time.monotonic()
            try:
                feedback = await asyncio.to_thread(
                    self._feedback, prepared, best, fidelity, rendered
                )
                comparison = await asyncio.to_thread(
                    side_by_side, prepared.page_png, await asyncio.to_thread(_page_png, rendered)
                )
                revised = await self._planner.revise(
                    prepared.page_png, prepared.measured, result.plan, feedback, comparison
                )
                again, fidelity_2, _ = await self._assemble_and_measure(prepared, revised.plan, pdf)
            except TemplateIngestionError as exc:
                logger.warning("documents.ingestion_revision_failed", error=str(exc)[:200])
                best.passes.append(
                    {"pass": 2, "error": str(exc.details.get("detail") or exc)[:300]}
                )
                return best
            again.passes = [*best.passes, self._record(2, revised, again, fidelity_2, started)]
            if self._better(again, fidelity_2, best, fidelity):
                again.passes[-1]["kept"] = True
                return again
            best.passes = again.passes
            best.passes[0]["kept"] = True
            return best

    async def _assemble_and_measure(
        self, prepared: PreparedTemplate, plan: Any, pdf: bytes
    ) -> tuple[AnalyzedTemplate, Fidelity, bytes]:
        analyzed = await self._analyzer.assemble(prepared, plan)
        view = from_sample(analyzed.spec, analyzed.sample)
        photo_name = (analyzed.spec["header"].get("photo") or {}).get("sample_file")
        photo = analyzed.assets.get(photo_name) if photo_name else None
        icons = {k: v for k, v in analyzed.assets.items() if not k.startswith("sample_")}
        rendered = await self._render(analyzed.spec, view, assets=icons, photo=photo)
        fidelity = await self._measure(pdf, rendered.data)
        return analyzed, fidelity, rendered.data

    @staticmethod
    def _settled(analyzed: AnalyzedTemplate, fidelity: Fidelity) -> bool:
        return evaluate(fidelity).passed and not analyzed.unlabeled

    @staticmethod
    def _better(
        candidate: AnalyzedTemplate, score: Fidelity, best: AnalyzedTemplate, best_score: Fidelity
    ) -> bool:
        return (score.score, -len(candidate.unlabeled)) > (best_score.score, -len(best.unlabeled))

    @staticmethod
    def _feedback(
        prepared: PreparedTemplate, analyzed: AnalyzedTemplate, fidelity: Fidelity, rendered: bytes
    ) -> PlanFeedback:
        decision = evaluate(fidelity)
        return PlanFeedback(
            score=fidelity.score,
            failures=decision.failures,
            misplaced=tuple(misplaced_lines(prepared.page, rendered)),
            missing_shapes=tuple(missing_shapes(prepared.page, rendered)),
            unplaced=tuple(analyzed.unlabeled),
            pages_render=fidelity.pages_render,
        )

    @staticmethod
    def _record(
        number: int,
        result: PlanResult,
        analyzed: AnalyzedTemplate,
        fidelity: Fidelity,
        started: float,
    ) -> Json:
        decision = evaluate(fidelity)
        record = {
            "pass": number,
            "score": fidelity.score,
            "components": fidelity.components(),
            "passed": decision.passed and not analyzed.unlabeled,
            "failures": list(decision.failures)
            + (
                [f"lignes non placées : {', '.join(analyzed.unlabeled)}"]
                if analyzed.unlabeled
                else []
            ),
            "pages_render": fidelity.pages_render,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
            "model": result.model,
            "provider": result.provider,
            "duration_s": round(time.monotonic() - started, 1),
        }
        logger.info(
            "documents.ingestion_pass",
            number=number,
            score=fidelity.score,
            passed=record["passed"],
            usage_in=result.input_tokens,
            usage_out=result.output_tokens,
            model=result.model,
        )
        return record


def _page_png(pdf: bytes) -> bytes:
    with PdfDocument(pdf) as doc:
        return doc.render_png(0, dpi=100)
