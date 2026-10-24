"""Fabriques en mémoire de l'infrastructure `documents` : polices, PDF, doublures.

Aucun binaire n'est versionné (CONVENTIONS § 9) : chaque police TrueType et
chaque PDF des tests est **construit à la volée**, avec fontTools et pypdfium2.
Ce sont de vrais fichiers — décodables par pdfplumber, pdfium, fontTools,
python-docx — pas des octets qui leur ressemblent.
"""

from __future__ import annotations

import ctypes
import io
from dataclasses import dataclass, field
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables.O_S_2f_2 import Panose
from fontTools.ttLib.tables.TupleVariation import TupleVariation

from karria.modules.documents.domain.fonts import FRENCH_CHARSET
from karria.modules.documents.infrastructure.fonts import FontFile

MM = 72 / 25.4

#: Ce que couvre une police de test « complète » : le français et la puce.
FULL_CHARS = "".join(sorted(FRENCH_CHARSET | set("•■|ÉÀ")))


def _advance(char: str) -> int:
    """Une chasse qui varie d'un caractère à l'autre (comme une vraie police)."""
    if char == " ":
        return 250
    return 400 + (ord(char) % 7) * 40


def _box(pen: TTGlyphPen, x0: int, y0: int, x1: int, y1: int) -> None:
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()


def build_font(
    *,
    family: str = "Testa",
    style: str = "Regular",
    weight: int = 400,
    chars: str = FULL_CHARS,
    licence: str = "This Font Software is licensed under the SIL Open Font License, Version 1.1",
    fs_type: int = 0,
    os2_version: int = 4,
    use_typo: bool = False,
    italic_angle: float = 0.0,
    fixed_pitch: bool = False,
    panose_serif: int = 11,
    family_class: int = 8 << 8,
    with_os2: bool = True,
    with_hhea: bool = True,
    upem: int = 1000,
) -> bytes:
    """Une petite police TrueType : un rectangle par caractère, métriques réalistes."""
    fb = FontBuilder(upem, isTTF=True)
    names = {c: f"uni{ord(c):04X}" for c in chars}
    order = [".notdef", *names.values()]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): g for c, g in names.items()})
    glyphs: dict[str, Any] = {}
    metrics: dict[str, tuple[int, int]] = {}
    pen = TTGlyphPen(None)
    _box(pen, 50, 0, 450, 700)
    glyphs[".notdef"] = pen.glyph()
    metrics[".notdef"] = (500, 50)
    for char, name in names.items():
        width = _advance(char)
        pen = TTGlyphPen(None)
        if char != " ":
            top = 500 if char.islower() else 700
            if char in "•■":
                _box(pen, 100, 200, 300, 400)
            else:
                _box(pen, 40, 0, width - 40, top)
        glyphs[name] = pen.glyph()
        metrics[name] = (width, 40 if char != " " else 0)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    if with_hhea:
        fb.setupHorizontalHeader(ascent=900, descent=-250)
    fb.setupNameTable(
        {
            "familyName": family,
            "styleName": style,
            "licenseDescription": licence,
            "uniqueFontIdentifier": f"{family}-{style}",
            "fullName": f"{family} {style}",
            "psName": f"{family.replace(' ', '')}-{style.replace(' ', '')}",
        }
    )
    if with_os2:
        selection = 0x40
        if "Italic" in style:
            selection = 0x01
        if use_typo:
            selection |= 1 << 7
        fb.setupOS2(
            version=os2_version,
            usWeightClass=weight,
            fsType=fs_type,
            sTypoAscender=800,
            sTypoDescender=-200,
            sTypoLineGap=0,
            usWinAscent=900,
            usWinDescent=250,
            sxHeight=500,
            sCapHeight=700,
            fsSelection=selection,
            sFamilyClass=family_class,
            panose=Panose(bFamilyType=2, bSerifStyle=panose_serif),
        )
    fb.setupPost(italicAngle=italic_angle, isFixedPitch=int(fixed_pitch))
    font = fb.font
    if not with_os2 and "OS/2" in font:
        del font["OS/2"]
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def build_variable_font(*, family: str = "Varia", ital_axis: bool = False) -> bytes:
    """Une police variable minimale (axe `wght`, et `ital` sur demande) pour l'instanciation."""
    chars = "ab "
    fb = FontBuilder(1000, isTTF=True)
    names = {c: f"uni{ord(c):04X}" for c in chars}
    order = [".notdef", *names.values()]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): g for c, g in names.items()})
    glyphs: dict[str, Any] = {}
    for name in order:
        pen = TTGlyphPen(None)
        _box(pen, 50, 0, 450, 700)
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(dict.fromkeys(order, (500, 50)))
    fb.setupHorizontalHeader(ascent=900, descent=-250)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(usWeightClass=400)
    fb.setupPost()
    axes: list[tuple[str, float, float, float, str]] = [("wght", 100, 400, 900, "Weight")]
    if ital_axis:
        axes.append(("ital", 0, 0, 1, "Italic"))
    fb.setupFvar(axes, [])
    variations = {
        name: [
            TupleVariation(
                {"wght": (0, 1, 1)}, [(40, 0), (40, 0), (-40, 0), (-40, 0)] + [(0, 0)] * 4
            )
        ]
        for name in order
    }
    fb.setupGvar(variations)
    stat_axes = [
        {
            "tag": "wght",
            "name": "Weight",
            "values": [
                {"value": w, "name": f"W{w}", "flags": 2 if w == 400 else 0}
                for w in range(100, 1000, 100)
            ],
        }
    ]
    if ital_axis:
        stat_axes.append(
            {
                "tag": "ital",
                "name": "Italic",
                "values": [
                    {"value": 0, "name": "Roman", "flags": 2},
                    {"value": 1, "name": "Italic"},
                ],
            }
        )
    fb.setupStat(stat_axes)
    out = io.BytesIO()
    fb.font.save(out)
    return out.getvalue()


