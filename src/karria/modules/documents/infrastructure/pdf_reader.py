"""Lecture déterministe d'un PDF : lignes de texte stylées, formes, images, polices, raster.

Contrat
-------
- Une seule porte vers les deux bibliothèques PDF retenues (ADR-0020 § 9) :
  `pdfplumber`/`pdfminer.six` pour le texte (ce que lisent beaucoup d'ATS) et
  `pypdfium2` pour les formes, les images, les polices incorporées et la
  rastérisation. PyMuPDF est écarté (AGPL).
- Les unités sortent en **millimètres**, origine en haut à gauche, comme la
  spécification. Les couleurs en `#RRGGBB`.
- Une **ligne** regroupe les caractères d'une même ligne de base, sans grand
  blanc entre eux ; ses boîtes suivent les métriques verticales de la police
  (`ascent`/`descent` de l'en-tête `hhea`), celles qu'emploie Chromium : la
  géométrie mesurée ici et celle du rendu parlent la même langue.
- L'opacité se lit dans l'état graphique (`FPDFPageObj_GetFillColor` rend
  l'alpha de l'`ExtGState`) : Canva dessine des cadres à remplissage nul.
"""

from __future__ import annotations

import ctypes
import io
import itertools
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Final

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

#: Plus grande image qu'on rastérise (≈ 30 Mpx) ; au-delà, c'est une bombe de décompression.
MAX_IMAGE_PIXELS: Final = 30_000_000
MM: Final = 72 / 25.4
#: Résolution du raster qui resserre la boîte d'un tracé plein, et tolérance de couleur (somme |Δ|).
TIGHTEN_DPI: Final = 150
TIGHTEN_TOLERANCE: Final = 40
#: Au-delà (mm), un tracé est un fond ou une vague : sa boîte reste celle du PDF.
TIGHTEN_MAX_MM: Final = 60.0
_SUBSET_PREFIX: Final = re.compile(r"^[A-Z]{6}\+")

Json = dict[str, Any]


def _crop_for(
    size_pt: tuple[float, float], box_mm: tuple[float, ...], scale: float
) -> tuple[float, float, float, float] | None:
    """Le recadrage pdfium (gauche, bas, droite, haut, en points) d'une zone `x, y, w, h` en mm.

    Une zone plus fine qu'un point (un filet) s'élargit à un point, une zone
    qui déborde est rognée à la page. pdfium arrondit chaque bord au pixel
    **supérieur** à l'échelle du rendu et refuse ce qui ne laisse pas un pixel :
    on compte comme lui, et on rend `None` quand rien ne resterait.
    """
    width, height = size_pt
    x, y, w, h = box_mm
    w, h = max(w, 1 / MM), max(h, 1 / MM)
    crop = (
        max(0.0, x * MM),
        max(0.0, height - (y + h) * MM),
        max(0.0, width - (x + w) * MM),
        max(0.0, y * MM),
    )
    px = [math.ceil(c * scale) for c in crop]
    if (
        math.ceil(width * scale) - px[0] - px[2] < 1
        or math.ceil(height * scale) - px[1] - px[3] < 1
    ):
        return None
    return crop


def base_font_name(font: str) -> str:
    """`ABCDEF+Raleway-Bold` → `Raleway-Bold` (préfixe de sous-ensemble retiré)."""
    return _SUBSET_PREFIX.sub("", font or "")


def _rgb(color: str) -> tuple[int, int, int] | None:
    """`#RRGGBB` → (r, g, b), ou rien."""
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color or ""):
        return None
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def hex_color(color: Any) -> str | None:
    """Couleur pdfminer (gris, RVB, CMJN, de 0 à 1) → `#RRGGBB`."""
    if color is None:
        return None
    values = tuple(color) if isinstance(color, list | tuple) else (color,)
    if not values:
        return None
    if len(values) == 1:
        rgb = (values[0],) * 3
    elif len(values) == 4:  # noqa: PLR2004 - CMJN
        c, m, y, k = values
        rgb = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    else:
        rgb = values[:3]
    return "#{:02X}{:02X}{:02X}".format(*(round(float(v) * 255) for v in rgb))


