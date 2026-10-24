"""Mesure de fidélité d'un rendu par rapport au PDF source (numpy, pdfium, pdfplumber).

Contrat
-------
- Visuel : chaque page rastérisée à 72 dpi en niveaux de gris ; SSIM (fenêtre
  gaussienne 11, σ 1,5, Wang 2004) brut, et **tolérant** : SSIM par tuiles de
  ~22 mm, chaque tuile au meilleur recalage dans ±1 mm, tuiles blanches des
  deux côtés ignorées. Le SSIM brut d'une page de texte tombe à 0,82 pour un
  décalage d'un pixel : il mesure la gigue, pas le dessin.
- Structure : les mots (texte + boîte) appariés source ↔ rendu ; part à ≤ 2 mm,
  décalage médian, concordance des familles, corps et couleurs.
- Décor : part des formes visibles de la source retrouvées à ±1,5 mm.
- Le calcul est CPU (≈ 1 à 3 s par page) : il ne tourne qu'à l'ingestion et
  dans le worker, jamais sur le chemin d'une requête.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from karria.modules.documents.domain.gate import Fidelity
from karria.modules.documents.infrastructure.pdf_reader import (
    PdfDocument,
    base_font_name,
    hex_color,
)

MM: Final = 72 / 25.4
Gray = NDArray[np.float64]


def _gauss1d(size: int = 11, sigma: float = 1.5) -> Gray:
    x = np.arange(size) - size // 2
    g = np.exp(-(x**2) / (2 * sigma**2))
    out: Gray = g / g.sum()
    return out


def _blur(img: Gray, k: Gray) -> Gray:
    pad = len(k) // 2
    tmp = np.pad(img, ((0, 0), (pad, pad)), mode="reflect")
    tmp = np.apply_along_axis(lambda r: np.convolve(r, k, mode="valid"), 1, tmp)
    tmp = np.pad(tmp, ((pad, pad), (0, 0)), mode="reflect")
    out: Gray = np.apply_along_axis(lambda c: np.convolve(c, k, mode="valid"), 0, tmp)
    return out


def ssim(a: Gray, b: Gray) -> float:
    """SSIM moyen de deux images de même taille (tronquées au plus petit)."""
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    k = _gauss1d()
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = _blur(a, k), _blur(b, k)
    s_aa = _blur(a * a, k) - mu_a**2
    s_bb = _blur(b * b, k) - mu_b**2
    s_ab = _blur(a * b, k) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * s_ab + c2)
    den = (mu_a**2 + mu_b**2 + c1) * (s_aa + s_bb + c2)
    return float(np.mean(num / den))


def tolerant_ssim(a: Gray, b: Gray, tile: int = 64, radius: int = 3) -> float:
    """SSIM par tuiles, chaque tuile au meilleur recalage dans ±`radius` px."""
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    pad = np.pad(b, radius, mode="edge")
    scores: list[float] = []
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            ta = a[y : y + tile, x : x + tile]
            if ta.min() > 245 and b[y : y + tile, x : x + tile].min() > 245:  # noqa: PLR2004
                continue
            best = -1.0
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    tb = pad[
                        y + radius + dy : y + radius + dy + tile,
                        x + radius + dx : x + radius + dx + tile,
                    ]
                    best = max(best, ssim(ta, tb))
            scores.append(best)
    return float(np.mean(scores)) if scores else 1.0


def _family(font: str) -> str:
    f = re.split(r"[-,_ ]", base_font_name(font))[0]
    # LibreOffice nomme l'instance par défaut d'une police variable « <Famille>Roman ».
    return re.sub(r"roman$", "", f.lower())


def words(doc: PdfDocument, index: int = 0) -> list[dict[str, Any]]:
    """Les mots d'une page, avec leur centre (mm) et leur style."""
    out: list[dict[str, Any]] = []
    for line in doc.text_lines(index):
        glyphs = line["chars"]
        text = line["text"]
        parts = [p for p in re.split(r"\s+", text) if p]
        if not parts:
            continue
        x0, x1 = line["x"], line["x"] + line["w"]
        total = sum(len(p) for p in parts)
        cur = x0
        for p in parts:
            w = (x1 - x0) * len(p) / max(total, 1)
            out.append(
                {
                    "t": p.lower(),
                    "cx": cur + w / 2,
                    "cy": line["y"] + line["h"] / 2,
                    "font": _family(line["font"]),
                    "size": round(float(line["size"]), 1),
                    "color": line["color"],
                }
            )
            cur += w
        del glyphs
    return out


