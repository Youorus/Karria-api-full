"""La couleur principale d'un modèle, changée par la personne — sans toucher au reste.

Contrat
-------
Un modèle a **une** couleur principale : celle de ses titres de rubrique
(`styles.section_title.color`, ce que la galerie mesure et affiche). La
personne peut en poser une autre depuis l'atelier. La transformation est
**pure et non destructive**, à la manière d'`ats_safe_spec` (`headline.py`) :

- toute occurrence de **cette même couleur** (styles, puce, filets, bandes,
  décorations d'en-tête et de page) prend la couleur choisie ;
- ses **teintes dérivées** (même teinte, autre clarté : la bande claire d'une
  colonne latérale, un filet pâle) sont déclinées depuis la nouvelle couleur
  avec le **même écart de clarté** et la même proportion de saturation — la
  bande reste pâle, le filet reste discret ;
- les autres couleurs (texte noir, gris, blanc du papier) ne bougent pas :
  elles ne sont pas « la couleur du modèle », elles sont le texte.

Un modèle dont la couleur principale est un gris ou un noir n'a pas de
teinte à suivre : seules les occurrences exactes changent. Les pictogrammes
de contact, rastérisés depuis le PDF du modèle, gardent leur couleur
d'origine (limite connue, notée dans l'ADR).

Le code couleur accepté est `#rrggbb` (ou `rrggbb`, `#rgb`), normalisé en
`#RRGGBB` — la forme que l'analyse écrit dans la spécification.
"""

from __future__ import annotations

import colorsys
import re
from typing import Any, Final

#: Les clés d'une spécification qui portent une couleur.
COLOR_KEYS: Final = frozenset({"color", "fill", "stroke", "background"})
#: Sous cette saturation, une couleur est un gris : pas de teinte à suivre.
GRAY_SATURATION: Final = 0.12
#: Deux teintes à moins de cet écart (en degrés) sont « la même ».
HUE_TOLERANCE_DEG: Final = 10.0
_HEX: Final = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


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


def primary_color_of(spec: dict[str, Any]) -> str | None:
    """La couleur principale du modèle : celle des titres de rubrique."""
    color = (spec.get("styles") or {}).get("section_title", {}).get("color")
    return str(color).upper() if color else None


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


def derived_color(color: str, origin: str, primary: str) -> str | None:
    """La couleur qui correspond à `color` une fois `origin` remplacée par `primary`.

    Exacte : `primary`. Teinte dérivée (même teinte qu'`origin`, pas un gris) :
    `primary` décalée de la même clarté, saturée dans la même proportion.
    Sinon `None` : la couleur n'est pas celle du modèle, elle reste.
    """
    if color.upper() == origin:
        return primary
    oh, ol, os_ = _hls(origin)
    if os_ < GRAY_SATURATION:
        return None
    ch, cl, cs = _hls(color)
    if cs < GRAY_SATURATION or not _same_hue(ch, oh):
        return None
    ph, pl, ps = _hls(primary)
    if ps < GRAY_SATURATION:
        # La nouvelle couleur est un gris : ses dérivées sont des gris de même clarté.
        lightness = min(1.0, max(0.0, pl + (cl - ol)))
        return _hex(*colorsys.hls_to_rgb(0.0, lightness, 0.0))
    lightness = min(1.0, max(0.0, pl + (cl - ol)))
    saturation = min(1.0, max(0.0, ps * (cs / os_)))
    return _hex(*colorsys.hls_to_rgb(ph / 360.0, lightness, saturation))


def _walk(node: Any, origin: str, primary: str) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in COLOR_KEYS and _is_hex(value):
                out[key] = derived_color(value, origin, primary) or value
            else:
                out[key] = _walk(value, origin, primary)
        return out
    if isinstance(node, list):
        return [_walk(item, origin, primary) for item in node]
    return node


def recolor_spec(spec: dict[str, Any], primary: str | None) -> dict[str, Any]:
    """La spécification avec la couleur principale remplacée par `primary` (`#RRGGBB`).

    `None`, ou la couleur d'origine, ou un modèle sans couleur mesurée : la
    spécification telle quelle (le même objet). Sinon une copie ; l'original
    n'est pas modifié.
    """
    if primary is None:
        return spec
    origin = primary_color_of(spec)
    if origin is None:
        return spec
    chosen = parse_hex_color(primary)
    if chosen == origin:
        return spec
    recolored: dict[str, Any] = _walk(spec, origin, chosen)
    return recolored