def font_file(
    family: str = "Testa", weight: int = 400, italic: bool = False, **kw: Any
) -> FontFile:
    """Une police servie, prête pour un `FontSet`."""
    style = {(400, False): "Regular", (700, False): "Bold", (400, True): "Italic"}.get(
        (weight, italic), "Medium"
    )
    data = build_font(family=family, style=style, weight=weight, **kw)
    return FontFile(family=family, weight=weight, italic=italic, data=data)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _rgba(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


@dataclass
class PdfBuilder:
    """Construit une page PDF en millimètres, origine en haut à gauche (comme la spécification)."""

    width_mm: float = 210.0
    height_mm: float = 297.0
    #: Polices TrueType à incorporer, par nom employé dans `text(font=…)`.
    embedded: dict[str, bytes] = field(default_factory=dict)
    _doc: Any = field(init=False)
    _page: Any = field(init=False)
    _fonts: dict[str, Any] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._doc = pdfium.PdfDocument.new()
        self._page = self._doc.new_page(self.width_mm * MM, self.height_mm * MM)

    @property
    def _h(self) -> float:
        return self.height_mm * MM

    def _font(self, name: str) -> Any:
        if name not in self._fonts:
            if name in self.embedded:
                data = self.embedded[name]
                buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
                handle = pdfium_c.FPDFText_LoadFont(
                    self._doc.raw, buf, len(data), pdfium_c.FPDF_FONT_TRUETYPE, 0
                )
                self._fonts[name] = (handle, buf)
            else:
                handle = pdfium_c.FPDFText_LoadStandardFont(self._doc.raw, name.encode())
                self._fonts[name] = (handle, None)
        return self._fonts[name][0]

    def text(
        self,
        text: str,
        x_mm: float,
        baseline_mm: float,
        *,
        size: float = 10.0,
        font: str = "Helvetica",
        color: str = "#000000",
    ) -> None:
        """Un objet texte posé à sa ligne de base."""
        handle = self._font(font)
        obj = pdfium_c.FPDFPageObj_CreateTextObj(self._doc.raw, handle, ctypes.c_float(size))
        encoded = (text + "\x00").encode("utf-16-le")
        buf = ctypes.create_string_buffer(encoded)
        pdfium_c.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(pdfium_c.FPDF_WCHAR)))
        r, g, b = _rgba(color)
        pdfium_c.FPDFPageObj_SetFillColor(obj, r, g, b, 255)
        pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x_mm * MM, self._h - baseline_mm * MM)
        pdfium_c.FPDFPage_InsertObject(self._page.raw, obj)

    def rect(
        self,
        x_mm: float,
        y_mm: float,
        w_mm: float,
        h_mm: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        stroke_pt: float = 0.75,
        alpha: int = 255,
    ) -> None:
        """Un rectangle (cadre, filet plein, pastille carrée)."""
        obj = pdfium_c.FPDFPageObj_CreateNewRect(
            x_mm * MM, self._h - (y_mm + h_mm) * MM, w_mm * MM, h_mm * MM
        )
        self._paint(obj, fill, stroke, stroke_pt, alpha)

    def line(
        self,
        x0_mm: float,
        y0_mm: float,
        x1_mm: float,
        y1_mm: float,
        *,
        stroke: str = "#000000",
        stroke_pt: float = 0.75,
    ) -> None:
        """Un filet tracé (contour seul)."""
        obj = pdfium_c.FPDFPageObj_CreateNewPath(x0_mm * MM, self._h - y0_mm * MM)
        pdfium_c.FPDFPath_LineTo(obj, x1_mm * MM, self._h - y1_mm * MM)
        self._paint(obj, None, stroke, stroke_pt, 255)

    def circle(
        self,
        cx_mm: float,
        cy_mm: float,
        r_mm: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
    ) -> None:
        """Un disque (quatre courbes de Bézier) : une puce ronde, une pastille."""
        k = 0.5523 * r_mm
        cx, cy = cx_mm, cy_mm

        def p(x: float, y: float) -> tuple[float, float]:
            return x * MM, self._h - y * MM

        obj = pdfium_c.FPDFPageObj_CreateNewPath(*p(cx + r_mm, cy))
        pdfium_c.FPDFPath_BezierTo(
            obj, *p(cx + r_mm, cy + k), *p(cx + k, cy + r_mm), *p(cx, cy + r_mm)
        )
        pdfium_c.FPDFPath_BezierTo(
            obj, *p(cx - k, cy + r_mm), *p(cx - r_mm, cy + k), *p(cx - r_mm, cy)
        )
        pdfium_c.FPDFPath_BezierTo(
            obj, *p(cx - r_mm, cy - k), *p(cx - k, cy - r_mm), *p(cx, cy - r_mm)
        )
        pdfium_c.FPDFPath_BezierTo(
            obj, *p(cx + k, cy - r_mm), *p(cx + r_mm, cy - k), *p(cx + r_mm, cy)
        )
        pdfium_c.FPDFPath_Close(obj)
        self._paint(obj, fill, stroke, 0.75, 255)

    def _paint(
        self, obj: Any, fill: str | None, stroke: str | None, stroke_pt: float, alpha: int
    ) -> None:
        if fill:
            pdfium_c.FPDFPageObj_SetFillColor(obj, *_rgba(fill), alpha)
        if stroke:
            pdfium_c.FPDFPageObj_SetStrokeColor(obj, *_rgba(stroke), alpha)
            pdfium_c.FPDFPageObj_SetStrokeWidth(obj, ctypes.c_float(stroke_pt))
        mode = pdfium_c.FPDF_FILLMODE_WINDING if fill else pdfium_c.FPDF_FILLMODE_NONE
        pdfium_c.FPDFPath_SetDrawMode(obj, mode, 1 if stroke else 0)
        pdfium_c.FPDFPage_InsertObject(self._page.raw, obj)

    def image(self, png_or_pil: Any, x_mm: float, y_mm: float, w_mm: float, h_mm: float) -> None:
        """Une image posée dans sa boîte."""
        from PIL import Image  # noqa: PLC0415

        pil = (
            png_or_pil
            if isinstance(png_or_pil, Image.Image)
            else Image.open(io.BytesIO(png_or_pil))
        )
        bitmap = pdfium.PdfBitmap.from_pil(pil.convert("RGB"))
        obj = pdfium_c.FPDFPageObj_NewImageObj(self._doc.raw)
        pdfium_c.FPDFImageObj_SetBitmap(None, 0, obj, bitmap.raw)
        pdfium_c.FPDFImageObj_SetMatrix(
            obj, w_mm * MM, 0, 0, h_mm * MM, x_mm * MM, self._h - (y_mm + h_mm) * MM
        )
        pdfium_c.FPDFPage_InsertObject(self._page.raw, obj)
        self._keep = [*getattr(self, "_keep", []), bitmap]

    def clip(self, x_mm: float, y_mm: float, w_mm: float, h_mm: float) -> None:
        """Un détourage rectangulaire (`re W n`) autour de **tout** le contenu de la page.

        pdfium n'écrit pas de détourage par objet : on l'ajoute au flux de
        contenu, à l'enregistrement, avec pypdf.
        """
        left, bottom = x_mm * MM, self._h - (y_mm + h_mm) * MM
        self._clip = (left, bottom, w_mm * MM, h_mm * MM)

    def to_bytes(self, *, title: str | None = None, producer: str | None = None) -> bytes:
        """Le PDF, et son titre (dictionnaire `Info`) si demandé."""
        pdfium_c.FPDFPage_GenerateContent(self._page.raw)
        buffer = io.BytesIO()
        self._doc.save(buffer)
        data = buffer.getvalue()
        clip = getattr(self, "_clip", None)
        if title is None and producer is None and clip is None:
            return data
        from pypdf import PdfReader, PdfWriter  # noqa: PLC0415
        from pypdf.generic import ContentStream  # noqa: PLC0415

        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
        if clip is not None:
            page = writer.pages[0]
            content = page.get_contents()
            assert content is not None
            body = content.get_data()
            prefix = "q {:.3f} {:.3f} {:.3f} {:.3f} re W n\n".format(*clip).encode()
            stream = ContentStream(None, writer)
            stream.set_data(prefix + body + b"\nQ")
            page.replace_contents(stream)
        meta = {}
        if title is not None:
            meta["/Title"] = title
        if producer is not None:
            meta["/Producer"] = producer
        writer.add_metadata(meta)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()