@dataclass(frozen=True, slots=True)
class VerticalMetrics:
    """Ascendante et descendante d'une police, en fraction du corps (descendante négative)."""

    ascent: float = 0.95
    descent: float = -0.25

    @property
    def box(self) -> float:
        """Hauteur de la boîte de texte, en fraction du corps."""
        return self.ascent - self.descent


@dataclass(slots=True)
class EmbeddedFont:
    """Une police incorporée au PDF, telle que pdfium la décrit."""

    name: str
    family: str
    weight: int
    italic: bool
    embedded: bool
    data: bytes | None
    flags: int
    ascent: float
    descent: float


@dataclass(slots=True)
class PdfPage:
    """Ce qu'on lit d'une page : de quoi construire la spécification et la mesurer."""

    width_mm: float
    height_mm: float
    pages: int
    lines: list[Json]
    drawings: list[Json]
    images: list[Json]
    fonts: dict[str, EmbeddedFont] = field(default_factory=dict)
    producer: str | None = None


class PdfDocument:
    """Un PDF ouvert en mémoire, lisible par ses deux bibliothèques.

    À fermer (`close`) ou à employer comme gestionnaire de contexte : pdfium
    tient des ressources natives.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pdf = pdfium.PdfDocument(data)
        self._plumber = pdfplumber.open(io.BytesIO(data))
        # Les objets pdfium vivent tant que leur page est ouverte : on garde
        # les pages (une page relâchée ferme ses objets, d'où un accès invalide).
        self._pages: dict[int, Any] = {}

    def __enter__(self) -> PdfDocument:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _page(self, index: int) -> Any:
        if index not in self._pages:
            self._pages[index] = self._pdf[index]
        return self._pages[index]

    def close(self) -> None:
        """Libère les ressources natives."""
        for page in self._pages.values():
            page.close()
        self._pages.clear()
        self._plumber.close()
        self._pdf.close()

    @property
    def page_count(self) -> int:
        """Nombre de pages."""
        return len(self._pdf)

    def page_size_mm(self, index: int = 0) -> tuple[float, float]:
        """Largeur et hauteur de la page, en mm."""
        width, height = self._pdf[index].get_size()
        return width / MM, height / MM

    # ------------------------------------------------------------------ texte
    def chars(self, index: int = 0) -> list[Json]:
        """Les caractères de la page (pdfplumber), dans l'ordre du flux."""
        return list(self._plumber.pages[index].chars)

    def text_lines(
        self,
        index: int = 0,
        *,
        metrics: Any = None,
    ) -> list[Json]:
        """Regroupe les caractères en lignes stylées.

        Args:
            metrics: `(nom de police) -> VerticalMetrics | None` ; sert à poser la
                boîte de chaque ligne comme le ferait le navigateur. À défaut,
                les métriques de la police incorporée.
        """
        fonts = self.fonts(index)
        height_pt = self._pdf[index].get_size()[1]
        chars = [c for c in self.chars(index) if c.get("upright", True)]
        rows: dict[int, list[Json]] = {}
        for char in chars:
            baseline = height_pt - float(char["matrix"][5])
            char["_baseline"] = baseline
            key = next((k for k in rows if abs(k / 10 - baseline) < 0.6), None)  # noqa: PLR2004
            rows.setdefault(key if key is not None else round(baseline * 10), []).append(char)
        lines: list[Json] = []
        for _, row in sorted(rows.items()):
            row.sort(key=lambda c: float(c["x0"]))
            segment: list[Json] = []
            for char in row:
                if segment:
                    prev = segment[-1]
                    if (
                        char["text"] == prev["text"]
                        and float(prev["x1"]) > float(prev["x0"])
                        and abs(float(char["x0"]) - float(prev["x0"])) < 0.1  # noqa: PLR2004
                    ):
                        # Le même caractère réimprimé exactement au même endroit
                        # (un gras ou une ombre par surimpression, Canva) : un
                        # seul glyphe. Un glyphe sans chasse n'est pas une réimpression.
                        continue
                    size = max(float(prev["size"]), float(char["size"]))
                    gap = float(char["x0"]) - float(prev["x1"])
                    if gap > max(1.5 * size, 3 * MM):
                        if "".join(c["text"] for c in segment).strip():
                            lines.append(self._line(segment, fonts, metrics))
                        segment = []
                segment.append(char)
            if segment and "".join(c["text"] for c in segment).strip():
                lines.append(self._line(segment, fonts, metrics))
        lines = [ln for ln in lines if ln["text"].strip()]
        return merge_baselines(lines)

    def _line(self, chars: list[Json], fonts: dict[str, EmbeddedFont], metrics: Any) -> Json:
        visible = [c for c in chars if c["text"].strip()]
        text = words_from_glyphs(chars)
        by_font = Counter(base_font_name(c["fontname"]) for c in visible)
        font = by_font.most_common(1)[0][0]
        main = [c for c in visible if base_font_name(c["fontname"]) == font]
        size = statistics.median(float(c["size"]) for c in main)
        color = Counter(hex_color(c.get("non_stroking_color")) for c in main).most_common(1)[0][0]
        baseline = statistics.median(float(c["_baseline"]) for c in main)
        x0 = min(float(c["x0"]) for c in visible)
        x1 = max(float(c["x1"]) for c in visible)
        vm: VerticalMetrics | None = metrics(font) if metrics else None
        if vm is None:
            embedded = fonts.get(font)
            vm = (
                VerticalMetrics(embedded.ascent, embedded.descent)
                if embedded and embedded.ascent > 0
                else VerticalMetrics()
            )
        top = baseline - vm.ascent * size
        bold, italic = style_from_name(font)
        embedded_font = fonts.get(font)
        if embedded_font is not None:
            bold = bold or embedded_font.weight >= 600  # noqa: PLR2004
            italic = italic or embedded_font.italic
        spans = None
        if len(by_font) > 1:
            spans = [
                {
                    "text": c["text"],
                    "font": base_font_name(c["fontname"]),
                    "size": round(float(c["size"]), 2),
                }
                for c in visible
            ]
        return {
            "id": "",
            "text": text,
            "font": font,
            "size": round(size, 2),
            "bold": bold,
            "italic": italic,
            "color": color or "#000000",
            "x": round(x0 / MM, 2),
            "y": round(top / MM, 2),
            "w": round((x1 - x0) / MM, 2),
            "h": round(vm.box * size / MM, 2),
            "baseline": round(baseline / MM, 3),
            "letter_spacing_pt": 0.0,
            "caps": text.isupper() and any(ch.isalpha() for ch in text),
            "spans": spans,
            "chars": [
                (c["text"], round(float(c["x0"]) / MM, 3), round(float(c["x1"]) / MM, 3))
                for c in visible
            ],
        }

    # ------------------------------------------------------------------ objets pdfium
    def _objects(self, index: int) -> list[_Placed]:
        """Tous les objets de la page, formulaires (XObject) dépliés, avec la
        transformation de leurs parents : pdfium rend les bornes d'un objet
        de formulaire dans l'espace du formulaire."""
        page = self._page(index)
        out: list[_Placed] = []

        def walk(count: int, get: Any, parent: tuple[float, ...], depth: int) -> None:
            for i in range(count):
                raw = get(i)
                kind = pdfium_c.FPDFPageObj_GetType(raw)
                if kind == pdfium_c.FPDF_PAGEOBJ_FORM and depth < 8:  # noqa: PLR2004
                    inner = _compose(_matrix(raw), parent)
                    walk(
                        pdfium_c.FPDFFormObj_CountObjects(raw),
                        lambda j, form=raw: pdfium_c.FPDFFormObj_GetObject(form, j),
                        inner,
                        depth + 1,
                    )
                    continue
                out.append(_Placed(raw, kind, parent))

        walk(
            pdfium_c.FPDFPage_CountObjects(page.raw),
            lambda j: pdfium_c.FPDFPage_GetObject(page.raw, j),
            _IDENTITY,
            0,
        )
        return out

    def fonts(self, index: int = 0) -> dict[str, EmbeddedFont]:
        """Les polices des objets texte de la page, par nom de base."""
        out: dict[str, EmbeddedFont] = {}
        for obj in self._objects(index):
            if obj.kind != pdfium_c.FPDF_PAGEOBJ_TEXT:
                continue
            handle = pdfium_c.FPDFTextObj_GetFont(obj.raw)
            if not handle:
                continue
            name = base_font_name(_font_string(pdfium_c.FPDFFont_GetBaseFontName, handle))
            if name in out:
                continue
            family = _font_string(pdfium_c.FPDFFont_GetFamilyName, handle)
            size = 1000.0
            asc, desc = ctypes.c_float(), ctypes.c_float()
            pdfium_c.FPDFFont_GetAscent(handle, ctypes.c_float(size), ctypes.byref(asc))
            pdfium_c.FPDFFont_GetDescent(handle, ctypes.c_float(size), ctypes.byref(desc))
            embedded = bool(pdfium_c.FPDFFont_GetIsEmbedded(handle))
            data = _font_data(handle) if embedded else None
            flags = int(pdfium_c.FPDFFont_GetFlags(handle))
            out[name] = EmbeddedFont(
                name=name,
                family=family,
                weight=int(pdfium_c.FPDFFont_GetWeight(handle)),
                italic=_italic_angle(handle) != 0,
                embedded=embedded,
                data=data,
                flags=flags,
                ascent=asc.value / size,
                descent=desc.value / size,
            )
        return out

    def drawings(self, index: int = 0) -> list[Json]:
        """Les formes vectorielles visibles (filets, pastilles, cadres), en mm.

        Une forme dont le remplissage et le contour sont transparents n'existe
        pas à l'œil : elle est écartée ; une couleur à alpha nul devient `None`.
        """
        crop_left, _, _, crop_top = self._page(index).get_cropbox()
        out: list[Json] = []
        for obj in self._objects(index):
            if obj.kind != pdfium_c.FPDF_PAGEOBJ_PATH:
                continue
            left, bottom, right, top = obj.bounds()
            fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
            pdfium_c.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill_mode), ctypes.byref(stroke))
            fill = _color(pdfium_c.FPDFPageObj_GetFillColor, obj.raw) if fill_mode.value else None
            line = _color(pdfium_c.FPDFPageObj_GetStrokeColor, obj.raw) if stroke.value else None
            width = ctypes.c_float()
            pdfium_c.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(width))
            stroke_pt = width.value * _scale(_matrix(obj.raw)) * _scale(obj.parent)
            if line is not None:
                # Les bornes de pdfium débordent du tracé de toute l'épaisseur du
                # trait, de chaque côté : on revient à la géométrie du chemin.
                inset = stroke_pt
                left, right = left + inset, right - inset
                bottom, top = bottom + inset, top - inset
                if right < left:
                    left = right = (left + right) / 2
                if top < bottom:
                    bottom = top = (bottom + top) / 2
            x, y = (left - crop_left) / MM, (crop_top - top) / MM
            w, h = (right - left) / MM, (top - bottom) / MM
            kind = "rect"
            if h < 0.6 and w > 3:  # noqa: PLR2004
                kind = "hline"
            elif w < 0.6 and h > 3:  # noqa: PLR2004
                kind = "vline"
            elif _has_curve(obj.raw):
                kind = "curve"
            if kind in ("hline", "vline") and line is None and fill is not None:
                line, fill = fill, None
                stroke_pt = min(w, h) * MM or 0.75
            out.append(
                {
                    "kind": kind,
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "w": round(w, 2),
                    "h": round(h, 2),
                    "fill": fill[0] if fill and fill[1] > 0 else None,
                    "stroke": line[0] if line and line[1] > 0 else None,
                    "stroke_width_pt": round(stroke_pt, 2),
                }
            )
        return [d for d in out if d["fill"] or d["stroke"]]

    def images(self, index: int = 0) -> list[Json]:
        """Les images de la page : boîte, cadre de détourage, octets PNG."""
        crop_left, _, _, crop_top = self._page(index).get_cropbox()
        out: list[Json] = []
        for obj in self._objects(index):
            if obj.kind != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                continue
            left, bottom, right, top = obj.bounds()
            png = io.BytesIO()
            bitmap = pdfium_c.FPDFImageObj_GetBitmap(obj.raw)
            if not bitmap:
                continue
            raster = pdfium.PdfBitmap.from_raw(bitmap).to_pil()
            if raster.width * raster.height > MAX_IMAGE_PIXELS:
                # Une image géante n'est pas un pictogramme : on ne la décode pas.
                continue
            raster.convert("RGB").save(png, "PNG")
            clip = _clip_box(obj.raw, obj.parent)
            entry: Json = {
                "x": round((left - crop_left) / MM, 2),
                "y": round((crop_top - top) / MM, 2),
                "w": round((right - left) / MM, 2),
                "h": round((top - bottom) / MM, 2),
                "png": png.getvalue(),
                "clip": None,
            }
            if clip is not None:
                cl, cb, cr, ct = clip
                entry["clip"] = {
                    "x": round((cl - crop_left) / MM, 2),
                    "y": round((crop_top - ct) / MM, 2),
                    "w": round((cr - cl) / MM, 2),
                    "h": round((ct - cb) / MM, 2),
                }
            out.append(entry)
        return out

    # ------------------------------------------------------------------ raster
    def render_gray(self, index: int = 0, *, dpi: float = 72) -> Any:
        """La page en niveaux de gris (tableau numpy `uint8`, H × L)."""
        bitmap = self._pdf[index].render(scale=dpi / 72, grayscale=True, may_draw_forms=True)
        array = bitmap.to_numpy()
        return array[:, :, 0] if array.ndim == 3 else array  # noqa: PLR2004

    def render_png(
        self, index: int = 0, *, dpi: float = 150, box_mm: tuple[float, ...] | None = None
    ) -> bytes:
        """La page (ou une zone `x, y, w, h` en mm) en PNG."""
        page = self._pdf[index]
        crop = (0.0, 0.0, 0.0, 0.0)
        if box_mm is not None:
            crop = _crop_for(page.get_size(), box_mm, dpi / 72) or crop
        bitmap = page.render(scale=dpi / 72, crop=crop)
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, "PNG")
        return buffer.getvalue()

    def is_round(self, box_mm: tuple[float, float, float, float]) -> bool:
        """Forme **vue** d'une petite figure : coin clair et centre sombre = un rond.

        Canva détoure des carrés en ronds : le tracé ment, le rendu non.
        """
        page = self._pdf[0]
        # Une figure qui déborde de la page est rognée à la page : pdfium refuse
        # un recadrage hors des bords.
        crop = _crop_for(page.get_size(), box_mm, 600 / 72)
        if crop is None:
            return False
        image = page.render(scale=600 / 72, crop=crop, grayscale=True).to_pil().convert("L")
        iw, ih = image.size
        corner = int(image.getpixel((max(0, iw // 12), max(0, ih // 12))))
        center = int(image.getpixel((iw // 2, ih // 2)))
        # Un rond touche les quatre bords au milieu ; un cadre (les équerres
        # d'un QR code) a le coin clair et le centre sombre sans toucher les bords.
        inset = max(1, min(iw, ih) // 25)
        edges = [
            int(image.getpixel(point))
            for point in (
                (iw // 2, inset),
                (iw // 2, ih - 1 - inset),
                (inset, ih // 2),
                (iw - 1 - inset, ih // 2),
            )
        ]
        return corner - center > 60 and all(corner - e > 60 for e in edges)  # noqa: PLR2004

    def tighten(self, index: int, d: Json) -> None:
        """La boîte **peinte** d'un tracé plein : celle de ses pixels à sa couleur.

        pdfium borne un tracé par ses points de contrôle : le surligneur sous
        un titre (une courbe) se voyait deux fois plus haut qu'il n'est peint,
        et se dessinait sur le texte. Les rectangles et filets sont déjà justes.
        """
        if d["kind"] != "curve" or not d.get("fill") or max(d["w"], d["h"]) > TIGHTEN_MAX_MM:
            return
        page = self._pdf[index]
        scale = TIGHTEN_DPI / 72
        crop = _crop_for(page.get_size(), (d["x"], d["y"], d["w"], d["h"]), scale)
        if crop is None:
            return
        image = page.render(scale=scale, crop=crop).to_pil().convert("RGB")
        target = _rgb(d["fill"])
        if target is None:
            return
        pixels = image.load()
        iw, ih = image.size
        xs: list[int] = []
        ys: list[int] = []
        for py in range(ih):
            for px in range(iw):
                r, g, b = pixels[px, py][:3]
                if (
                    abs(r - target[0]) + abs(g - target[1]) + abs(b - target[2])
                    <= TIGHTEN_TOLERANCE
                ):
                    xs.append(px)
                    ys.append(py)
        if not xs:
            return
        px_mm = 25.4 / TIGHTEN_DPI
        x0, y0 = d["x"] + min(xs) * px_mm, d["y"] + min(ys) * px_mm
        x1, y1 = d["x"] + (max(xs) + 1) * px_mm, d["y"] + (max(ys) + 1) * px_mm
        d.update(x=round(x0, 2), y=round(y0, 2), w=round(x1 - x0, 2), h=round(y1 - y0, 2))

    def read(self, index: int = 0, *, metrics: Any = None) -> PdfPage:
        """Tout ce que la spécification et la mesure lisent d'une page."""
        width, height = self.page_size_mm(index)
        lines = self.text_lines(index, metrics=metrics)
        drawings = self.drawings(index)
        for d in drawings:
            self.tighten(index, d)
        return PdfPage(
            width_mm=round(width, 2),
            height_mm=round(height, 2),
            pages=self.page_count,
            lines=lines,
            drawings=drawings,
            images=self.images(index),
            fonts=self.fonts(index),
            producer=(self._plumber.metadata or {}).get("Producer"),
        )

    def metadata_title(self) -> str:
        """Le titre du document (la mise en page y écrit son état d'ajustement)."""
        title: object = (self._plumber.metadata or {}).get("Title") or ""
        return title if isinstance(title, str) else str(title)


def words_from_glyphs(chars: list[Json]) -> str:
    """Le texte d'une ligne, reconstruit par les espaces du PDF et l'écart entre glyphes.

    Les espaces du PDF ne suffisent pas : un titre « B E N J A M I N » est
    souvent dessiné par interlettrage, sans espace, et un autre par des
    espaces tapés entre chaque lettre. On mesure l'écart usuel entre deux
    glyphes visibles de la ligne ; un mot commence là où l'écart le dépasse
    nettement (de 80 %, et d'au moins 0,18 em) — ou là où le PDF a mis une
    espace, même étroite (une police condensée : 0,15 em, sous l'écart).
    """
    glyphs = [c for c in chars if str(c["text"]).strip()]
    if not glyphs:
        return ""
    spaced = {id(c) for prev, c in itertools.pairwise(chars) if not str(prev["text"]).strip()}
    gaps = [float(b["x0"]) - float(a["x1"]) for a, b in itertools.pairwise(glyphs)]
    typical = statistics.median(gaps) if gaps else 0.0
    typical = max(typical, 0.0)
    text = str(glyphs[0]["text"])
    for gap, glyph in zip(gaps, glyphs[1:], strict=True):
        size = float(glyph["size"])
        if id(glyph) in spaced or gap > typical + max(0.18 * size, 0.8 * typical):
            text += " "
        text += str(glyph["text"])
    return text


def style_from_name(font: str) -> tuple[bool, bool]:
    """Graisse et style déduits du nom (`Raleway-BoldItalic`)."""
    lower = font.lower()
    bold = any(k in lower for k in ("bold", "black", "heavy", "semibold", "demi"))
    italic = "italic" in lower or "oblique" in lower
    return bold, italic


def merge_baselines(lines: list[Json]) -> list[Json]:
    """Recolle les morceaux d'une ligne qu'un texte justifié a dispersés.

    Même police, même corps, même couleur, même ligne de base (±0,6 mm), écart
    de moins de 4 mm : une seule ligne. Les identifiants `L<n>` sont posés
    dans l'ordre de lecture (y puis x).
    """
    ordered = sorted(lines, key=lambda ln: (round(ln["y"] / 0.6), ln["x"]))
    merged: list[Json] = []
    for line in ordered:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and abs(prev["baseline"] - line["baseline"]) <= 0.6  # noqa: PLR2004
            and prev["font"] == line["font"]
            and abs(prev["size"] - line["size"]) < 0.2  # noqa: PLR2004
            and prev["color"] == line["color"]
            and 0 <= line["x"] - (prev["x"] + prev["w"]) <= 4.0  # noqa: PLR2004
        ):
            prev["text"] = prev["text"] + " " + line["text"]
            prev["w"] = round(line["x"] + line["w"] - prev["x"], 2)
            prev["h"] = max(prev["h"], line["h"])
            prev["chars"] = prev["chars"] + line["chars"]
            prev["caps"] = prev["text"].isupper() and any(ch.isalpha() for ch in prev["text"])
            continue
        merged.append(dict(line))
    for i, line in enumerate(merged, 1):
        line["id"] = f"L{i}"
    return merged


def _font_string(getter: Any, handle: Any) -> str:
    size = getter(handle, None, 0)
    if size <= 0:
        return ""
    buffer = ctypes.create_string_buffer(size)
    getter(handle, buffer, size)
    return buffer.value.decode("utf-8", "replace")


def _font_data(handle: Any) -> bytes | None:
    length = ctypes.c_size_t()
    pdfium_c.FPDFFont_GetFontData(handle, None, 0, ctypes.byref(length))
    if length.value == 0:
        return None
    buffer = (ctypes.c_uint8 * length.value)()
    pdfium_c.FPDFFont_GetFontData(handle, buffer, length.value, ctypes.byref(length))
    return bytes(buffer)


def _italic_angle(handle: Any) -> int:
    angle = ctypes.c_int()
    pdfium_c.FPDFFont_GetItalicAngle(handle, ctypes.byref(angle))
    return angle.value


def _color(getter: Any, raw: Any) -> tuple[str, int] | None:
    r, g, b, a = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
    if not getter(raw, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)):
        return None
    return f"#{r.value:02X}{g.value:02X}{b.value:02X}", a.value


_IDENTITY: Final = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class _Placed:
    """Un objet pdfium et la transformation cumulée de ses formulaires parents."""

    raw: Any
    kind: int
    parent: tuple[float, ...]

    def bounds(self) -> tuple[float, float, float, float]:
        """Bornes dans l'espace de la page (gauche, bas, droite, haut), en points."""
        left, bottom, right, top = (ctypes.c_float() for _ in range(4))
        pdfium_c.FPDFPageObj_GetBounds(
            self.raw,
            ctypes.byref(left),
            ctypes.byref(bottom),
            ctypes.byref(right),
            ctypes.byref(top),
        )
        return _transform_box((left.value, bottom.value, right.value, top.value), self.parent)


def _matrix(raw: Any) -> tuple[float, ...]:
    matrix = pdfium_c.FS_MATRIX()
    if not pdfium_c.FPDFPageObj_GetMatrix(raw, ctypes.byref(matrix)):
        return _IDENTITY
    return (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)


def _compose(inner: tuple[float, ...], outer: tuple[float, ...]) -> tuple[float, ...]:
    """`inner` appliquée d'abord, puis `outer` (convention PDF : ligne × matrice)."""
    a, b, c, d, e, f = inner
    A, B, C, D, E, F = outer  # noqa: N806
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _transform_box(
    box: tuple[float, float, float, float], m: tuple[float, ...]
) -> tuple[float, float, float, float]:
    left, bottom, right, top = box
    a, b, c, d, e, f = m
    points = [(x * a + y * c + e, x * b + y * d + f) for x in (left, right) for y in (bottom, top)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _scale(m: tuple[float, ...]) -> float:
    a, b, c, d, _, _ = m
    return float(abs(a * d - b * c) ** 0.5) or 1.0


def _has_curve(raw: Any) -> bool:
    for i in range(pdfium_c.FPDFPath_CountSegments(raw)):
        segment = pdfium_c.FPDFPath_GetPathSegment(raw, i)
        if pdfium_c.FPDFPathSegment_GetType(segment) == pdfium_c.FPDF_SEGMENT_BEZIERTO:
            return True
    return False


def _clip_box(raw: Any, parent: tuple[float, ...]) -> tuple[float, float, float, float] | None:
    """Boîte du plus petit chemin de détourage d'un objet (cadre réel d'une photo)."""
    clip = pdfium_c.FPDFPageObj_GetClipPath(raw)
    if not clip:
        return None
    best: tuple[float, float, float, float] | None = None
    for p in range(pdfium_c.FPDFClipPath_CountPaths(clip)):
        xs: list[float] = []
        ys: list[float] = []
        for s in range(pdfium_c.FPDFClipPath_CountPathSegments(clip, p)):
            segment = pdfium_c.FPDFClipPath_GetPathSegment(clip, p, s)
            x, y = ctypes.c_float(), ctypes.c_float()
            if pdfium_c.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y)):
                xs.append(x.value)
                ys.append(y.value)
        if not xs:
            continue
        box = _transform_box((min(xs), min(ys), max(xs), max(ys)), parent)
        area = (box[2] - box[0]) * (box[3] - box[1])
        if best is None or area < (best[2] - best[0]) * (best[3] - best[1]):
            best = box
    return best
