"""La couleur d'un modèle, changée par la personne : le décor change, le texte reste.

Contrat (ADR-0034, addendum du 2026-11-06)
-------
- **La couleur du modèle est celle de son décor**, mesurée : la couleur
  dominante — par surface peinte, à égalité par occurrences — des bandes,
  filets, pastilles, puces dessinées et séparateurs (`fill`, `stroke`,
  `background`, `marker.color`), hors blanc du papier et hors couleurs très
  claires qui ne sont qu'un fond (une bande pâle suit, elle ne définit pas).
  Sans décor coloré, la couleur des filets et des puces (souvent noir) est
  celle du décor. Les titres de rubrique ne la définissent plus : ce sont des
  textes.
- **`recolor_spec(spec, primary)` ne touche que le décor** : les occurrences
  exactes de la couleur du décor prennent la couleur choisie ; ses **teintes
  dérivées** (même teinte, autre clarté : la bande pâle d'une colonne, un filet
  discret) sont déclinées depuis la nouvelle couleur à la **même position
  entre elle et le blanc** (ou le noir) — la bande reste pâle, le filet reste
  discret. Un décor gris (noir compris) suit en entier, gris par gris ; un
  gris dans un décor coloré reste gris, une couleur dans un décor gris reste.
  Aucune clé `color` d'un style de texte ne change…
- **… sauf pour rester lisible** : un texte posé **sur** une surface
  recolorée (bande d'en-tête, fond de page ou de colonne, barre de titre) dont
  le contraste avec la nouvelle couleur passe sous 4,5:1 (WCAG, luminance
  relative) prend le blanc ou le noir profond, celui qui se lit le mieux sur
  **toutes** les surfaces où ce style est posé. « Posé sur » se lit dans la
  géométrie de la spécification : un élément d'en-tête dans le rectangle
  d'une décoration pleine, une colonne couverte par une décoration de page ou
  dotée d'un `background`, une rubrique derrière une barre pleine ancrée.
- Les **pictogrammes** rastérisés ne sont pas dans la spécification :
  `tinted_ink` dit, pour l'encre mesurée d'un pictogramme monochrome, la
  couleur qu'il prend (la mesure et la teinte sont faites par l'application
  avec le socle image, ADR-0009).

La transformation est pure et non destructive, à la manière d'`ats_safe_spec`
(`headline.py`) : l'original n'est jamais modifié. Le code couleur accepté est
`#rrggbb` (ou `rrggbb`, `#rgb`), normalisé en `#RRGGBB`.
"""

from __future__ import annotations

import colorsys
import re
from collections.abc import Iterator
from typing import Any, Final

#: Les clés d'une spécification qui portent une couleur de décor. `color`
#: n'en fait partie que sous `marker` (la puce dessinée) : partout ailleurs
#: c'est la couleur d'un texte.
DECOR_KEYS: Final = frozenset({"fill", "stroke", "background"})
#: Sous cette saturation, une couleur est un gris.
GRAY_SATURATION: Final = 0.12
#: Deux teintes à moins de cet écart (en degrés) sont « la même ».
HUE_TOLERANCE_DEG: Final = 10.0
#: À partir de cette clarté, une couleur de décor n'est qu'un fond : elle
#: suit la couleur du décor comme teinte dérivée, elle ne la définit pas.
BACKGROUND_LIGHTNESS: Final = 0.85
#: Contraste minimal d'un texte sur une surface (WCAG AA, texte courant).
MIN_CONTRAST: Final = 4.5
#: Sous le minimum partout, un texte ne change que si son pire contraste
#: fait au moins ce gain : un style partagé entre une bande et le papier
#: garde sa couleur plutôt que d'en perdre un côté pour presque rien.
CLEAR_GAIN: Final = 2.0
#: Ce qu'un texte devient quand il ne se lit plus sur sa surface recolorée.
TEXT_ON_DARK: Final = "#FFFFFF"
TEXT_ON_LIGHT: Final = "#111111"
#: Le blanc du papier.
PAPER: Final = "#FFFFFF"
#: Épaisseur par défaut d'un filet (pt), la même que dans les moteurs.
DEFAULT_STROKE_PT: Final = 0.75
MM_PER_PT: Final = 25.4 / 72
#: Écart par canal (0–255) sous lequel l'encre mesurée d'un pictogramme est
#: « la même » couleur que celle du décor (anticrénelage, arrondis).
INK_TOLERANCE: Final = 8
#: Une surface pleine plus petite que ceci (mm²) ne porte pas de texte.
MIN_SURFACE_MM2: Final = 20.0
#: Une surface de rubrique (barre de titre, fond d'élément) a au moins cette taille.
MIN_BAR_W_MM: Final = 8.0
MIN_BAR_H_MM: Final = 2.5
#: Une barre de titre commence au plus à cette distance sous le haut du titre (mm)
#: et en couvre au moins cette part.
BAR_TOP_TOLERANCE_MM: Final = 1.0
BAR_COVER: Final = 0.6
#: Une surface couvre une colonne quand elle en recouvre ces parts de largeur et de hauteur.
COVER_WIDTH: Final = 0.9
COVER_HEIGHT: Final = 0.8
#: Tolérance du test « ce point est dans ce rectangle » (mm).
POINT_TOLERANCE_MM: Final = 0.3
_HEX: Final = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

