"""La lettre de motivation, HTML et DOCX, depuis une spécification de lettre (ADR-0030, ADR-0036).

Contrat
-------
- **Rien n'est déclaré, tout est lu dans la spécification de lettre** : celle
  mesurée dans le PDF de la lettre appariée d'un modèle, ou la lettre sobre
  intégrée (`domain/plain_letter.py`). Page, polices, styles par rôle, blocs
  d'en-tête ancrés (gauche, droite, centre) dont les lignes s'empilent au pas
  mesuré — une ligne vide disparaît sans laisser de blanc —, colonne de texte
  dont les blocs se suivent avec leurs blancs mesurés, décorations.
- **Une page.** Le même script d'ajustement que le CV resserre les blancs puis
  le corps du texte courant ; au-delà, la seconde page est assumée. L'en-tête
  ne bouge pas (comme celui du CV).
- **Même vue pour les deux moteurs** (`domain/letter.py`), même résolveur
  d'adresses et mêmes ancres `data-chemin` que `HtmlRenderer` (ADR-0029).
- Le logo est un fichier joint (`logo.png`) : présent, il se pose au-dessus du
  bloc du destinataire (sinon en haut à droite), à hauteur bornée ; absent,
  rien, sans blanc.
- Les positions se reproduisent au demi-blanc près : Chromium centre la boîte
  de texte dans la ligne (`half`), Word et LibreOffice posent l'excédent
  au-dessus (`lead`) — les mêmes corrections que les moteurs du CV.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any, Final

from docx import Document
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from karria.modules.documents.domain.plain_letter import SUBJECT_PREFIX
from karria.modules.documents.infrastructure.docx_renderer import (
    FontNames,
    embed_fonts,
    no_borders,
    rgb,
    set_widths,
)
from karria.modules.documents.infrastructure.fonts import MM_PER_PT, FontSet, facts_of
from karria.modules.documents.infrastructure.footer import (
    add_docx_footer,
    docx_footer_distance,
    footer_html,
)
from karria.modules.documents.infrastructure.html_renderer import (
    anchor,
    deco_css,
    esc,
    fit_script,
    fit_text_css,
    identity_url,
)

Json = dict[str, Any]

LOGO_FILE: Final = "logo.png"
#: Hauteur maximale du logo (mm) : un en-tête, pas une affiche.
LOGO_HEIGHT_MM: Final = 14.0
LOGO_GAP_MM: Final = 3.0
#: Les champs de l'en-tête lus dans `view["recipient"]` plutôt que `view["header"]`.
RECIPIENT_FIELDS: Final = frozenset({"company", "city"})


def half(style: Json, pitch: float | None = None) -> float:
    """Demi-blanc d'interligne : Chromium centre la boîte de texte dans la ligne."""
    line = float(pitch if pitch is not None else style.get("pitch_mm") or style["box_h_mm"])
    return (line - float(style["box_h_mm"])) / 2


def header_value(view: Json, field: str) -> str:
    """Le texte d'un champ d'en-tête : expéditeur, destinataire ou date."""
    if field == "date_line":
        return str(view.get("date_line") or "")
    if field in RECIPIENT_FIELDS:
        return str((view.get("recipient") or {}).get(field) or "")
    return str((view.get("header") or {}).get(field) or "")


def header_path(view: Json, field: str) -> str | None:
    """Le chemin `data-chemin` d'un champ d'en-tête, ou `None` (la date n'en a pas)."""
    paths: Json = view.get("paths") or {}
    if field == "date_line":
        return None
    if field in RECIPIENT_FIELDS:
        return (paths.get("recipient") or {}).get(field)
    return (paths.get("header") or {}).get(field)


def block_texts(view: Json, block: Json) -> list[tuple[str, str | None]]:
    """Les (texte, chemin) qu'un bloc du corps affiche ; vide quand la donnée manque."""
    role = block["role"]
    paths: Json = view.get("paths") or {}
    if role == "paragraph":
        ps = paths.get("paragraphs") or []
        return [
            (p["text"], ps[i] if i < len(ps) else None)
            for i, p in enumerate(view.get("paragraphs") or [])
            if p.get("text")
        ]
    if role == "subject":
        subject = str(view.get("subject") or "")
        prefix = str(block.get("prefix") or SUBJECT_PREFIX)
        return [(f"{prefix}{subject}", paths.get("subject"))] if subject else []
    text = str(view.get(role) or "")
    return [(text, paths.get(role))] if text else []


