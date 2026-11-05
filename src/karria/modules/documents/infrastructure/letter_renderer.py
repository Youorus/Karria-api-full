"""La lettre de motivation, HTML et DOCX, dans la grammaire du modèle de CV (ADR-0030).

Contrat
-------
- **Rien n'est déclaré, tout est lu dans la spécification** : la page et ses
  marges (la colonne de texte principale du modèle), la famille et le corps du
  texte courant (rôle `item_text`), la couleur d'accent (titres de rubrique)
  pour l'objet, le style du nom pour la signature. Un CV et sa lettre rendus
  dans le même modèle se lisent comme un dossier.
- **Une page.** Le même script d'ajustement que le CV resserre les blancs
  puis le corps ; au-delà, la seconde page est assumée.
- **Même vue pour les deux moteurs** (`domain/letter.py`), même résolveur
  d'adresses et mêmes ancres `data-chemin` que `HtmlRenderer` (ADR-0029).
- Le logo est un fichier joint (`logo.png`) : présent, il se pose en tête à
  droite, à hauteur bornée ; absent, rien, sans blanc.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any, Final

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from karria.modules.documents.domain.headline import MM_PER_PT, ats_safe_spacing
from karria.modules.documents.infrastructure.docx_renderer import FontNames, embed_fonts, rgb
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.footer import (
    add_docx_footer,
    docx_footer_distance,
    footer_html,
)
from karria.modules.documents.infrastructure.html_renderer import (
    anchor,
    esc,
    fit_script,
    identity_url,
    style_key,
)

Json = dict[str, Any]

LOGO_FILE: Final = "logo.png"
#: Marges verticales d'une lettre (mm) : la feuille de route des recruteurs
#: français, et de quoi respirer en bas.
TOP_MM: Final = 20.0
#: Hauteur maximale du logo (mm) : un en-tête, pas une affiche.
LOGO_HEIGHT_MM: Final = 14.0
#: Corps du texte courant d'une lettre, borné : un CV à 8,5 pt est lisible
#: en liste, pas en paragraphes.
BODY_MIN_PT: Final = 10.0
BODY_MAX_PT: Final = 11.5
NAME_MAX_PT: Final = 20.0
SIGNATURE_MAX_PT: Final = 13.0
CONTACT_MIN_PT: Final = 8.5
#: Interligne des paragraphes, en multiple du corps.
LEADING: Final = 1.45
#: Blancs entre les blocs (mm), multipliés par le facteur d'ajustement.
GAP_HEADER_MM: Final = 10.0
GAP_SUBJECT_MM: Final = 8.0
GAP_PARAGRAPH_MM: Final = 4.0
GAP_SIGNATURE_MM: Final = 10.0
#: Marges latérales d'une lettre : par défaut sans modèle, plancher et plafond (mm).
DEFAULT_MARGIN_MM: Final = 20.0
MIN_MARGIN_MM: Final = 12.0
MAX_MARGIN_MM: Final = 25.0


def letter_frame(spec: Json) -> tuple[float, float, float]:
    """(gauche, largeur, droite) de la colonne de texte d'une lettre, en mm.

    Les **marges extérieures** du modèle : la première colonne fixe la gauche,
    la dernière la droite (bornée à 25 mm : un modèle à large bande de couleur
    ne fait pas une lettre étroite). Une lettre à deux colonnes n'existe pas ;
    ce qui doit se reconnaître, c'est la page, pas la grille.
    """
    page_w = float(spec["page"]["width_mm"])
    columns = [c for b in spec["bands"] for c in b["columns"]]
    if not columns:
        return DEFAULT_MARGIN_MM, page_w - 2 * DEFAULT_MARGIN_MM, DEFAULT_MARGIN_MM
    left = max(min(float(c["x_mm"]) for c in columns), MIN_MARGIN_MM)
    outer_right = page_w - max(float(c["x_mm"]) + float(c["w_mm"]) for c in columns)
    right = min(max(outer_right, left), MAX_MARGIN_MM)
    return left, page_w - left - right, right


def letter_styles(spec: Json) -> dict[str, Json]:
    """Les styles d'une lettre, dérivés de ceux du modèle."""
    styles: dict[str, Json] = spec["styles"]
    body = dict(styles[style_key(styles, "experiences", "item_text")])
    body["size_pt"] = round(min(max(float(body["size_pt"]), BODY_MIN_PT), BODY_MAX_PT), 1)
    body["uppercase"] = False
    body["letter_spacing_pt"] = 0.0
    headers = [k for k in styles if k.startswith("header.")]
    name_key = "header.name" if "header.name" in styles else None
    if name_key is None and headers:
        name_key = max(headers, key=lambda k: float(styles[k]["size_pt"]))
    name = dict(styles[name_key]) if name_key else {**body, "weight": 700}
    name["size_pt"] = round(min(float(name["size_pt"]), NAME_MAX_PT), 1)
    name["letter_spacing_pt"] = ats_safe_spacing(name)
    title = styles["section_title"]
    accent = title["color"]
    headline = dict(styles.get("header.headline") or {**body, "weight": 500, "color": accent})
    headline["size_pt"] = round(max(float(headline["size_pt"]), body["size_pt"]), 1)
    headline["letter_spacing_pt"] = ats_safe_spacing(headline)
    contact = dict(styles[style_key(styles, "contact", "email")])
    contact["size_pt"] = round(max(float(contact["size_pt"]), CONTACT_MIN_PT), 1)
    contact["uppercase"] = False
    contact["letter_spacing_pt"] = 0.0
    subject = {
        **title,
        "size_pt": round(max(body["size_pt"] * 1.1, float(title["size_pt"]) * 0.9), 1),
        "uppercase": False,
        "letter_spacing_pt": ats_safe_spacing(title),
        "color": accent,
    }
    signature = {**name, "size_pt": round(min(float(name["size_pt"]), SIGNATURE_MAX_PT), 1)}
    return {
        "body": body,
        "name": name,
        "headline": headline,
        "contact": contact,
        "subject": subject,
        "signature": signature,
        "recipient": {**body, "weight": 600 if int(body["weight"]) < 600 else body["weight"]},  # noqa: PLR2004
    }


