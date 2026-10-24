"""Porte de qualité d'un modèle : score composite, planchers et pages en trop."""

from __future__ import annotations

from dataclasses import replace

import pytest

from karria.modules.documents.domain.gate import Fidelity, GateDecision, evaluate

pytestmark = pytest.mark.unit


def _fidelity(**changes: object) -> Fidelity:
    base = Fidelity(
        ssim=0.97,
        ssim_tolerant=0.95,
        words_within_2mm=0.96,
        words_matched=0.99,
        median_shift_mm=0.4,
        font_match=1.0,
        size_match=0.97,
        color_match=0.97,
        decoration=0.9,
        pages_source=1,
        pages_render=1,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_le_style_est_la_moyenne_police_corps_couleur() -> None:
    assert _fidelity(font_match=0.9, size_match=0.6, color_match=0.9).style == pytest.approx(0.8)


def test_le_score_pondere_les_quatre_composantes() -> None:
    fid = _fidelity(
        ssim_tolerant=1.0,
        words_within_2mm=0.8,
        font_match=0.6,
        size_match=0.6,
        color_match=0.6,
        decoration=0.4,
    )
    assert fid.score == round(0.45 * 1.0 + 0.25 * 0.8 + 0.15 * 0.6 + 0.15 * 0.4, 4)


def test_les_composantes_sont_arrondies_a_quatre_decimales() -> None:
    comps = _fidelity(ssim_tolerant=0.912345).components()
    assert comps["ssim_tolerant"] == 0.9123
    assert set(comps) == {"ssim_tolerant", "words_within_2mm", "style", "decoration"}


def test_la_forme_json_inclut_les_mesures_et_l_extra() -> None:
    data = _fidelity(extra={"tiles": 12}).to_json()
    assert data["score"] == _fidelity().score
    assert data["components"] == _fidelity().components()
    assert data["median_shift_mm"] == 0.4
    assert data["pages_source"] == 1
    assert data["pages_render"] == 1
    assert data["tiles"] == 12


def test_un_rendu_fidele_passe_la_porte() -> None:
    decision = evaluate(_fidelity())
    assert decision == GateDecision(passed=True, failures=())


def test_un_plancher_manque_suffit_a_refuser_meme_avec_un_bon_score() -> None:
    decision = evaluate(_fidelity(decoration=0.7))
    assert not decision.passed
    assert decision.failures == ("decoration 0.700 < 0.75",)


def test_un_score_trop_bas_est_signale_avec_chaque_plancher_manque() -> None:
    decision = evaluate(
        _fidelity(
            ssim_tolerant=0.5,
            words_within_2mm=0.5,
            font_match=0.5,
            size_match=0.5,
            color_match=0.5,
            decoration=0.5,
        )
    )
    assert decision.failures[0].startswith("score 0.500 < 0.90")
    assert len(decision.failures) == 5


def test_un_rendu_plus_long_que_la_source_est_refuse() -> None:
    decision = evaluate(_fidelity(pages_render=2))
    assert decision.failures == ("2 pages rendues pour 1",)


def test_la_decision_se_serialise() -> None:
    assert GateDecision(passed=False, failures=("x",)).to_json() == {
        "passed": False,
        "failures": ["x"],
    }
