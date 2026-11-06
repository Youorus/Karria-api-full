"""La boucle d'ingestion bornée : une passe si la porte passe, deux au plus, la meilleure gardée."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from documents.fakes import bad_fidelity, good_fidelity
from documents.infrastructure.fakes import (
    CV_ROLES,
    FakePlanner,
    blank_pdf,
    cv_template_pdf,
)
from documents.infrastructure.test_analyzer import FakeLibrary
from karria.modules.documents.application.ports import RenderedPdf
from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.gate import Fidelity
from karria.modules.documents.domain.templates import Fit
from karria.modules.documents.infrastructure.analyzer import TemplateAnalyzer
from karria.modules.documents.infrastructure.ingestion_loop import IngestionLoop

pytestmark = pytest.mark.unit


class Measures:
    """Des mesures programmées, rendues dans l'ordre ; note les rendus mesurés."""

    def __init__(self, *results: Fidelity) -> None:
        self._results = list(results)
        self.rendered: list[bytes] = []

    async def __call__(self, source: bytes, rendered: bytes) -> Fidelity:
        self.rendered.append(rendered)
        return self._results.pop(0)


class Renders:
    """Le moteur : rend une page vide (valide), note ce qu'on lui demande."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, spec: Any, view: Any, *, assets: dict[str, bytes], photo: bytes | None
    ) -> RenderedPdf:
        self.calls.append({"spec": spec, "view": view, "assets": assets, "photo": photo})
        return RenderedPdf(data=blank_pdf(210, 297), fit=Fit())


def loop(
    planner: FakePlanner, *measures: Fidelity, max_passes: int = 2
) -> tuple[IngestionLoop, Renders, Measures]:
    renders, measured = Renders(), Measures(*measures)
    analyzer = TemplateAnalyzer(FakeLibrary(), planner)  # type: ignore[arg-type]
    return (
        IngestionLoop(analyzer, planner, render=renders, measure=measured, max_passes=max_passes),
        renders,
        measured,
    )


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return cv_template_pdf()


async def test_une_passe_suffit_quand_la_porte_passe(pdf: bytes) -> None:
    planner = FakePlanner(CV_ROLES)
    run, renders, _ = loop(planner, good_fidelity())
    out = await run.run(pdf)
    assert [c[0] for c in planner.calls] == ["plan"]
    assert len(renders.calls) == 1
    assert renders.calls[0]["view"]["sample"] is True
    assert renders.calls[0]["photo"] is not None
    assert "sample_photo.png" not in renders.calls[0]["assets"]
    (record,) = out.passes
    assert record["pass"] == 1
    assert record["passed"] is True
    assert record["score"] == good_fidelity().score
    assert (record["input_tokens"], record["output_tokens"], record["model"]) == (
        7000,
        1500,
        "fake",
    )
    assert "kept" not in record
    assert out.unlabeled == []


async def test_la_seconde_passe_est_gardee_si_elle_fait_mieux(pdf: bytes) -> None:
    planner = FakePlanner(CV_ROLES)
    better = replace(bad_fidelity(), ssim_tolerant=0.7)
    run, renders, _ = loop(planner, bad_fidelity(), better)
    out = await run.run(pdf)
    assert [c[0] for c in planner.calls] == ["plan", "revise"]
    assert len(renders.calls) == 2
    comparison = planner.calls[1][1]
    assert comparison.startswith(b"\x89PNG")
    (feedback,) = planner.feedback
    assert feedback.score == bad_fidelity().score
    assert feedback.failures
    assert feedback.pages_render == 1
    assert feedback.unplaced == ()
    # La page vide rendue ne porte aucun mot : chaque ligne est absente du rendu.
    assert feedback.misplaced
    assert all(m["note"] == "absente du rendu" for m in feedback.misplaced)
    assert feedback.missing_shapes
    assert [p["pass"] for p in out.passes] == [1, 2]
    assert out.passes[1]["kept"] is True
    assert out.passes[1]["score"] == better.score
    assert "kept" not in out.passes[0]


async def test_la_premiere_passe_est_gardee_si_la_seconde_fait_moins_bien(pdf: bytes) -> None:
    planner = FakePlanner(CV_ROLES)
    first = replace(bad_fidelity(), ssim_tolerant=0.7)
    run, _, _ = loop(planner, first, bad_fidelity())
    out = await run.run(pdf)
    assert [p["pass"] for p in out.passes] == [1, 2]
    assert out.passes[0]["kept"] is True
    assert "kept" not in out.passes[1]
    assert out.passes[0]["score"] == first.score


async def test_une_ligne_non_placee_declenche_la_correction(pdf: bytes) -> None:
    planner = FakePlanner({k: v for k, v in CV_ROLES.items() if k != "Paris"})
    run, _, _ = loop(planner, good_fidelity(), good_fidelity())
    out = await run.run(pdf)
    assert [c[0] for c in planner.calls] == ["plan", "revise"]
    assert planner.feedback[0].unplaced == ("L8",)
    assert out.passes[0]["passed"] is False
    assert "lignes non placées : L8" in out.passes[0]["failures"]
    # Même score, autant de lignes non placées : la première passe reste.
    assert out.passes[0]["kept"] is True


async def test_une_correction_en_panne_garde_la_premiere_passe(
    pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner = FakePlanner(CV_ROLES)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise TemplateIngestionError("modèle injoignable")

    monkeypatch.setattr(planner, "revise", broken)
    run, renders, _ = loop(planner, bad_fidelity())
    out = await run.run(pdf)
    assert len(renders.calls) == 1
    assert [p["pass"] for p in out.passes] == [1, 2]
    assert "injoignable" in out.passes[1]["error"]
    assert out.spec["meta"]["title"] == "Azur"


async def test_une_seule_passe_quand_on_le_demande(pdf: bytes) -> None:
    planner = FakePlanner(CV_ROLES)
    run, renders, _ = loop(planner, bad_fidelity(), max_passes=1)
    out = await run.run(pdf)
    assert len(renders.calls) == 1
    assert len(out.passes) == 1
    assert out.passes[0]["passed"] is False