def contact_line(header: Json) -> list[tuple[str, str]]:
    """Les coordonnées affichées, (champ, texte), sans les vides."""
    return [(f, header[f]) for f in ("phone", "email", "address") if header.get(f)]


class LetterHtmlRenderer:
    """Rend une lettre en HTML imprimable, aux polices et couleurs du modèle."""

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
        self.styles = letter_styles(spec)
        self.paths: Json = view.get("paths") or {}

    def anchor(self, path: str | None) -> str:
        """`data-chemin` en mode ancré, rien sinon."""
        return anchor(path) if self.anchored else ""

    def css_style(self, name: str, st: Json) -> str:
        """Une classe par style de lettre ; le corps suit `--f`."""
        size = f"calc({st['size_pt']}pt*var(--f))"
        lead = f"calc({float(st['size_pt']) * LEADING * MM_PER_PT:.3f}mm*var(--f))"
        return (
            f".l-{name}{{font-family:'{st['family']}',sans-serif;font-weight:{st['weight']};"
            f"font-style:{'italic' if st.get('italic') else 'normal'};font-size:{size};"
            f"color:{st['color']};letter-spacing:{st['letter_spacing_pt']}pt;line-height:{lead};"
            f"text-transform:{'uppercase' if st.get('uppercase') else 'none'}}}"
        )

    def font_faces(self) -> str:
        """`@font-face` des instances statiques servies."""
        return "".join(
            f"@font-face{{font-family:'{f.family}';src:url('{self.url(f.file_name)}');"
            f"font-weight:{f.weight};font-style:{'italic' if f.italic else 'normal'}}}"
            for f in self.fonts.files
        )

    def block(
        self, style: str, text: str, *, path: str | None, gap: float = 0.0, extra: str = ""
    ) -> str:
        """Un bloc de texte mesurable (`.t`), avec son blanc avant."""
        margin = f"margin-top:calc({gap:.2f}mm*var(--k));" if gap else ""
        return (
            f'<div class="t l-{style}"{self.anchor(path)} style="{margin}{extra}">{esc(text)}</div>'
        )

    def header(self) -> str:
        """Expéditeur à gauche ; logo et destinataire à droite."""
        h = self.view["header"]
        hp: Json = self.paths.get("header", {})
        left = [self.block("name", h["full_name"], path=hp.get("full_name"))]
        if h.get("headline"):
            left.append(self.block("headline", h["headline"], path=hp.get("headline")))
        left.extend(
            self.block("contact", text, path=hp.get(field)) for field, text in contact_line(h)
        )
        r = self.view["recipient"]
        rp: Json = self.paths.get("recipient", {})
        right: list[str] = []
        if self.logo:
            right.append(
                f'<img src="{esc(self.url(self.logo))}" alt="" style="display:block;margin-left:auto;'
                f'max-height:{LOGO_HEIGHT_MM}mm;max-width:40mm;margin-bottom:3mm">'
            )
        if r.get("company"):
            right.append(self.block("recipient", r["company"], path=rp.get("company")))
        if r.get("city"):
            right.append(self.block("body", r["city"], path=rp.get("city")))
        return (
            '<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div style="max-width:60%">{"".join(left)}</div>'
            f'<div style="text-align:right;max-width:38%">{"".join(right)}</div></div>'
        )

    def body(self) -> str:
        """Date, objet, formule d'appel, paragraphes, politesse, signature."""
        v, p = self.view, self.paths
        out = [
            self.block(
                "body", v["date_line"], path=None, gap=GAP_HEADER_MM, extra="text-align:right;"
            ),
            self.block(
                "subject",
                f"Objet : {v['subject']}" if v["subject"] else "",
                path=p.get("subject"),
                gap=GAP_SUBJECT_MM,
            )
            if v["subject"]
            else "",
            self.block("body", v["greeting"], path=p.get("greeting"), gap=GAP_SUBJECT_MM),
        ]
        paths = p.get("paragraphs") or []
        out.extend(
            self.block(
                "body",
                para["text"],
                path=paths[i] if i < len(paths) else None,
                gap=GAP_PARAGRAPH_MM,
                extra="text-align:justify;",
            )
            for i, para in enumerate(v["paragraphs"])
        )
        out.append(
            self.block(
                "body",
                v["closing"],
                path=p.get("closing"),
                gap=GAP_PARAGRAPH_MM,
                extra="text-align:justify;",
            )
        )
        out.append(
            self.block("signature", v["signature"], path=p.get("signature"), gap=GAP_SIGNATURE_MM)
        )
        return "".join(out)

    def render(self) -> str:
        """Le document HTML complet."""
        page = self.spec["page"]
        left, width, _ = letter_frame(self.spec)
        css = [
            self.font_faces(),
            f"@page{{size:{page['width_mm']}mm {page['height_mm']}mm;margin:0}}",
            "html,body{margin:0;padding:0}*{box-sizing:border-box}",
            ".t{white-space:pre-wrap;overflow-wrap:anywhere;position:relative}",
            ":root{--k:1;--f:1}",
            f".page{{position:relative;width:{page['width_mm']}mm;height:{page['height_mm']}mm;overflow:hidden;background:{page['background']}}}",
            f".sheet{{position:absolute;left:{left:.2f}mm;top:{TOP_MM}mm;width:{width:.2f}mm}}",
            *(self.css_style(k, v) for k, v in self.styles.items()),
        ]
        return (
            f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Lettre</title><style>{"".join(css)}</style></head>'
            f'<body><div class="page"><div class="sheet">{self.header()}{self.body()}</div>'
            f"{footer_html('letter')}</div>{fit_script(self.spec, live=self.anchored)}</body></html>"
        )


