"""Dériver un modèle Karria **original** d'une spécification transcrite.

Contrat
-------
Un modèle transcrit fidèlement depuis le PDF d'un fournisseur (Canva ou
autre) reste l'expression de ce fournisseur : sa composition, ses polices,
ses couleurs, ses ornements. Ce qui n'appartient à personne, c'est l'idée
de mise en page (deux colonnes, une bande latérale, une photo ronde) et
l'architecture de l'information. Karria peut donc **dériver** de la
transcription une création qui lui est propre, à condition que l'expression
change sur tous les plans à la fois, de façon mesurable :

- la **composition** : les colonnes sont inversées en miroir, la photo change
  de forme ;
- la **typographie** : un couple de polices libres (OFL) qui ne partage
  aucune famille avec la source, pour le nom et les titres d'un côté, le
  texte de l'autre ;
- les **couleurs** : une couleur principale d'une autre teinte (à plus de
  60° de la source), appliquée par `recolor_spec` au seul décor ;
- rien d'autre : les titres, les corps, les filets, les puces gardent leur
  dessin — c'est ce qui fait que la création reste belle et lisible. On a
  essayé d'en changer plus (titres, graisse des filets, forme de toute photo) :
  le résultat était laid et cassé (Marc, 13/11).

La dérivation est **déterministe** (une graine) et **tracée** : la
spécification porte l'empreinte de la source, la graine et chaque choix
(`source.derivation`), pour que l'on puisse toujours dire d'où vient un
modèle et en quoi il s'en écarte. La distance visuelle (SSIM) se mesure
ensuite par le moteur, comme la fidélité — mais on veut qu'elle soit
**faible**.

Ce module ne dit pas le droit : il garantit une expression distincte et une
provenance claire. La licence du fournisseur reste à lire.
"""

from __future__ import annotations

import colorsys
import copy
from dataclasses import dataclass, field
from typing import Any, Final

from karria.modules.documents.domain.palette import primary_color_of, recolor_spec

Json = dict[str, Any]

#: Des couples (titres, texte) de polices libres servies par la bibliothèque.
PAIRINGS: Final[tuple[tuple[str, str], ...]] = (
    ("Red Hat Display", "Source Sans 3"),
    ("Playfair Display", "Lato"),
    ("Manrope", "Mulish"),
    ("Josefin Sans", "Work Sans"),
    ("Oswald", "Karla"),
    ("DM Sans", "Lora"),
    ("Montserrat", "Nunito Sans"),
    ("Raleway", "Open Sans"),
)

#: Des couleurs principales candidates, de teintes bien séparées.
PRIMARIES: Final[tuple[str, ...]] = (
    "#1F4FA3",  # bleu
    "#9A5B2E",  # caramel
    "#2F5D3A",  # vert forêt
    "#6D28D9",  # violet
    "#B91C1C",  # rouge brique
    "#0F766E",  # sarcelle
    "#A16207",  # ocre
    "#BE185D",  # framboise
)

MIN_HUE_DISTANCE_DEG: Final = 60.0
#: Au-delà de cette ressemblance (SSIM tolérant) au rendu de la source, la
#: dérivation n'est pas une création distincte : on la refuse.
MAX_SSIM_TO_SOURCE: Final = 0.68
#: Une bande d'une seule colonne n'a rien à inverser.
MIN_COLUMNS_TO_MIRROR: Final = 2
NAME_ROLES: Final[frozenset[str]] = frozenset(
    {"header.first_name", "header.last_name", "header.name"}
)


@dataclass(frozen=True, slots=True)
class Derivation:
    """Ce que la dérivation a changé, pour la trace et pour le verdict."""

    seed: int
    mirrored: bool
    display_font: str
    body_font: str
    source_fonts: tuple[str, ...]
    primary: str | None
    source_primary: str | None
    photo_shape: str | None
    title_uppercase: bool | None
    notes: tuple[str, ...] = field(default=())

    def to_json(self) -> Json:
        return {
            "seed": self.seed,
            "mirrored": self.mirrored,
            "fonts": {"display": self.display_font, "body": self.body_font},
            "source_fonts": list(self.source_fonts),
            "primary": self.primary,
            "source_primary": self.source_primary,
            "photo_shape": self.photo_shape,
            "title_uppercase": self.title_uppercase,
            "notes": list(self.notes),
        }

    @property
    def shares_a_font(self) -> bool:
        """Vrai si une famille de la source est reprise : la dérivation a échoué sur ce plan."""
        return self.display_font in self.source_fonts or self.body_font in self.source_fonts