def blank_pdf(width_mm: float = 60.0, height_mm: float = 40.0, **meta: Any) -> bytes:
    """Une page vide (petite : la rastérisation reste instantanée)."""
    return PdfBuilder(width_mm, height_mm).to_bytes(**meta)


def png(width: int = 40, height: int = 30, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    """Une image PNG unie, avec une bande sombre (pas un aplat : l'œil doit y voir quelque chose)."""
    from PIL import Image  # noqa: PLC0415

    img = Image.new("RGB", (width, height), color)
    for x in range(width // 3):
        for y in range(height):
            img.putpixel((x, y), (20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Un modèle de CV complet, à deux colonnes et deux bandes
# ---------------------------------------------------------------------------

REGULAR = "Testa-Regular"
BOLD = "Testa-Bold"

#: Le rôle de chaque ligne du modèle, par son texte : (rôle, rubrique, élément).
CV_ROLES: dict[str, tuple[str, str, int]] = {
    "JEAN DUPONT": ("full_name", "header", 0),
    "06 12 34 56 78": ("phone", "header", 0),
    "CONTACT": ("section_title", "contact", 0),
    "jean@mail.fr": ("email", "contact", 0),
    "Paris": ("address", "contact", 0),
    "COMPETENCES": ("section_title", "skills", 0),
    "TECHNIQUE": ("subsection_title", "skills", 0),
    "Python": ("skill", "skills", 0),
    "SQL": ("skill", "skills", 0),
    "PROFIL": ("section_title", "summary", 0),
    "lorem ipsum dolor sit": ("summary", "summary", 0),
    "amet": ("summary", "summary", 0),
    "EXPERIENCES": ("section_title", "experiences", 0),
    "Developpeur": ("item_title", "experiences", 1),
    "2020 - 2023": ("item_dates", "experiences", 1),
    "Acme": ("item_subtitle", "experiences", 1),
    "Concu une API": ("bullet", "experiences", 1),
    "Deploye en prod": ("bullet", "experiences", 1),
    "Stagiaire": ("item_title", "experiences", 2),
    "2019": ("item_dates", "experiences", 2),
    "Beta": ("item_subtitle", "experiences", 2),
    "LANGUES": ("section_title", "languages", 0),
    "Anglais": ("language", "languages", 0),
    "FORMATION": ("section_title", "educations", 0),
    "Master": ("item_title", "educations", 1),
    "2018": ("item_dates", "educations", 1),
}


def cv_fonts() -> dict[str, bytes]:
    """Les deux polices incorporées au modèle (normal et gras)."""
    return {
        REGULAR: build_font(family="Testa", style="Regular", weight=400),
        BOLD: build_font(family="Testa", style="Bold", weight=700),
    }


def cv_template_pdf() -> bytes:
    """Le modèle : en-tête (nom, téléphone + pictogramme, bandeau, filet, photo),
    bande 1 (contact, compétences | profil, expériences avec frise et pastilles),
    filet pleine largeur, bande 2 (langues | formation), ornement de page."""
    b = PdfBuilder(embedded=cv_fonts())
    # En-tête
    b.rect(0, 0, 210, 5, fill="#336699")
    b.text("JEAN DUPONT", 20, 25, size=24, font=BOLD, color="#222222")
    b.rect(135, 20.5, 3, 3, fill="#336699")
    b.text("06 12 34 56 78", 140, 25, size=9, font=REGULAR)
    b.line(24, 30, 24, 44, stroke="#999999")
    b.image(png(60, 80), 170, 8, 25, 30)
    # Bande 1, colonne de gauche
    b.text("CONTACT", 15, 60, size=11, font=BOLD, color="#336699")
    b.rect(15, 64.5, 3, 3, fill="#336699")
    b.text("jean@mail.fr", 20, 67, size=9, font=REGULAR)
    b.text("Paris", 20, 72, size=9, font=REGULAR)
    b.text("COMPETENCES", 15, 90, size=11, font=BOLD, color="#336699")
    b.text("TECHNIQUE", 15, 96, size=8, font=BOLD)
    b.circle(16.5, 100, 0.6, fill="#336699")
    b.text("Python", 19, 101, size=9, font=REGULAR)
    b.circle(16.5, 105, 0.6, fill="#336699")
    b.text("SQL", 19, 106, size=9, font=REGULAR)
    # Bande 1, colonne de droite
    b.text("PROFIL", 80, 60, size=11, font=BOLD, color="#336699")
    b.text("lorem ipsum dolor sit", 80, 67, size=9, font=REGULAR)
    b.text("lorem ipsum dolor sit", 80, 71.5, size=9, font=REGULAR)
    b.text("amet", 80, 76, size=9, font=REGULAR)
    b.line(80, 82, 180, 82, stroke="#CCCCCC", stroke_pt=0.5)
    b.text("EXPERIENCES", 80, 95, size=11, font=BOLD, color="#336699")
    b.line(81, 99, 81, 128, stroke="#336699", stroke_pt=1)
    b.circle(81, 101.5, 1.2, fill="#336699")
    b.text("Developpeur", 85, 103, size=10, font=BOLD)
    b.line(145, 99.5, 145, 104, stroke="#999999", stroke_pt=0.5)
    b.text("2020 - 2023", 150, 103, size=9, font=REGULAR, color="#666666")
    b.text("Acme", 85, 108, size=9, font=REGULAR, color="#666666")
    b.rect(86, 112.3, 1, 1, fill="#222222")
    b.text("Concu une API", 89, 114, size=9, font=REGULAR)
    b.rect(86, 117.3, 1, 1, fill="#222222")
    b.text("Deploye en prod", 89, 119, size=9, font=REGULAR)
    b.line(81, 127, 81, 134, stroke="#336699", stroke_pt=1)
    b.circle(81, 129.5, 1.2, fill="#336699")
    b.text("Stagiaire", 85, 131, size=10, font=BOLD)
    b.line(145, 127.5, 145, 132, stroke="#999999", stroke_pt=0.5)
    b.text("2019", 150, 131, size=9, font=REGULAR, color="#666666")
    b.text("Beta", 85, 136, size=9, font=REGULAR, color="#666666")
    # Entre les bandes, puis bande 2
    b.line(22, 150, 202, 150, stroke="#336699", stroke_pt=1)
    b.text("LANGUES", 15, 165, size=11, font=BOLD, color="#336699")
    b.text("Anglais", 15, 172, size=9, font=REGULAR)
    b.text("FORMATION", 80, 165, size=11, font=BOLD, color="#336699")
    b.text("Master", 85, 172, size=10, font=BOLD)
    b.text("2018", 85, 177, size=9, font=REGULAR, color="#666666")
    # Ornement de page
    b.rect(190, 280, 10, 10, fill="#EEEEEE")
    return b.to_bytes(producer="Canva")


#: Le second modèle : une colonne, nom centré interlettré, titre sur deux lignes.
CENTERED_ROLES: dict[str, tuple[str, str, int]] = {
    "MARIE": ("full_name", "header", 0),
    "CENTRES": ("section_title", "interests", 0),
    "INTERETS": ("section_title", "interests", 0),
    "Lecture": ("interest", "interests", 0),
    "Voyage": ("interest", "interests", 0),
    "PROJETS": ("section_title", "projects", 0),
    "Karria": ("item_title", "projects", 1),
    "aaaa bbbb cccc dddd": ("item_text", "projects", 1),
    "ee": ("item_text", "projects", 1),
}


def centered_template_pdf() -> bytes:
    """Une colonne, nom centré et interlettré (sans poste visé), photo détourée,
    titre de rubrique sur deux lignes avec un filet de rubrique, paragraphe justifié."""
    from karria.modules.documents.infrastructure.fonts import FontMeasurer  # noqa: PLC0415

    fonts = cv_fonts()
    natural = FontMeasurer(fonts[BOLD]).width_pt("M A R I E", 20) / MM
    b = PdfBuilder(150, 200, embedded=fonts)
    b.text("M A R I E", 75 - natural / 2, 25, size=20, font=BOLD)
    b.image(png(38, 20), 110, 30, 38, 20)
    b.text("CENTRES", 20, 50, size=11, font=BOLD, color="#AA3300")
    b.text("INTERETS", 20, 55, size=11, font=BOLD, color="#AA3300")
    b.line(17, 46, 17, 70, stroke="#AA3300", stroke_pt=0.75)
    b.text("Lecture", 20, 62, size=9, font=REGULAR)
    b.text("Voyage", 20, 67, size=9, font=REGULAR)
    b.text("PROJETS", 20, 85, size=11, font=BOLD, color="#AA3300")
    b.text("Karria", 20, 92, size=10, font=BOLD)
    b.text("aaaa bbbb cccc dddd", 20, 97, size=9, font=REGULAR)
    b.text("ee", 20, 101.5, size=9, font=REGULAR)
    b.clip(0, 0, 140, 200)
    return b.to_bytes()


def labels_for(
    lines: list[dict[str, Any]], roles: dict[str, tuple[str, str, int]] | None = None
) -> dict[str, Any]:
    """Les étiquettes que rendrait le modèle de vision, d'après le texte de chaque ligne."""
    roles = roles or CV_ROLES
    out = []
    for line in lines:
        if line["text"] in roles:
            role, section, item = roles[line["text"]]
            out.append({"line": line["id"], "role": role, "section": section, "item": item})
    return {
        "title": "Azur",
        "category": "modern",
        "description_fr": "Deux colonnes.",
        "tags": ["moderne"],
        "photo_shape": "circle",
        "columns": 2,
        "lines": out,
    }


SERVED = {REGULAR: ("Testa", 400, False), BOLD: ("Testa", 700, False)}


def cv_font_set() -> Any:
    """Les polices servies du modèle (mêmes octets que celles incorporées)."""
    from karria.modules.documents.infrastructure.fonts import FontSet  # noqa: PLC0415

    fonts = cv_fonts()
    return FontSet(
        [
            FontFile("Testa", 400, False, fonts[REGULAR]),
            FontFile("Testa", 700, False, fonts[BOLD]),
        ]
    )
