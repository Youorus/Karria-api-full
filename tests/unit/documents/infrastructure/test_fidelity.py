"""Mesure de fidélité : SSIM brut et tolérant, appariement des mots, décor retrouvé."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from documents.infrastructure.fakes import PdfBuilder
from karria.modules.documents.infrastructure.fidelity import (
    _family,
    _gauss1d,
    decoration_recall,
    measure,
    ssim,
    structural,
    tolerant_ssim,
    visible_shapes,
    words,
)

pytestmark = pytest.mark.unit


def _page(shift_mm: float = 0.0, *, rule: bool = True) -> bytes:
    b = PdfBuilder(60, 40)
    b.text("Jean Dupont", 5 + shift_mm, 10, size=12, color="#112233")
    b.text("Python SQL", 5 + shift_mm, 20, size=9)
    if rule:
        b.rect(5, 25, 40, 0.3, fill="#336699")
    return b.to_bytes()


def _checker(size: int = 70) -> np.ndarray:
    img = np.full((size, size), 255.0)
    img[10:40, 10:40] = 0.0
    return img


class TestSsim:
    def test_noyau_gaussien_normalise(self) -> None:
        k = _gauss1d()
        assert len(k) == 11
        assert k.sum() == pytest.approx(1.0)

    def test_deux_images_identiques(self) -> None:
        assert ssim(_checker(), _checker()) == pytest.approx(1.0)

    def test_tailles_differentes_tronquees(self) -> None:
        assert ssim(_checker(70), _checker(80)[:70, :75]) == pytest.approx(1.0)

    def test_le_ssim_tolerant_pardonne_un_decalage(self) -> None:
        a = _checker()
        b = np.roll(a, 2, axis=1)
        assert ssim(a, b) < 0.95
        assert tolerant_ssim(a, b) == pytest.approx(1.0)

    def test_des_tuiles_blanches_des_deux_cotes_sont_ignorees(self) -> None:
        white = np.full((70, 70), 255.0)
        assert tolerant_ssim(white, white) == 1.0


def test_la_famille_dun_nom_de_police() -> None:
    assert _family("ABCDEF+RalewayRoman-Bold") == "raleway"
    assert _family("Open Sans,Bold") == "open"


class _Doc:
    """Une page dont on choisit les lignes et les formes."""

    def __init__(self, lines: list[dict[str, Any]], drawings: list[dict[str, Any]]) -> None:
        self._lines, self._drawings = lines, drawings

    def text_lines(self, index: int = 0) -> list[dict[str, Any]]:
        return self._lines

    def drawings(self, index: int = 0) -> list[dict[str, Any]]:
        return self._drawings

    def page_size_mm(self, index: int = 0) -> tuple[float, float]:
        return 100.0, 100.0


class TestMots:
    def test_chaque_mot_a_son_centre_et_son_style(self) -> None:
        line = {
            "text": "Jean  Dupont",
            "chars": [],
            "x": 10.0,
            "w": 20.0,
            "y": 5.0,
            "h": 4.0,
            "font": "ABCDEF+Testa-Bold",
            "size": 12.04,
            "color": "#000000",
        }
        blank = {**line, "text": "   "}
        out = words(_Doc([line, blank], []))  # type: ignore[arg-type]
        assert [w["t"] for w in out] == ["jean", "dupont"]
        assert out[0]["cx"] == pytest.approx(10 + 20 * 4 / 10 / 2)
        assert out[0]["cy"] == 7.0
        assert out[0]["font"] == "testa"
        assert out[0]["size"] == 12.0


class TestAppariement:
    def _w(self, t: str, cx: float, **kw: Any) -> dict[str, Any]:
        return {"t": t, "cx": cx, "cy": 0.0, "font": "a", "size": 10.0, "color": "#000000", **kw}

    def test_mots_proches_lointains_et_absents(self) -> None:
        src = [self._w("a", 0), self._w("b", 0), self._w("c", 0)]
        ren = [
            self._w("a", 1),
            self._w("a", 9),
            self._w("b", 5, font="z", size=11.0, color="#FFFFFF"),
        ]
        out = structural(src, ren)
        assert out["matched"] == pytest.approx(2 / 3)
        assert out["within_2mm"] == pytest.approx(1 / 3)
        assert out["median_shift_mm"] == 3.0
        assert (out["font"], out["size"], out["color"]) == (0.5, 0.5, 0.5)

    def test_aucun_mot(self) -> None:
        out = structural([], [])
        assert out["matched"] == 0
        assert out["median_shift_mm"] is None


class TestDecor:
    def test_formes_visibles_hors_fond_blanc_et_hors_page(self) -> None:
        d = {"fill": None, "stroke": None, "x": 1.0, "y": 1.0, "w": 2.0, "h": 2.0}
        drawings = [
            {**d, "fill": "#FF0000"},
            {**d, "fill": "#FFFFFF"},
            {**d, "fill": "#000000", "w": 99.0, "h": 99.0},
            {**d, "stroke": "#000000", "y": 150.0},
            {**d, "stroke": "#000000", "y": -10.0},
            {**d, "stroke": "#000000", "x": 5.0},
        ]
        out = visible_shapes(_Doc([], drawings))  # type: ignore[arg-type]
        assert out == [(1.0, 1.0, 3.0, 3.0), (5.0, 1.0, 7.0, 3.0)]

    def test_part_des_formes_retrouvees(self) -> None:
        assert decoration_recall([], []) == 1.0
        src = [(0.0, 0.0, 10.0, 1.0), (20.0, 0.0, 30.0, 1.0)]
        assert decoration_recall(src, [(1.0, 0.5, 11.0, 1.4)]) == 0.5


class TestMesure:
    def test_un_rendu_identique_est_fidele(self) -> None:
        page = _page()
        out = measure(page, page)
        assert out.ssim == pytest.approx(1.0)
        assert out.ssim_tolerant == pytest.approx(1.0)
        assert out.words_matched == 1.0
        assert out.words_within_2mm == 1.0
        assert out.median_shift_mm == 0.0
        assert (out.font_match, out.size_match, out.color_match) == (1.0, 1.0, 1.0)
        assert out.decoration == 1.0
        assert (out.pages_source, out.pages_render) == (1, 1)

    def test_un_rendu_decale_et_sans_filet(self) -> None:
        out = measure(_page(), _page(3.0, rule=False))
        assert out.ssim < 0.99
        assert out.words_matched == 1.0
        assert out.words_within_2mm == 0.0
        assert out.median_shift_mm == pytest.approx(3.0, abs=0.05)
        assert out.decoration == 0.0