Json = dict[str, Any]


def parse_hex_color(value: str) -> str:
    """`#1a2b3c`, `1a2b3c` ou `#abc` → `#1A2B3C`.

    Raises:
        ValueError: Ce n'est pas un code couleur hexadécimal.
    """
    match = _HEX.match(value.strip())
    if match is None:
        msg = "Un code couleur s'écrit #rrggbb."
        raise ValueError(msg)
    digits = match.group(1)
    if len(digits) == 3:  # noqa: PLR2004 - forme courte
        digits = "".join(c * 2 for c in digits)
    return f"#{digits.upper()}"


# ---------------------------------------------------------------------- couleurs


def _rgb(color: str) -> tuple[float, float, float]:
    digits = color.lstrip("#")
    return tuple(int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(round(min(1.0, max(0.0, v)) * 255) for v in (r, g, b)))


def _hls(color: str) -> tuple[float, float, float]:
    """(teinte en degrés, clarté, saturation)."""
    hue, lightness, saturation = colorsys.rgb_to_hls(*_rgb(color))
    return hue * 360.0, lightness, saturation


def _is_hex(value: Any) -> bool:
    return isinstance(value, str) and _HEX.match(value) is not None and len(value) == 7  # noqa: PLR2004


def _same_hue(a: float, b: float) -> bool:
    delta = abs(a - b) % 360.0
    return min(delta, 360.0 - delta) <= HUE_TOLERANCE_DEG


def _shift_lightness(lightness: float, origin: float, primary: float) -> float:
    """La clarté qui est, par rapport à `primary`, ce que `lightness` est à `origin`.

    Même position entre la couleur et le blanc (plus clair) ou le noir (plus
    sombre) : une bande pâle reste pâle quelle que soit la clarté de la
    nouvelle couleur, sans jamais s'effacer dans le blanc.
    """
    if lightness >= origin:
        return 1.0 - (1.0 - primary) * (1.0 - lightness) / max(1.0 - origin, 1e-9)
    return primary * lightness / max(origin, 1e-9)