class LetterDocxRenderer:
    """Rend une lettre en DOCX modifiable, polices incorporées, aux mêmes mesures."""

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
        self.styles = letter_styles(spec)
        self.names = FontNames(fonts)
        self.doc = Document()

    def para(self, style: str, text: str, *, before_mm: float = 0.0, align: str = "left") -> None:
        """Un paragraphe au style de lettre."""
        st = self.styles[style]
        p = self.doc.add_paragraph()
        family, bold, italic = self.names.word_font(
            str(st["family"]), int(st["weight"]), bool(st.get("italic"))
        )
        run = p.add_run(text)
        run.font.name = family
        rpr = run._r.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), family)
        size = float(st["size_pt"]) * self.f
        run.font.size = Pt(round(size * 2) / 2)
        run.font.bold, run.font.italic = bold, italic
        run.font.color.rgb = rgb(str(st["color"]))
        if st.get("letter_spacing_pt"):
            sp = OxmlElement("w:spacing")
            sp.set(qn("w:val"), str(round(float(st["letter_spacing_pt"]) * 20)))
            rpr.append(sp)
        pf = p.paragraph_format
        pf.space_before, pf.space_after = Mm(before_mm * self.k), Pt(0)
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        pf.line_spacing = Mm(size * LEADING * MM_PER_PT)
        pf.alignment = {
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
            "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }.get(align, WD_ALIGN_PARAGRAPH.LEFT)

    def footer_family(self) -> str:
        """La police du pied Karria : celle du plus petit corps du document."""
        smallest = min(self.styles.values(), key=lambda st: float(st["size_pt"]))
        return self.names.word_font(str(smallest["family"]), 400, False)[0]

    def render(self) -> bytes:
        """Le DOCX complet, polices incorporées."""
        page = self.spec["page"]
        left, _, right = letter_frame(self.spec)
        sec = self.doc.sections[0]
        sec.page_width, sec.page_height = Mm(page["width_mm"]), Mm(page["height_mm"])
        sec.top_margin, sec.bottom_margin = Mm(TOP_MM), Mm(15)
        sec.left_margin, sec.right_margin = Mm(left), Mm(right)
        sec.footer_distance = docx_footer_distance()
        add_docx_footer(sec.footer.paragraphs[0], "letter", self.footer_family())
        normal = self.doc.styles["Normal"]
        normal.paragraph_format.space_after = Pt(0)
        normal.paragraph_format.space_before = Pt(0)
        v, h = self.view, self.view["header"]
        if self.logo:
            p = self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p.add_run().add_picture(io.BytesIO(self.logo), height=Mm(LOGO_HEIGHT_MM))
        self.para("name", h["full_name"])
        if h.get("headline"):
            self.para("headline", h["headline"])
        for _, text in contact_line(h):
            self.para("contact", text)
        r = v["recipient"]
        if r.get("company"):
            self.para("recipient", r["company"], before_mm=GAP_PARAGRAPH_MM, align="right")
        if r.get("city"):
            self.para("body", r["city"], align="right")
        self.para("body", v["date_line"], before_mm=GAP_HEADER_MM, align="right")
        if v["subject"]:
            self.para("subject", f"Objet : {v['subject']}", before_mm=GAP_SUBJECT_MM)
        self.para("body", v["greeting"], before_mm=GAP_SUBJECT_MM)
        for para in v["paragraphs"]:
            self.para("body", para["text"], before_mm=GAP_PARAGRAPH_MM, align="justify")
        self.para("body", v["closing"], before_mm=GAP_PARAGRAPH_MM, align="justify")
        self.para("signature", v["signature"], before_mm=GAP_SIGNATURE_MM)
        props = self.doc.core_properties
        props.title = f"Lettre de motivation — {h['full_name']}".strip(" —")
        props.author = h["full_name"]
        buffer = io.BytesIO()
        self.doc.save(buffer)
        return embed_fonts(buffer.getvalue(), self.names)
