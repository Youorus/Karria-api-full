"""Lire un document comme un ATS : PDF par pdfminer, DOCX par son XML brut.

Contrat
-------
- PDF : `pdfminer.six` (ce qu'emploient beaucoup d'analyseurs de CV), mise en
  page par défaut ; les types de police (`Type3` repéré) et leur incorporation
  sont lus dans les ressources de chaque page.
- DOCX : le XML de `word/document.xml`, paragraphe par paragraphe, dans
  l'ordre du document, comme le fait un analyseur de CV ; les tableaux et les
  images sont comptés.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from collections import Counter

from pdfminer.high_level import extract_text
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import resolve1

from karria.modules.documents.domain.ats import ExtractedDocument


def read_pdf(data: bytes) -> ExtractedDocument:
    """Ce qu'un ATS lit d'un PDF."""
    text = extract_text(io.BytesIO(data))
    types: Counter[str] = Counter()
    embedded = True
    images = 0
    pages = 0
    parser = PDFParser(io.BytesIO(data))
    document = PDFDocument(parser)
    for page in PDFPage.create_pages(document):
        pages += 1
        resources = resolve1(page.resources) or {}
        fonts = resolve1(resources.get("Font")) or {}
        for ref in fonts.values():
            font = resolve1(ref) or {}
            subtype = getattr(resolve1(font.get("Subtype")), "name", str(font.get("Subtype")))
            types[subtype] += 1
            descriptor = resolve1(font.get("FontDescriptor")) or {}
            if subtype == "Type0":
                descendants = resolve1(font.get("DescendantFonts")) or []
                if descendants:
                    descriptor = resolve1(resolve1(descendants[0]).get("FontDescriptor")) or {}
            if subtype != "Type3" and not any(
                k in descriptor for k in ("FontFile", "FontFile2", "FontFile3")
            ):
                embedded = False
        xobjects = resolve1(resources.get("XObject")) or {}
        for ref in xobjects.values():
            obj = resolve1(ref)
            subtype = getattr(obj, "attrs", {}).get("Subtype") if obj is not None else None
            if getattr(subtype, "name", None) == "Image":
                images += 1
    return ExtractedDocument(
        text=text,
        format="pdf",
        pages=pages,
        font_types=dict(types),
        embedded=embedded,
        images=images,
        tables=0,
    )


def read_docx(data: bytes) -> ExtractedDocument:
    """Ce qu'un ATS lit d'un DOCX : texte des paragraphes dans l'ordre du document."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    lines = []
    for paragraph in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        runs = re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>|<w:tab/>", paragraph, flags=re.S)
        lines.append("".join(r if r else "\t" for r in runs))
    text = html.unescape("\n".join(line for line in lines if line.strip()))
    return ExtractedDocument(
        text=text,
        format="docx",
        pages=None,
        font_types={},
        embedded=True,
        images=len([m for m in media if not m.endswith(".odttf")]),
        tables=xml.count("<w:tbl>"),
    )
