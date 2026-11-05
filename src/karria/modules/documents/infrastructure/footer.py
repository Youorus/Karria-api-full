"""Le pied de page Karria des documents exportés.

Une ligne discrète au bas de la dernière page — « CV réalisé avec Karria ·
karria.fr/app » — en petit (7 pt), gris doux, le lien cliquable dans le PDF.
C'est la seule marque que porte un document qui quitte l'app : ni logo, ni
bandeau. Quand un recruteur ou un proche reçoit le fichier, il sait d'où il
vient et où trouver Karria ; rien de plus.

En HTML, la ligne est posée en absolu au bas de la feuille `.page` : elle
ne participe pas au flux, ne déplace aucun texte et ne change pas la
pagination estimée (`estimate_pages`) ni réelle (le script d'ajustement
mesure les `.t`, pas elle). Quand la feuille prend n hauteurs de page, le
bas de `.page` est le bas de la dernière page. Le script d'ajustement
réserve au moins 6 mm en bas de la première page (`page_limit_mm`) : la
ligne tient dessous. Elle vaut aussi pour les documents « ATS safe » : un
logiciel de tri y lit une phrase, pas un bruit.

En DOCX, Word ne connaît pas « la dernière page » : la ligne est le pied de
section, dans la marge basse, sur chaque page — un CV tient sur une page
dans l'immense majorité des cas.
"""

from __future__ import annotations

from typing import Any, Literal
from xml.sax.saxutils import escape

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from docx.text.paragraph import Paragraph

Kind = Literal["cv", "letter"]

FOOTER_LABEL: dict[Kind, str] = {
    "cv": "CV réalisé avec Karria",
    "letter": "Lettre réalisée avec Karria",
}
FOOTER_LINK_TEXT = "karria.fr/app"
FOOTER_SIZE_PT = 7.0
FOOTER_COLOR = "#9a9fa6"
FOOTER_BOTTOM_MM = 3.0
FONT_STACK = "Helvetica, Arial, 'Liberation Sans', sans-serif"


def footer_url(kind: Kind) -> str:
    """Le lien intelligent du site, avec l'origine du document."""
    return f"https://karria.fr/app?utm_source=document&utm_medium={kind}"


def footer_text(kind: Kind) -> str:
    """« CV réalisé avec Karria · karria.fr/app », tel qu'un lecteur le lit."""
    return f"{FOOTER_LABEL[kind]} · {FOOTER_LINK_TEXT}"


def footer_html(kind: Kind) -> str:
    """La ligne, en absolu au bas de `.page`, hors du flux et des ancres."""
    style = (
        f"position:absolute;left:0;right:0;bottom:{FOOTER_BOTTOM_MM}mm;text-align:center;"
        f"font:{FOOTER_SIZE_PT}pt {FONT_STACK};color:{FOOTER_COLOR};line-height:1;"
        "letter-spacing:0.02em;pointer-events:auto"
    )
    return (
        f'<div class="karria-pied" style="{style}">{escape(FOOTER_LABEL[kind])} · '
        f'<a href="{escape(footer_url(kind))}" style="color:{FOOTER_COLOR};text-decoration:none">'
        f"{FOOTER_LINK_TEXT}</a></div>"
    )


def _run(text: str, family: str) -> Any:
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), family)
    rpr.append(fonts)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), FOOTER_COLOR.lstrip("#").upper())
    rpr.append(color)
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(int(FOOTER_SIZE_PT * 2)))
    rpr.append(size)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)
    return run


def add_docx_footer(paragraph: Paragraph, kind: Kind, family: str = "Arial") -> None:
    """Pose la ligne dans `paragraph` (le pied de section), lien compris."""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    label = paragraph.add_run(f"{FOOTER_LABEL[kind]} · ")
    label.font.name = family
    label.font.size = Pt(FOOTER_SIZE_PT)
    label.font.color.rgb = RGBColor.from_string(FOOTER_COLOR.lstrip("#").upper())
    part = paragraph.part
    rid = part.relate_to(footer_url(kind), RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    link.append(_run(FOOTER_LINK_TEXT, family))
    paragraph._p.append(link)


def docx_footer_distance() -> Mm:
    """La distance du pied au bord : la ligne reste dans la marge basse."""
    return Mm(FOOTER_BOTTOM_MM)
