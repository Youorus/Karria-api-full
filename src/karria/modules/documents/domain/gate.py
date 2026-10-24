"""La porte de qualité d'un modèle : la fidélité se mesure, avec un seuil (ADR-0020 § 4).

Contrat
-------
Le modèle est rendu **avec son propre contenu d'exemple** et comparé page à
page au PDF source. Quatre composantes, chacune avec un plancher :

| Composante | Ce qu'elle mesure | Plancher PDF |
|---|---|---|
| SSIM tolérant | le dessin, à ±1 mm de recalage par tuile | 0,88 |
| Mots à ≤ 2 mm | la place de chaque mot | 0,90 |
| Style | police, corps, couleur des mots appariés | 0,95 |
| Décor | filets, pastilles, cadres retrouvés à ±1,5 mm | 0,75 |

Score = 0,45 × SSIM tolérant + 0,25 × mots + 0,15 × style + 0,15 × décor ;
**PDF : score ≥ 0,90 et chaque plancher**. Le DOCX est mesuré (aperçu
LibreOffice) et rapporté, avec un seuil informatif de 0,80 : il ne bloque
pas la publication. Un modèle sous le seuil n'est **pas publié** : il part en
revue avec ses mesures et ses substitutions de polices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

PDF_SCORE_MIN: Final = 0.90
DOCX_SCORE_TARGET: Final = 0.80
FLOORS: Final[dict[str, float]] = {
    "ssim_tolerant": 0.88,
    "words_within_2mm": 0.90,
    "style": 0.95,
    "decoration": 0.75,
}
WEIGHTS: Final[dict[str, float]] = {
    "ssim_tolerant": 0.45,
    "words_within_2mm": 0.25,
    "style": 0.15,
    "decoration": 0.15,
}


@dataclass(frozen=True, slots=True)
class Fidelity:
    """Les mesures d'un rendu comparé à la source (première page)."""

    ssim: float
    ssim_tolerant: float
    words_within_2mm: float
    words_matched: float
    median_shift_mm: float | None
    font_match: float
    size_match: float
    color_match: float
    decoration: float
    pages_source: int
    pages_render: int
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def style(self) -> float:
        """Concordance de style : moyenne police, corps, couleur."""
        return (self.font_match + self.size_match + self.color_match) / 3

    @property
    def score(self) -> float:
        """Le score composite."""
        parts = {
            "ssim_tolerant": self.ssim_tolerant,
            "words_within_2mm": self.words_within_2mm,
            "style": self.style,
            "decoration": self.decoration,
        }
        return round(sum(WEIGHTS[k] * v for k, v in parts.items()), 4)

    def components(self) -> dict[str, float]:
        """Les composantes notées par la porte."""
        return {
            "ssim_tolerant": round(self.ssim_tolerant, 4),
            "words_within_2mm": round(self.words_within_2mm, 4),
            "style": round(self.style, 4),
            "decoration": round(self.decoration, 4),
        }

    def to_json(self) -> dict[str, Any]:
        """Forme gardée avec la version du modèle."""
        return {
            "score": self.score,
            "components": self.components(),
            "ssim": round(self.ssim, 4),
            "words_matched": round(self.words_matched, 4),
            "median_shift_mm": self.median_shift_mm,
            "font_match": round(self.font_match, 4),
            "size_match": round(self.size_match, 4),
            "color_match": round(self.color_match, 4),
            "pages_source": self.pages_source,
            "pages_render": self.pages_render,
            **self.extra,
        }


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Le verdict de la porte : publier, ou envoyer en revue avec les raisons."""

    passed: bool
    failures: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        """Forme gardée avec la version du modèle."""
        return {"passed": self.passed, "failures": list(self.failures)}


def evaluate(pdf: Fidelity) -> GateDecision:
    """Applique les seuils PDF : le score **et** chaque plancher."""
    failures: list[str] = []
    if pdf.score < PDF_SCORE_MIN:
        failures.append(f"score {pdf.score:.3f} < {PDF_SCORE_MIN:.2f}")
    for key, floor in FLOORS.items():
        value = pdf.components()[key]
        if value < floor:
            failures.append(f"{key} {value:.3f} < {floor:.2f}")
    if pdf.pages_render > pdf.pages_source:
        failures.append(f"{pdf.pages_render} pages rendues pour {pdf.pages_source}")
    return GateDecision(passed=not failures, failures=tuple(failures))