def _hue(color: str) -> float | None:
    try:
        r, g, b = (int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return None
    h, _l, s = colorsys.rgb_to_hls(r, g, b)
    return None if s < 0.08 else h * 360.0  # noqa: PLR2004 - un gris n'a pas de teinte


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def choose_pairing(source_fonts: set[str], seed: int) -> tuple[str, str]:
    """Le premier couple, à partir de la graine, qui ne partage rien avec la source."""
    for i in range(len(PAIRINGS)):
        display, body = PAIRINGS[(seed + i) % len(PAIRINGS)]
        if display not in source_fonts and body not in source_fonts:
            return display, body
    return PAIRINGS[seed % len(PAIRINGS)]


def choose_primary(source_primary: str | None, seed: int) -> str:
    """La première couleur, à partir de la graine, à plus de 60° de teinte de la source."""
    source_hue = _hue(source_primary) if source_primary else None
    for i in range(len(PRIMARIES)):
        candidate = PRIMARIES[(seed + i) % len(PRIMARIES)]
        hue = _hue(candidate)
        if (
            source_hue is None
            or hue is None
            or _hue_distance(hue, source_hue) >= MIN_HUE_DISTANCE_DEG
        ):
            return candidate
    return PRIMARIES[seed % len(PRIMARIES)]


def _mirror_box(box: Json, width: float) -> None:
    """Inverse la **position** d'une boîte autour de l'axe vertical de la page.

    Seule la géométrie change (`x_mm`, `rx_mm`) : l'alignement du texte, le
    côté des dates et des puces, l'ordre des lignes restent ce qu'ils sont —
    on lit de gauche à droite, la composition seule change de côté. Les
    lignes d'exemple d'un élément suivent le même décalage que lui.
    """
    x = box.get("x_mm")
    w = box.get("w_mm")
    rx = box.get("rx_mm")
    if not isinstance(x, (int, float)):
        return
    if isinstance(rx, (int, float)):
        new_x, new_rx = width - rx, width - x
        box["x_mm"], box["rx_mm"] = round(new_x, 2), round(new_rx, 2)
    elif isinstance(w, (int, float)):
        new_x = width - x - w
        box["x_mm"] = round(new_x, 2)
    else:
        new_x = width - x
        box["x_mm"] = round(new_x, 2)
    delta = new_x - x
    for line in box.get("sample_lines") or []:
        if isinstance(line, dict) and isinstance(line.get("x_mm"), (int, float)):
            line["x_mm"] = round(line["x_mm"] + delta, 2)


def _band_mirrors(band: Json) -> bool:
    """Une bande s'inverse si elle a au moins deux colonnes et aucune colonne latérale.

    Une colonne pleine largeur n'a rien à inverser ; une rubrique dont les
    dates ou les puces vivent à gauche du texte (`side`) sortirait de la page
    une fois collée au bord (Frise Pharmacie, 13/11). Ces bandes gardent leur
    place ; l'en-tête, la photo et les ornements de page s'inversent quand même.
    """
    columns = band.get("columns") or []
    if len(columns) < MIN_COLUMNS_TO_MIRROR:
        return False
    return not any(
        section.get("side") for column in columns for section in column.get("sections") or []
    )


def _mirror(spec: Json) -> bool:
    """Inverse la composition en miroir ; faux si rien ne s'est inversé.

    Chaque bande décide pour elle (`_band_mirrors`) ; l'en-tête, la photo et
    les ornements de page s'inversent toujours, pour que la création change de
    côté même quand le corps garde ses colonnes latérales.
    """
    width = float((spec.get("page") or {}).get("width_mm") or 0)
    if width <= 0:
        return False
    header = spec.get("header") or {}
    for element in header.get("elements") or []:
        _mirror_box(element, width)
    if header.get("photo"):
        _mirror_box(header["photo"], width)
    for deco in header.get("decorations") or []:
        _mirror_box(deco, width)
    for deco in spec.get("page_decorations") or []:
        _mirror_box(deco, width)
    for band in spec.get("bands") or []:
        if not _band_mirrors(band):
            continue
        for deco in band.get("decorations") or []:
            _mirror_box(deco, width)
        columns = band.get("columns") or []
        for column in columns:
            _mirror_box(column, width)
        columns.reverse()
    return True


def _restyle_fonts(spec: Json, display: str, body: str) -> None:
    """Le nom en police de titre ; tout le reste, titres de rubrique compris, en police de texte.

    Un titre de rubrique est mesuré au millimètre pour la police de la source ;
    une police de titre plus large le ferait déborder de sa boîte (« PROFESSIONNELLE / S »).
    La police de texte, de chasse proche, tient ; et son espacement est plafonné.
    """
    styles = spec.get("styles") or {}
    for key, style in styles.items():
        style["family"] = display if key in NAME_ROLES else body
        if key == "section_title" and isinstance(style.get("letter_spacing_pt"), (int, float)):
            style["letter_spacing_pt"] = round(min(float(style["letter_spacing_pt"]), 1.5), 2)
    used: dict[tuple[str, int, bool], Json] = {}
    for style in styles.values():
        ref = (style["family"], int(style.get("weight") or 400), bool(style.get("italic")))
        used.setdefault(ref, {"family": ref[0], "weight": ref[1], "italic": ref[2]})
    spec["fonts"] = list(used.values())


def _toggle_photo(spec: Json) -> str | None:
    """Rond ↔ rectangle, seulement si la boîte est presque carrée : un rond sur un
    rectangle ferait une ellipse."""
    photo = (spec.get("header") or {}).get("photo")
    if not isinstance(photo, dict):
        return None
    w, h = float(photo.get("w_mm") or 0), float(photo.get("h_mm") or 0)
    if w <= 0 or h <= 0 or abs(w - h) > 0.1 * max(w, h):
        return None
    shape = "rect" if photo.get("shape") == "circle" else "circle"
    photo["shape"] = shape
    return shape


def derive_spec(
    spec: Json, *, seed: int = 1, source_sha256: str | None = None
) -> tuple[Json, Derivation]:
    """Une spécification dérivée, originale, et le compte rendu de la dérivation.

    L'original n'est pas modifié. `source_sha256` : l'empreinte du PDF du
    fournisseur, inscrite dans la trace.
    """
    out = copy.deepcopy(spec)
    source_fonts = {str(f.get("family")) for f in spec.get("fonts") or []}
    source_fonts |= {str(s.get("family")) for s in (spec.get("styles") or {}).values()}
    display, body = choose_pairing(source_fonts, seed)
    source_primary = primary_color_of(spec)
    primary = choose_primary(source_primary, seed)

    mirrored = _mirror(out)
    _restyle_fonts(out, display, body)
    shape = _toggle_photo(out)
    recolored = recolor_spec(out, primary)
    if recolored is not out:
        out = recolored
    applied_primary = primary if source_primary is not None else None

    derivation = Derivation(
        seed=seed,
        mirrored=mirrored,
        display_font=display,
        body_font=body,
        source_fonts=tuple(sorted(source_fonts)),
        primary=applied_primary,
        source_primary=source_primary,
        photo_shape=shape,
        title_uppercase=None,
        notes=("sans décor coloré : la couleur de la source est conservée",)
        if source_primary is None
        else (),
    )
    meta = out.setdefault("meta", {})
    meta["origin"] = "karria"
    source = out.setdefault("source", {})
    source["derived_from_sha256"] = source_sha256
    source["derivation"] = derivation.to_json()
    return out, derivation