def relative_luminance(color: str) -> float:
    """La luminance relative d'une couleur (WCAG 2)."""

    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4  # noqa: PLR2004

    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """Le rapport de contraste WCAG entre deux couleurs (1 à 21)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def derived_color(color: str, origin: str, primary: str) -> str | None:
    """La couleur qui correspond à `color` une fois `origin` remplacée par `primary`.

    Exacte : `primary`. Teinte dérivée (même teinte qu'`origin`, ou gris si
    `origin` est un gris) : `primary` à la même position de clarté, saturée
    dans la même proportion. Sinon `None` : ce n'est pas la couleur du décor,
    elle reste.
    """
    if color.upper() == origin:
        return primary
    oh, ol, os_ = _hls(origin)
    ch, cl, cs = _hls(color)
    origin_gray, color_gray = os_ < GRAY_SATURATION, cs < GRAY_SATURATION
    if origin_gray != color_gray or (not origin_gray and not _same_hue(ch, oh)):
        return None
    ph, pl, ps = _hls(primary)
    lightness = min(1.0, max(0.0, _shift_lightness(cl, ol, pl)))
    if ps < GRAY_SATURATION:
        # La nouvelle couleur est un gris : ses dérivées sont des gris.
        return _hex(*colorsys.hls_to_rgb(0.0, lightness, 0.0))
    saturation = ps if origin_gray else min(1.0, max(0.0, ps * (cs / os_)))
    return _hex(*colorsys.hls_to_rgb(ph / 360.0, lightness, saturation))


def tinted_ink(ink: str, origin: str, primary: str) -> str | None:
    """La couleur que prend l'encre d'un pictogramme monochrome, ou `None` s'il reste tel quel.

    L'encre est **mesurée** sur un PNG (anticrénelage, arrondis) : elle vaut
    la couleur du décor à `INK_TOLERANCE` près par canal, sinon elle suit
    comme une teinte dérivée, sinon elle n'est pas la couleur du décor.
    """
    a, b = _rgb(ink), _rgb(origin)
    if max(abs(x - y) for x, y in zip(a, b, strict=True)) * 255 <= INK_TOLERANCE:
        return primary
    return derived_color(ink, origin, primary)


# ---------------------------------------------------------------------- la couleur du décor


def _decor_shape(d: Json) -> Iterator[tuple[str, float]]:
    """(couleur, surface peinte en mm²) d'une décoration : filet, pastille, cadre, bande."""
    w, h = float(d.get("w_mm") or 0.0), float(d.get("h_mm") or 0.0)
    thickness = float(d.get("stroke_pt") or DEFAULT_STROKE_PT) * MM_PER_PT
    fill, stroke = d.get("fill"), d.get("stroke")
    if d.get("kind") in ("vline", "hline"):
        color = stroke or fill
        if _is_hex(color):
            yield str(color).upper(), max(w, h) * thickness
        return
    if _is_hex(fill):
        yield str(fill).upper(), w * h
    if _is_hex(stroke):
        yield str(stroke).upper(), 2 * (w + h) * thickness


def decor_colors(spec: Json) -> list[tuple[str, float]]:
    """Chaque élément de décor de la spécification : (couleur, surface peinte en mm²)."""
    found: list[tuple[str, float]] = []
    header: Json = spec.get("header") or {}
    for d in header.get("decorations") or []:
        found.extend(_decor_shape(d))
    for d in spec.get("page_decorations") or []:
        found.extend(_decor_shape(d))
    for band in spec.get("bands") or []:
        top = float(band.get("y_sample") or 0.0)
        height = max(float(band.get("bottom_sample") or top) - top, 1.0)
        for col in band.get("columns") or []:
            if _is_hex(col.get("background")):
                found.append((str(col["background"]).upper(), float(col.get("w_mm") or 0) * height))
            for s in col.get("sections") or []:
                marker = s.get("marker") or {}
                if _is_hex(marker.get("color")):
                    size = float(marker.get("size_mm") or 0.0)
                    found.append((str(marker["color"]).upper(), size * size))
                for d in s.get("decorations") or []:
                    found.extend(_decor_shape(d))
                for slot in s.get("slots") or []:
                    sep = slot.get("separator") or {}
                    if _is_hex(sep.get("stroke")):
                        thickness = float(sep.get("stroke_pt") or DEFAULT_STROKE_PT) * MM_PER_PT
                        found.append(
                            (str(sep["stroke"]).upper(), float(sep.get("h_mm") or 0) * thickness)
                        )
    return found


def primary_color_of(spec: Json) -> str | None:
    """La couleur du modèle : celle de son décor, dominante par surface puis par occurrences.

    Le blanc du papier ne compte pas ; une couleur très claire (clarté ≥ 0,85)
    n'est qu'un fond et ne compte que si rien d'autre ne colore le décor.
    `None` quand le modèle n'a aucun décor.
    """
    paper = str((spec.get("page") or {}).get("background") or PAPER).upper()
    area: dict[str, float] = {}
    count: dict[str, int] = {}
    for color, mm2 in decor_colors(spec):
        if color in (paper, PAPER):
            continue
        area[color] = area.get(color, 0.0) + mm2
        count[color] = count.get(color, 0) + 1
    if not area:
        return None
    inks = [c for c in area if _hls(c)[1] < BACKGROUND_LIGHTNESS] or list(area)
    return max(inks, key=lambda c: (round(area[c], 3), count[c]))


# ---------------------------------------------------------------------- recoloration du décor