class LetterHtmlRenderer:
    """Rend une lettre en HTML imprimable, aux polices et positions de sa spécification."""

    def __init__(
        self,
        spec: Json,
        view: Json,
        fonts: FontSet,
        *,
        logo: str | None,
        url_for: Callable[[str], str] = identity_url,
        anchored: bool = False,
    ) -> None:
        self.spec, self.view, self.fonts = spec, view, fonts
        self.logo, self.url, self.anchored = logo, url_for, anchored
        self.styles: dict[str, Json] = spec["styles"]

    def anchor(self, path: str | None) -> str:
        """`data-chemin` en mode ancré, rien sinon."""
        return anchor(path) if self.anchored else ""

    @staticmethod
    def css_class(key: str) -> str:
        return f"l-{key.replace('.', '-')}"

    def css_style(self, key: str, st: Json) -> str:
        """Une classe par style ; le corps du texte courant suit `--f`, l'en-tête non."""
        scaled = not (key.startswith(("header.", "recipient.")) or key == "date_line")
        f = "*var(--f)" if scaled else ""
        return (
            f".{self.css_class(key)}{{font-family:'{st['family']}',sans-serif;font-weight:{st['weight']};"
            f"font-style:{'italic' if st.get('italic') else 'normal'};font-size:calc({st['size_pt']}pt{f});"
            f"color:{st['color']};letter-spacing:{st['letter_spacing_pt']}pt;"
            f"text-transform:{'uppercase' if st.get('uppercase') else 'none'}}}"
        )

    def font_faces(self) -> str:
        """`@font-face` des instances statiques servies."""
        return "".join(
            f"@font-face{{font-family:'{f.family}';src:url('{self.url(f.file_name)}');"
            f"font-weight:{f.weight};font-style:{'italic' if f.italic else 'normal'}}}"
            for f in self.fonts.files
        )

    # ------------------------------------------------------------------ en-tête
    def header_block(self, block: Json) -> str:
        """Un bloc d'en-tête : ses lignes présentes, empilées au pas mesuré."""
        present = [
            (e, header_value(self.view, e["field"]))
            for e in block["elements"]
            if header_value(self.view, e["field"])
        ]
        if not present:
            return ""
        first_st = self.styles[present[0][0]["style"]]
        top = float(block["y_mm"]) - half(first_st, present[0][0]["pitch_mm"])
        if block["anchor"] == "right":
            place = f"right:{block['right_mm']:.2f}mm;text-align:right"
        elif block["anchor"] == "center":
            place = f"left:{block['cx_mm']:.2f}mm;transform:translateX(-50%);text-align:center"
        else:
            place = f"left:{block['x_mm']:.2f}mm"
        avail = float(block.get("max_w_mm") or self.spec["page"]["width_mm"])
        out = [f'<div class="hb" style="position:absolute;top:{top:.2f}mm;{place}">']
        prev_half: float | None = None
        for e, text in present:
            st = self.styles[e["style"]]
            h = half(st, e["pitch_mm"])
            margin = 0.0 if prev_half is None else prev_half - h
            fit = fit_text_css(self.fonts, text, st, avail)
            out.append(
                f'<div class="t {self.css_class(e["style"])}"{self.anchor(header_path(self.view, e["field"]))} '
                f'style="line-height:{e["pitch_mm"]:.3f}mm;margin-top:{margin:.2f}mm;white-space:nowrap{fit}">'
                f"{esc(text)}</div>"
            )
            prev_half = h
        out.append("</div>")
        return "".join(out)

    def logo_html(self) -> str:
        """Le logo au-dessus du bloc du destinataire (sinon en haut à droite)."""
        if not self.logo:
            return ""
        right_blocks = [
            b
            for b in self.spec["header"]["blocks"]
            if b["anchor"] == "right" and any(e["field"] in RECIPIENT_FIELDS for e in b["elements"])
        ]
        if right_blocks:
            b = min(right_blocks, key=lambda b: float(b["y_mm"]))
            top = max(2.0, float(b["y_mm"]) - LOGO_HEIGHT_MM - LOGO_GAP_MM)
            right = float(b["right_mm"])
        else:
            body = self.spec["body"]
            top = 10.0
            right = float(self.spec["page"]["width_mm"]) - float(body["x_mm"]) - float(body["w_mm"])
        return (
            f'<img src="{esc(self.url(self.logo))}" alt="" style="position:absolute;top:{top:.2f}mm;'
            f'right:{right:.2f}mm;max-height:{LOGO_HEIGHT_MM}mm;max-width:40mm">'
        )

    def header(self) -> str:
        """Décorations d'en-tête, blocs, logo."""
        out = [
            f'<div style="{deco_css(d, d["x_mm"], d["y_mm"])}"></div>'
            for d in self.spec["header"].get("decorations", [])
        ]
        out.extend(self.header_block(b) for b in self.spec["header"]["blocks"])
        out.append(self.logo_html())
        return "".join(out)

    # ------------------------------------------------------------------ corps
    def body(self) -> str:
        """Les blocs du corps, dans l'ordre du modèle, aux blancs mesurés."""
        body = self.spec["body"]
        out: list[str] = []
        prev_half: float | None = None
        for block in body["blocks"]:
            texts = block_texts(self.view, block)
            if not texts:
                continue
            st = self.styles[block["style"]]
            pitch = float(st.get("pitch_mm") or st["box_h_mm"])
            h = half(st, pitch)
            gap = float(block["gap_before_mm"])
            for i, (text, path) in enumerate(texts):
                if prev_half is None:
                    margin = 0.0
                elif i == 0:
                    margin = gap - prev_half - h
                else:
                    margin = float(block.get("gap_between_mm") or pitch) - 2 * h
                out.append(
                    f'<div class="t {self.css_class(block["style"])}"{self.anchor(path)} '
                    f'style="margin-top:calc({margin:.2f}mm*var(--k));line-height:calc({pitch:.3f}mm*var(--f));'
                    f'text-align:{block["align"]}">{esc(text)}</div>'
                )
                prev_half = h
        return "".join(out)

    def render(self) -> str:
        """Le document HTML complet."""
        page = self.spec["page"]
        body = self.spec["body"]
        first = next((b for b in body["blocks"] if block_texts(self.view, b)), body["blocks"][0])
        first_st = self.styles[first["style"]]
        top = float(body["top_mm"]) - half(first_st)
        css = [
            self.font_faces(),
            f"@page{{size:{page['width_mm']}mm {page['height_mm']}mm;margin:0}}",
            "html,body{margin:0;padding:0}*{box-sizing:border-box}",
            ".t{white-space:pre-wrap;overflow-wrap:anywhere;position:relative}",
            ":root{--k:1;--f:1}",
            f".page{{position:relative;width:{page['width_mm']}mm;height:{page['height_mm']}mm;overflow:hidden;background:{page['background']}}}",
            f".sheet{{position:absolute;left:{body['x_mm']:.2f}mm;top:{top:.2f}mm;width:{body['w_mm']:.2f}mm}}",
            *(self.css_style(k, v) for k, v in self.styles.items() if "box_h_mm" in v),
        ]
        statics = "".join(
            f'<div style="{deco_css(d, d["x_mm"], d["y_mm"])}"></div>'
            for d in self.spec.get("page_decorations", [])
        )
        return (
            f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Lettre</title><style>{"".join(css)}</style></head>'
            f'<body><div class="page">{statics}{self.header()}<div class="sheet">{self.body()}</div>'
            f"{footer_html('letter')}</div>{fit_script(self.spec, live=self.anchored)}</body></html>"
        )