def structural(src: list[dict[str, Any]], ren: list[dict[str, Any]]) -> dict[str, Any]:
    """Appariement des mots source ↔ rendu."""
    by_text: dict[str, list[dict[str, Any]]] = {}
    for w in ren:
        by_text.setdefault(w["t"], []).append(w)
    matched = close = font_ok = size_ok = color_ok = 0
    dists: list[float] = []
    for w in src:
        cands = by_text.get(w["t"])
        if not cands:
            continue
        best = min(cands, key=lambda c: (c["cx"] - w["cx"]) ** 2 + (c["cy"] - w["cy"]) ** 2)
        d = ((best["cx"] - w["cx"]) ** 2 + (best["cy"] - w["cy"]) ** 2) ** 0.5
        matched += 1
        dists.append(d)
        close += d <= 2.0  # noqa: PLR2004
        font_ok += best["font"] == w["font"]
        size_ok += abs(best["size"] - w["size"]) <= 0.6  # noqa: PLR2004
        color_ok += best["color"] == w["color"]
    n = max(len(src), 1)
    m = max(matched, 1)
    return {
        "matched": matched / n,
        "within_2mm": close / n,
        "median_shift_mm": round(statistics.median(dists), 2) if dists else None,
        "font": font_ok / m,
        "size": size_ok / m,
        "color": color_ok / m,
    }


def visible_shapes(doc: PdfDocument, index: int = 0) -> list[tuple[float, float, float, float]]:
    """Formes visibles (x0, y0, x1, y1 en mm), hors fond de page et blanc pur."""
    width, height = doc.page_size_mm(index)
    out = []
    for d in doc.drawings(index):
        fill = d.get("fill") if d.get("fill") not in (None, "#FFFFFF") else None
        if not (fill or d.get("stroke")):
            continue
        if d["w"] > width * 0.95 and d["h"] > height * 0.95:
            continue
        if d["y"] > height or d["y"] + d["h"] < 0:
            continue
        out.append((d["x"], d["y"], d["x"] + d["w"], d["y"] + d["h"]))
    return out


def decoration_recall(
    src: list[tuple[float, float, float, float]],
    ren: list[tuple[float, float, float, float]],
    tol_mm: float = 1.5,
) -> float:
    """Part des formes de la source retrouvées dans le rendu (boîte à ±1,5 mm près)."""
    if not src:
        return 1.0
    found = sum(
        1 for r in src if any(all(abs(r[i] - o[i]) <= tol_mm for i in range(4)) for o in ren)
    )
    return found / len(src)


def measure(source: bytes, render: bytes, *, dpi: int = 72) -> Fidelity:
    """Compare la première page d'un rendu à celle de la source."""
    with PdfDocument(source) as s, PdfDocument(render) as r:
        a = s.render_gray(0, dpi=dpi).astype(np.float64)
        b = r.render_gray(0, dpi=dpi).astype(np.float64)
        st = structural(words(s), words(r))
        return Fidelity(
            ssim=ssim(a, b),
            ssim_tolerant=tolerant_ssim(a, b),
            words_within_2mm=st["within_2mm"],
            words_matched=st["matched"],
            median_shift_mm=st["median_shift_mm"],
            font_match=st["font"],
            size_match=st["size"],
            color_match=st["color"],
            decoration=decoration_recall(visible_shapes(s), visible_shapes(r)),
            pages_source=s.page_count,
            pages_render=r.page_count,
        )


__all__ = ["hex_color", "measure"]
