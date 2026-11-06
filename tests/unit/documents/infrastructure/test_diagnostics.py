"""Ce que la mesure dit ligne par ligne et forme par forme : de quoi corriger un plan."""

from __future__ import annotations

import pytest

from documents.infrastructure.fakes import PdfBuilder
from karria.modules.documents.infrastructure.diagnostics import (
    line_words,
    misplaced_lines,
    missing_shapes,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument

pytestmark = pytest.mark.unit


def _page(shift_mm: float = 0.0, *, rule: bool = True, second: str = "Python SQL") -> bytes:
    b = PdfBuilder(80, 60)
    b.text("Jean Dupont", 5 + shift_mm, 10, size=12)
    b.text(second, 5 + shift_mm, 20, size=9)
    if rule:
        b.line(5, 25, 45, 25, stroke="#336699")
    b.rect(0, 0, 80, 60, fill="#FFFFFF")
    b.circle(6, 30, 0.5, fill="#000000")
    return b.to_bytes()


def _read(pdf: bytes) -> object:
    with PdfDocument(pdf) as doc:
        return doc.read()


def test_les_mots_dune_ligne_ont_un_centre() -> None:
    line = {"text": "a  bb", "x": 10.0, "w": 30.0, "y": 5.0, "h": 4.0}
    out = line_words(line)
    assert [w["t"] for w in out] == ["a", "bb"]
    assert out[0]["cx"] == pytest.approx(15.0)
    assert out[1]["cy"] == 7.0
    assert line_words({**line, "text": "  "}) == []


class TestLignesHorsDeLeurPlace:
    def test_un_rendu_identique_ne_signale_rien(self) -> None:
        page = _page()
        assert misplaced_lines(_read(page), page) == []  # type: ignore[arg-type]

    def test_un_rendu_decale_signale_chaque_ligne_avec_son_decalage(self) -> None:
        out = misplaced_lines(_read(_page()), _page(3.0))  # type: ignore[arg-type]
        assert [m["line"] for m in out] == ["L1", "L2"]
        assert all(m["dx_mm"] == pytest.approx(3.0, abs=0.1) for m in out)
        assert all(m["dy_mm"] == pytest.approx(0.0, abs=0.1) for m in out)
        assert all(m["note"] == "" for m in out)

    def test_une_ligne_absente_du_rendu_est_dite_absente(self) -> None:
        out = misplaced_lines(_read(_page()), _page(second="Rust Go"))  # type: ignore[arg-type]
        (missing,) = out
        assert missing["line"] == "L2"
        assert missing["note"] == "absente du rendu"
        assert missing["dx_mm"] is None

    def test_un_mot_manquant_sur_deux_est_compte(self) -> None:
        out = misplaced_lines(_read(_page()), _page(second="Python Go"))  # type: ignore[arg-type]
        (partial,) = out
        assert partial["line"] == "L2"
        assert partial["note"] == "1 mot(s) absent(s) du rendu"
        assert partial["dx_mm"] == pytest.approx(0.0, abs=0.1)


class TestFormesManquantes:
    def test_le_filet_present_puis_absent(self) -> None:
        page = _page()
        assert missing_shapes(_read(page), page) == []  # type: ignore[arg-type]
        # Le fond blanc et la puce ne comptent pas ; le filet manque.
        assert missing_shapes(_read(page), _page(rule=False)) == ["D1"]  # type: ignore[arg-type]


def test_une_ligne_vide_et_une_forme_blanche_ne_comptent_pas() -> None:
    from karria.modules.documents.infrastructure.pdf_reader import PdfPage  # noqa: PLC0415

    page = PdfPage(
        width_mm=80,
        height_mm=60,
        pages=1,
        lines=[{"id": "L1", "text": "   ", "x": 1.0, "y": 1.0, "w": 5.0, "h": 3.0}],
        drawings=[
            {"kind": "rect", "x": 5, "y": 5, "w": 10, "h": 10, "fill": "#FFFFFF", "stroke": None},
            {"kind": "rect", "x": 5, "y": 5, "w": 10, "h": 10, "fill": None, "stroke": None},
        ],
        images=[],
    )
    assert misplaced_lines(page, _page()) == []
    assert missing_shapes(page, _page()) == []