class LetterDocxRenderer:
    """Rend une lettre en DOCX modifiable, polices incorporées, aux mêmes mesures.

    Chaque ligne de base est posée là où la spécification la met, d'après la
    règle mesurée de Word et LibreOffice pour un interligne **exact** `L` et
    une police de boîte `B` (ascendante `A`, descendante `D`) : si `L ≥ B`,
    l'excédent va au-dessus et la ligne de base tombe à `L − D` du haut de la
    ligne ; si `L < B`, la boîte est réduite à l'échelle et la ligne de base
    tombe à `L × A / B`. Un blanc négatif n'existe pas : une ligne qui devrait
    commencer plus haut que le bas de la précédente est raccourcie jusqu'à y
    tenir (le nom en grand corps, au pas plus serré que sa boîte).
    """

    def __init__(
        self,
        spec: Json,
        view: Json,
        fonts: FontSet,
        *,
        logo: bytes | None,
        k: float = 1.0,
        f: float = 1.0,
    ) -> None:
        self.spec, self.view, self.fonts, self.logo = spec, view, fonts, logo
        self.k, self.f = k, f
        self.styles: dict[str, Json] = spec["styles"]
        self.names = FontNames(fonts)
        self.doc = Document()

    # ------------------------------------------------------------------ métriques
    def metrics(self, key: str, scale: float = 1.0) -> tuple[float, float, float]:
        """(ascendante, descendante, boîte) d'un style en mm, sur la police servie."""
        st = self.styles[key]
        size = float(st["size_pt"]) * scale
        font = self.fonts.get(str(st["family"]), int(st["weight"]), bool(st.get("italic")))
        if font is None:
            box = float(st["box_h_mm"]) * scale
            return 0.8 * box, 0.2 * box, box
        facts = facts_of(font.data)
        ascent = facts.ascent * size * MM_PER_PT
        descent = -facts.descent * size * MM_PER_PT
        return ascent, descent, ascent + descent

    @staticmethod
    def baseline_offset(line: float, ascent: float, descent: float) -> float:
        """Où tombe la ligne de base sous le haut d'une ligne exacte (règle mesurée)."""
        box = ascent + descent
        return line - descent if line >= box else line * ascent / box

    def place(
        self, target: float, line: float, cursor: float, key: str, scale: float = 1.0
    ) -> tuple[float, float, float]:
        """La ligne exacte et le blanc avant qui posent la ligne de base à `target`.

        Rend `(ligne, blanc avant, bas de la ligne)`. La ligne est raccourcie quand
        le haut calculé passerait au-dessus de `cursor` (le bas de la précédente).
        """
        ascent, descent, box = self.metrics(key, scale)
        top = target - self.baseline_offset(line, ascent, descent)
        if top < cursor:
            avail = max(target - cursor, 0.3)
            line = avail + descent if avail >= box - descent else max(1.0, avail * box / ascent)
            top = target - self.baseline_offset(line, ascent, descent)
        return line, max(0.0, top - cursor), max(top, cursor) + line

    # ------------------------------------------------------------------ paragraphe
    def para(
        self,
        container: Any,
        key: str,
        text: str,
        *,
        line_mm: float,
        space_mm: float = 0.0,
        align: str = "left",
        scale: float = 1.0,
    ) -> Any:
        """Un paragraphe au style du rôle, à la ligne exacte `line_mm`, après `space_mm` de blanc."""
        st = self.styles[key]
        p = container.add_paragraph()
        family, bold, italic = self.names.word_font(
            str(st["family"]), int(st["weight"]), bool(st.get("italic"))
        )
        shown = text.upper() if st.get("uppercase") else text
        run = p.add_run(shown)
        run.font.name = family
        rpr = run._r.get_or_add_rPr()
        fonts = rpr.get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), family)
        size = float(st["size_pt"]) * scale
        run.font.size = Pt(round(size * 2) / 2)
        run.font.bold, run.font.italic = bold, italic
        run.font.color.rgb = rgb(str(st["color"]))
        if st.get("letter_spacing_pt"):
            sp = OxmlElement("w:spacing")
            sp.set(qn("w:val"), str(round(float(st["letter_spacing_pt"]) * 20)))
            rpr.append(sp)
        pf = p.paragraph_format
        pf.space_before, pf.space_after = Mm(max(0.0, space_mm)), Pt(0)
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        pf.line_spacing = Mm(line_mm)
        pf.alignment = {
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }.get(align, WD_ALIGN_PARAGRAPH.LEFT)
        return p

    def footer_family(self) -> str:
        """La police du pied Karria : celle du plus petit corps du document."""
        smallest = min(
            (st for st in self.styles.values() if "box_h_mm" in st),
            key=lambda st: float(st["size_pt"]),
        )
        return self.names.word_font(str(smallest["family"]), 400, False)[0]

    # ------------------------------------------------------------------ en-tête
    def present(self, block: Json) -> list[tuple[Json, str]]:
        """Les éléments d'un bloc qui ont un texte."""
        return [
            (e, header_value(self.view, e["field"]))
            for e in block["elements"]
            if header_value(self.view, e["field"])
        ]

    def _stack(self, cell: Any, blocks: list[Json], top_mm: float, *, align: str) -> float:
        """Les blocs d'une colonne d'en-tête, empilés dans une cellule, chaque ligne de base à sa place."""
        cursor = top_mm
        for block in sorted(blocks, key=lambda b: float(b["y_mm"])):
            present = self.present(block)
            if not present:
                continue
            y = float(block["y_mm"])
            for e, text in present:
                ascent, _, _ = self.metrics(e["style"])
                line, space, bottom = self.place(
                    y + ascent, float(e["pitch_mm"]), cursor, e["style"]
                )
                self.para(cell, e["style"], text, line_mm=line, space_mm=space, align=align)
                cursor = bottom
                y += float(e["pitch_mm"])
        return cursor

    def header(self, top_mm: float) -> float:
        """L'en-tête en tableau sans bordure : gauche | droite, depuis la marge haute.

        Rend le bas de l'en-tête (mm) : là où le corps commence.
        """
        page_w = float(self.spec["page"]["width_mm"])
        body = self.spec["body"]
        blocks = self.spec["header"]["blocks"]
        left = [b for b in blocks if b["anchor"] != "right"]
        right = [b for b in blocks if b["anchor"] == "right"]
        if self.logo:
            p = self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p.paragraph_format.space_after = Pt(0)
            p.add_run().add_picture(io.BytesIO(self.logo), height=Mm(LOGO_HEIGHT_MM))
        body_x = float(body["x_mm"])
        text_w = float(body["w_mm"])
        table = self.doc.add_table(rows=1, cols=2)
        no_borders(table)
        split = min((float(b["x_mm"]) for b in left if b["anchor"] == "left"), default=body_x)
        right_edge = max((page_w - float(b["right_mm"]) for b in right), default=body_x + text_w)
        left_w = text_w * 0.6 if not right else min(text_w * 0.6, right_edge - split - 25)
        left_w = max(20.0, left_w)
        set_widths(table, [left_w, text_w - left_w], indent_mm=max(0.0, split - body_x))
        cells = table.rows[0].cells
        bottom = max(
            self._stack(cells[0], left, top_mm, align="left"),
            self._stack(cells[1], right, top_mm, align="right"),
            top_mm + 5.0,
        )
        for c in cells:
            if c.paragraphs and not c.paragraphs[0].runs and len(c.paragraphs) > 1:
                c.paragraphs[0]._p.getparent().remove(c.paragraphs[0]._p)
        row = table.rows[0]
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        row.height = Mm(bottom - top_mm)
        return bottom

    # ------------------------------------------------------------------ document
    def render(self) -> bytes:
        """Le DOCX complet, polices incorporées."""
        page = self.spec["page"]
        body = self.spec["body"]
        blocks = self.spec["header"]["blocks"]
        top_margin = float(body["top_mm"]) - 10.0
        first = min(blocks, key=lambda b: float(b["y_mm"]), default=None)
        if first is not None:
            e0 = first["elements"][0]
            ascent, descent, _ = self.metrics(e0["style"])
            top_margin = float(first["y_mm"]) + ascent
            top_margin -= self.baseline_offset(float(e0["pitch_mm"]), ascent, descent)
        top_margin = max(5.0, top_margin)
        sec = self.doc.sections[0]
        sec.page_width, sec.page_height = Mm(page["width_mm"]), Mm(page["height_mm"])
        sec.top_margin, sec.bottom_margin = Mm(top_margin), Mm(12)
        sec.left_margin = Mm(float(body["x_mm"]))
        sec.right_margin = Mm(float(page["width_mm"]) - float(body["x_mm"]) - float(body["w_mm"]))
        sec.footer_distance = docx_footer_distance()
        add_docx_footer(sec.footer.paragraphs[0], "letter", self.footer_family())
        normal = self.doc.styles["Normal"]
        normal.paragraph_format.space_after = Pt(0)
        normal.paragraph_format.space_before = Pt(0)
        normal.font.size = Pt(2)
        cursor = self.header(top_margin)
        box_bottom: float | None = None
        for block in body["blocks"]:
            texts = block_texts(self.view, block)
            if not texts:
                continue
            key = block["style"]
            st = self.styles[key]
            pitch = float(st.get("pitch_mm") or st["box_h_mm"]) * self.f
            ascent, _, box = self.metrics(key, self.f)
            for i, (text, _) in enumerate(texts):
                if box_bottom is None:
                    top = float(body["top_mm"])
                elif i == 0:
                    top = box_bottom + float(block["gap_before_mm"]) * self.k
                else:
                    top = box_bottom + float(block.get("gap_between_mm") or pitch) * self.k
                line, space, bottom = self.place(top + ascent, pitch, cursor, key, self.f)
                self.para(
                    self.doc,
                    key,
                    text,
                    line_mm=line,
                    space_mm=space,
                    align=block["align"],
                    scale=self.f,
                )
                lines = max(1, self.wrap(text, key, float(body["w_mm"])))
                cursor = bottom + line * (lines - 1)
                box_bottom = top + box + line * (lines - 1)
        h = self.view["header"]
        props = self.doc.core_properties
        props.title = f"Lettre de motivation — {h['full_name']}".strip(" —")
        props.author = h["full_name"]
        buffer = io.BytesIO()
        self.doc.save(buffer)
        return embed_fonts(buffer.getvalue(), self.names)

    def wrap(self, text: str, key: str, width_mm: float) -> int:
        """Le nombre de lignes qu'un paragraphe occupe, estimé avec la police servie."""
        st = self.styles[key]
        words = text.split()
        if not words:
            return 1
        lines, current = 1, ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if (
                current
                and self.fonts.width_mm(candidate, st, size_pt=float(st["size_pt"]) * self.f)
                > width_mm
            ):
                lines += 1
                current = word
            else:
                current = candidate
        return lines