def _walk(node: Any, origin: str, primary: str, parent: str | None = None) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            decor = key in DECOR_KEYS or (key == "color" and parent == "marker")
            if decor and _is_hex(value):
                out[key] = derived_color(value, origin, primary) or value
            else:
                out[key] = _walk(value, origin, primary, key)
        return out
    if isinstance(node, list):
        return [_walk(item, origin, primary, parent) for item in node]
    return node


def recolor_spec(spec: Json, primary: str | None) -> Json:
    """La spécification avec la couleur du décor remplacée par `primary` (`#RRGGBB`).

    `None`, ou la couleur d'origine, ou un modèle sans décor : la
    spécification telle quelle (le même objet). Sinon une copie ; l'original
    n'est pas modifié. Le texte ne change que pour rester lisible sur une
    surface recolorée (`legible_styles`).
    """
    if primary is None:
        return spec
    origin = primary_color_of(spec)
    if origin is None:
        return spec
    chosen = parse_hex_color(primary)
    if chosen == origin:
        return spec
    recolored: Json = _walk(spec, origin, chosen)
    if "styles" in recolored:
        recolored["styles"] = legible_styles(spec, recolored)
    return recolored


# ---------------------------------------------------------------------- lisibilité


def _surface_rect(d: Json) -> tuple[float, float, float, float] | None:
    """Le rectangle (x, y, w, h) d'une décoration pleine assez grande pour porter un texte."""
    if d.get("kind") not in ("rect", "curve") or not _is_hex(d.get("fill")):
        return None
    w, h = float(d.get("w_mm") or 0.0), float(d.get("h_mm") or 0.0)
    if w * h < MIN_SURFACE_MM2:
        return None
    return float(d.get("x_mm") or 0.0), float(d.get("y_mm") or 0.0), w, h


def _page_surfaces(
    before: Json, after: Json
) -> list[tuple[tuple[float, float, float, float], str, bool]]:
    """Les surfaces pleines de la page, dans l'ordre de peinture : (rectangle, couleur, recolorée)."""
    pairs: list[tuple[Json, Json]] = []
    pairs.extend(
        zip(before.get("page_decorations") or [], after.get("page_decorations") or [], strict=True)
    )
    header_before: Json = before.get("header") or {}
    header_after: Json = after.get("header") or {}
    pairs.extend(
        zip(
            header_before.get("decorations") or [],
            header_after.get("decorations") or [],
            strict=True,
        )
    )
    out = []
    for old, new in pairs:
        if old.get("anchor") not in (None, "page"):
            continue
        rect = _surface_rect(new)
        if rect is not None:
            out.append((rect, str(new["fill"]).upper(), new["fill"] != old.get("fill")))
    return out


def _topmost(
    surfaces: list[tuple[tuple[float, float, float, float], str, bool]],
    x: float,
    y: float,
) -> tuple[str, bool] | None:
    found = None
    tol = POINT_TOLERANCE_MM
    for (rx, ry, rw, rh), color, recolored in surfaces:
        if rx - tol <= x <= rx + rw + tol and ry - tol <= y <= ry + rh + tol:
            found = (color, recolored)
    return found


def _covering(
    surfaces: list[tuple[tuple[float, float, float, float], str, bool]],
    x: float,
    y: float,
    w: float,
    h: float,
) -> tuple[str, bool] | None:
    """La surface qui couvre un rectangle (90 % de sa largeur, 80 % de sa hauteur), la dernière peinte."""
    found = None
    for (rx, ry, rw, rh), color, recolored in surfaces:
        dx = min(x + w, rx + rw) - max(x, rx)
        dy = min(y + h, ry + rh) - max(y, ry)
        if dx >= COVER_WIDTH * w and dy >= COVER_HEIGHT * h:
            found = (color, recolored)
    return found


