"""Inspection des fichiers reçus : ce qu'ils sont vraiment, avant jugement.

Implémente le port `UploadInspector`. Trois formats demandent de lire les
octets :

- **PDF** — compter les pages (`pypdf`, pur Python, seule dépendance
  ajoutée par ce module). Le contenu part ensuite tel quel à Gemini, qui
  lit le PDF nativement : aucune extraction de texte locale, qui perdrait
  la mise en page dont le modèle se sert pour distinguer les sections.
- **DOCX** — Gemini ne le lit pas. Un DOCX est une archive ZIP dont
  `word/document.xml` porte le texte ; la bibliothèque standard suffit à
  l'en sortir, paragraphe par paragraphe. La mise en page est perdue, c'est
  le prix de ne pas convertir en PDF — acceptable pour un CV Word, dont le
  texte suit l'ordre de lecture.
- **M4A** — la durée se lit dans l'atome `mvhd` du conteneur MP4, sans
  décoder l'audio. Les autres formats audio repartent sans durée connue :
  la taille les borne.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from typing import Final

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from karria.modules.cv.domain import (
    DOCX_CONTENT_TYPE,
    AudioInfo,
    DocumentInfo,
    UnsupportedUploadError,
)
from karria.shared.images import sniff_content_type
from karria.shared.logging import get_logger

logger = get_logger(__name__)

_WORD_NS: Final = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
#: Un `document.xml` de CV pèse quelques dizaines de kilooctets. Au-delà
#: de cette borne, décompresser serait servir une bombe ZIP.
_MAX_DOCX_XML_BYTES: Final = 20 * 1024 * 1024
#: En dessous, la couche texte d'un PDF est un titre ou du bruit : le
#: document est un scan, et c'est l'image qu'il faut lire.
MIN_TEXT_LAYER_CHARS: Final = 200
_MP4_TYPES: Final[frozenset[str]] = frozenset(
    {"audio/m4a", "audio/x-m4a", "audio/mp4", "audio/aac"}
)


class FileInspector:
    """Implémente `UploadInspector` avec `pypdf`, `zipfile` et `struct`."""

    def document(self, payload: bytes, content_type: str) -> DocumentInfo:
        """Inspecte un document selon son type annoncé.

        Raises:
            UnsupportedUploadError: Les octets ne se lisent pas comme annoncé.
        """
        if content_type == "application/pdf":
            pages, text = _pdf_pages_and_text(payload)
            if len("".join(text.split())) >= MIN_TEXT_LAYER_CHARS:
                return DocumentInfo(
                    payload=text.encode("utf-8"), content_type="text/plain", pages=pages
                )
            return DocumentInfo(payload=payload, content_type=content_type, pages=pages)
        if content_type == DOCX_CONTENT_TYPE:
            text = _docx_text(payload)
            return DocumentInfo(payload=text.encode("utf-8"), content_type="text/plain")
        if content_type.startswith("image/"):
            sniffed = sniff_content_type(payload)
            if sniffed is None:
                raise UnsupportedUploadError(
                    "Ce fichier n'est pas une image lisible.",
                    reason="document_unreadable",
                    field="file",
                )
            return DocumentInfo(payload=payload, content_type=sniffed, pages=1)
        return DocumentInfo(payload=payload, content_type=content_type)

    def audio(self, payload: bytes, content_type: str) -> AudioInfo:
        """Inspecte un enregistrement : la durée quand le conteneur la donne."""
        duration = _mp4_duration(payload) if content_type in _MP4_TYPES else None
        return AudioInfo(content_type=content_type, duration_seconds=duration)


def _pdf_pages_and_text(payload: bytes) -> tuple[int, str]:
    """Le nombre de pages et la couche texte d'un PDF.

    La couche texte est préférée au PDF lui-même quand elle existe :
    constaté le 2026-10-21, Gemini lit un PDF joint comme une image et en
    perd la moitié (une expérience sur deux, aucune compétence), alors
    qu'il extrait tout du même contenu en texte. Un PDF scanné n'a pas de
    couche texte : il part tel quel, et le modèle le lit comme une image.
    """
    try:
        reader = PdfReader(BytesIO(payload))
        if reader.is_encrypted:
            # Un PDF « protégé » sans mot de passe s'ouvre avec la clé vide ;
            # un vrai mot de passe nous arrête ici, et c'est normal.
            reader.decrypt("")
        pages = list(reader.pages)
        text = "\n".join(page.extract_text() or "" for page in pages)
        return len(pages), text
    except (PyPdfError, ValueError, TypeError, KeyError, OSError) as exc:
        logger.info("cv.pdf_unreadable", error=type(exc).__name__)
        raise UnsupportedUploadError(
            "Ce PDF ne se lit pas. Essaie de l'exporter à nouveau, ou envoie une photo du CV.",
            reason="document_unreadable",
            field="file",
        ) from exc


def _docx_text(payload: bytes) -> str:
    """Le texte d'un DOCX, paragraphe par paragraphe.

    `xml.etree` et non `defusedxml` : l'archive est bornée en taille avant
    décompression, le document vient d'un utilisateur authentifié, et
    l'expat livré avec Python 3.13 plafonne l'expansion d'entités. Ajouter
    une dépendance pour ce seul cas ne se justifie pas.
    """
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > _MAX_DOCX_XML_BYTES:
                raise UnsupportedUploadError(
                    "Ce document est anormalement volumineux.",
                    reason="document_too_large",
                    field="file",
                )
            root = ET.fromstring(archive.read(info))  # noqa: S314 - borné, voir docstring
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        logger.info("cv.docx_unreadable", error=type(exc).__name__)
        raise UnsupportedUploadError(
            "Ce document Word ne se lit pas.", reason="document_unreadable", field="file"
        ) from exc

    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_WORD_NS}p"):
        pieces: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_WORD_NS}t" and node.text:
                pieces.append(node.text)
            elif node.tag == f"{_WORD_NS}tab":
                pieces.append("\t")
            elif node.tag in {f"{_WORD_NS}br", f"{_WORD_NS}cr"}:
                pieces.append("\n")
        text = "".join(pieces).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _mp4_duration(payload: bytes) -> float | None:
    """La durée d'un conteneur MP4/M4A, lue dans `moov/mvhd`. `None` si absente."""
    moov = _find_box(payload, 0, len(payload), b"moov")
    if moov is None:
        return None
    start, end = moov
    mvhd = _find_box(payload, start + 8, end, b"mvhd")
    if mvhd is None:
        return None
    offset = mvhd[0]
    try:
        version = payload[offset + 8]
        if version == 0:
            timescale, duration = struct.unpack_from(">II", payload, offset + 20)
        else:
            (timescale,) = struct.unpack_from(">I", payload, offset + 28)
            (duration,) = struct.unpack_from(">Q", payload, offset + 32)
    except (struct.error, IndexError):
        return None
    if timescale == 0:
        return None
    return float(duration) / float(timescale)


def _find_box(payload: bytes, start: int, end: int, wanted: bytes) -> tuple[int, int] | None:
    """Parcourt les boîtes entre `start` et `end` et rend (début, fin) de `wanted`."""
    offset = start
    while offset + 8 <= end:
        (size,) = struct.unpack_from(">I", payload, offset)
        kind = payload[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > end:
                return None
            (size,) = struct.unpack_from(">Q", payload, offset + 8)
            header = 16
        elif size == 0:
            size = end - offset
        if size < header:
            return None
        if kind == wanted:
            return offset, min(offset + size, end)
        offset += size
    return None
