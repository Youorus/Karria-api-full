"""Ce que la mesure dit d'un rendu, ligne par ligne et forme par forme (ADR-0037 § 3).

Contrat
-------
La porte de qualité (`domain/gate.py`) rend des parts (mots à ≤ 2 mm, décor
retrouvé) ; la passe de correction du plan a besoin du **détail** : quelles
lignes de la source sont hors de leur place dans le rendu, de combien, et
quelles formes manquent. Les lignes sont celles que le planificateur a
reçues (mêmes identifiants) ; l'appariement des mots est celui de
`fidelity.structural` (par texte, au plus proche).
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Final

from karria.modules.documents.infrastructure.fidelity import words
from karria.modules.documents.infrastructure.layout_planner import (
    is_page_background,
    is_small_shape,
    shape_id,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument, PdfPage

Json = dict[str, Any]

#: Un mot est à sa place à moins de cette distance (mm), comme dans la porte.
CLOSE_MM: Final = 2.0
#: Une ligne est hors de sa place quand au moins cette part de ses mots l'est.
OFF_SHARE: Final = 0.5
#: Une forme est retrouvée à cette tolérance (mm), comme dans la porte.
SHAPE_TOLERANCE_MM: Final = 1.5


def line_words(line: Json) -> list[Json]:
    """Les mots d'une ligne mesurée, avec leur centre (même découpe que `fidelity.words`)."""
    parts = [p for p in re.split(r"\s+", line["text"]) if p]
    out: list[Json] = []
    if not parts:
        return out
    x0, x1 = line["x"], line["x"] + line["w"]
    total = sum(len(p) for p in parts)
    cur = x0
    for p in parts:
        w = (x1 - x0) * len(p) / max(total, 1)
        out.append({"t": p.lower(), "cx": cur + w / 2, "cy": line["y"] + line["h"] / 2})
        cur += w
    return out


def misplaced_lines(page: PdfPage, render: bytes) -> list[Json]:
    """Les lignes de la source hors de leur place (ou absentes) dans le rendu.

    Pour chaque ligne : le décalage médian de ses mots appariés (`dx_mm`,
    `dy_mm`, positifs vers la droite et le bas), et une note (« absente du
    rendu » quand aucun mot n'est retrouvé). Triées du plus grand décalage au
    plus petit.
    """
    with PdfDocument(render) as doc:
        rendered = words(doc) if doc.page_count else []
    by_text: dict[str, list[Json]] = {}
    for w in rendered:
        by_text.setdefault(w["t"], []).append(w)
    out: list[Json] = []
    for line in page.lines:
        shifts: list[tuple[float, float]] = []
        missing = 0
        for w in line_words(line):
            cands = by_text.get(w["t"])
            if not cands:
                missing += 1
                continue
            best = min(cands, key=lambda c: (c["cx"] - w["cx"]) ** 2 + (c["cy"] - w["cy"]) ** 2)
            shifts.append((best["cx"] - w["cx"], best["cy"] - w["cy"]))
        total = len(shifts) + missing
        if not total:
            continue
        off = sum(1 for dx, dy in shifts if (dx * dx + dy * dy) ** 0.5 > CLOSE_MM) + missing
        if off / total < OFF_SHARE:
            continue
        dx = round(statistics.median(s[0] for s in shifts), 1) if shifts else None
        dy = round(statistics.median(s[1] for s in shifts), 1) if shifts else None
        note = "absente du rendu" if not shifts else ""
        if shifts and missing:
            note = f"{missing} mot(s) absent(s) du rendu"
        out.append(
            {
                "line": line["id"],
                "text": line["text"][:60],
                "dx_mm": dx,
                "dy_mm": dy,
                "note": note,
            }
        )
    out.sort(
        key=lambda m: (
            -(
                ((m["dx_mm"] or 0.0) ** 2 + (m["dy_mm"] or 0.0) ** 2) ** 0.5
                if m["dx_mm"] is not None
                else float("inf")
            )
        )
    )
    return out


def missing_shapes(page: PdfPage, render: bytes) -> list[str]:
    """Les formes de la source (hors fond de page et puces) absentes du rendu, par identifiant."""
    with PdfDocument(render) as doc:
        rendered = doc.drawings(0) if doc.page_count else []
    boxes = [(d["x"], d["y"], d["x"] + d["w"], d["y"] + d["h"]) for d in rendered]
    out: list[str] = []
    for i, d in enumerate(page.drawings, 1):
        if is_page_background(d, page) or is_small_shape(d):
            continue
        if not (d.get("fill") not in (None, "#FFFFFF") or d.get("stroke")):
            continue
        box = (d["x"], d["y"], d["x"] + d["w"], d["y"] + d["h"])
        if not any(all(abs(box[k] - o[k]) <= SHAPE_TOLERANCE_MM for k in range(4)) for o in boxes):
            out.append(shape_id(i))
    return out