def _occurrences(before: Json, after: Json) -> dict[str, list[tuple[str, bool]]]:
    """Où chaque style de texte est posé : (couleur du fond, fond recoloré), occurrence par occurrence."""
    styles: dict[str, Json] = after.get("styles") or {}
    surfaces = _page_surfaces(before, after)
    paper = str((after.get("page") or {}).get("background") or PAPER).upper()
    found: dict[str, list[tuple[str, bool]]] = {}

    def add(key: str, background: tuple[str, bool] | None) -> None:
        if key in styles:
            found.setdefault(key, []).append(background or (paper, False))

    header: Json = after.get("header") or {}
    for e in header.get("elements") or []:
        st = styles.get(str(e.get("style")))
        if st is None:
            continue
        centered = e.get("align") == "center" and e.get("cx_mm") is not None
        x = float(e["cx_mm"] if centered else e.get("x_mm") or 0.0)
        y = float(e.get("y_mm") or 0.0) + float(st.get("box_h_mm") or 0.0) / 2
        add(str(e["style"]), _topmost(surfaces, x, y))
    bands_before = before.get("bands") or []
    for bi, band in enumerate(after.get("bands") or []):
        top = float(band.get("y_sample") or before.get("first_band_top_mm") or 0.0)
        bottom = float(band.get("bottom_sample") or before.get("content_bottom_mm") or top + 1.0)
        for ci, col in enumerate(band.get("columns") or []):
            cx, cw = float(col.get("x_mm") or 0.0), float(col.get("w_mm") or 0.0)
            background = _covering(surfaces, cx, top, cw, max(bottom - top, 1.0))
            if _is_hex(col.get("background")):
                old = bands_before[bi]["columns"][ci].get("background")
                background = (str(col["background"]).upper(), col["background"] != old)
            for si, s in enumerate(col.get("sections") or []):
                kind = str(s.get("kind"))
                keys = ["section_title", *(k for k in styles if k.startswith(kind + "."))]
                bars = _section_bars(
                    bands_before[bi]["columns"][ci]["sections"][si], s, styles, kind
                )
                for key in keys:
                    add(key, bars.get(key, background))
    return found


def _section_bars(
    old: Json, new: Json, styles: dict[str, Json], kind: str
) -> dict[str, tuple[str, bool]]:
    """Les barres pleines d'une rubrique et le style qu'elles portent (titre, ligne, élément)."""
    out: dict[str, tuple[str, bool]] = {}
    title_h = float((styles.get("section_title") or {}).get("box_h_mm") or 0.0)
    for d_old, d in zip(old.get("decorations") or [], new.get("decorations") or [], strict=True):
        if d.get("kind") not in ("rect", "curve") or not _is_hex(d.get("fill")):
            continue
        w, h = float(d.get("w_mm") or 0.0), float(d.get("h_mm") or 0.0)
        if w < MIN_BAR_W_MM or h < MIN_BAR_H_MM:
            continue
        background = (str(d["fill"]).upper(), d["fill"] != d_old.get("fill"))
        dy = float(d.get("dy_mm") or 0.0)
        if d.get("anchor") == "section":
            if dy <= BAR_TOP_TOLERANCE_MM and dy + h >= BAR_COVER * title_h:
                out["section_title"] = background
        elif d.get("ref_role"):
            out[f"{kind}.{d['ref_role']}"] = background
        elif d.get("anchor") == "item":
            for key in styles:
                if key.startswith(kind + "."):
                    out[key] = background
    return out


def legible_styles(before: Json, after: Json) -> dict[str, Json]:
    """Les styles de texte d'`after`, corrigés là où une surface recolorée les rend illisibles.

    Un style posé sur une surface recolorée avec moins de 4,5:1 de contraste
    prend, du blanc et du noir profond, celui qui se lit le mieux sur toutes
    les surfaces où il est posé. Les autres styles sont rendus tels quels.
    """
    styles: dict[str, Json] = dict(after.get("styles") or {})
    for key, places in _occurrences(before, after).items():
        color = styles[key].get("color")
        if not _is_hex(color):
            continue
        if not any(
            recolored and contrast_ratio(str(color), bg) < MIN_CONTRAST for bg, recolored in places
        ):
            continue

        backgrounds = [bg for bg, _ in places]
        best = max((TEXT_ON_DARK, TEXT_ON_LIGHT), key=lambda c: _worst(c, backgrounds))
        # Un style posé à la fois sur une bande et sur le papier ne peut pas
        # toujours être sauvé : on ne change que si ça lit nettement mieux partout.
        gain = _worst(best, backgrounds)
        if gain >= MIN_CONTRAST or gain >= CLEAR_GAIN * _worst(str(color), backgrounds):
            styles[key] = {**styles[key], "color": best}
    return styles


def _worst(color: str, backgrounds: list[str]) -> float:
    """Le pire contraste d'une couleur de texte sur ses fonds."""
    return min(contrast_ratio(color, bg) for bg in backgrounds)
