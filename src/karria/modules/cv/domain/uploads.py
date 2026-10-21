"""Ce qu'on accepte de recevoir : types, tailles, pages, durée.

Contrat
-------
Ce sont des **règles**, pas de la configuration : un CV de cent pages
n'est pas un CV, une note vocale d'une heure n'est pas une réponse. Elles
vivent donc dans le domaine, avec leur justification, et non dans une
variable d'environnement qu'on monterait un jour « pour voir ».

Les plafonds d'octets restent sous la limite de 20 Mo d'une requête
Gemini — le fichier y part encodé en base64, soit un tiers de plus.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from karria.modules.cv.domain.errors import UnsupportedUploadError

#: Ce que Gemini lit nativement, plus le DOCX que l'on convertit en texte.
DOCX_CONTENT_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SUPPORTED_DOCUMENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        DOCX_CONTENT_TYPE,
        "text/plain",
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
    }
)
#: Un CV tient en quelques pages ; au-delà, c'est un portfolio ou une erreur.
MAX_DOCUMENT_BYTES: Final = 10 * 1024 * 1024
MAX_DOCUMENT_PAGES: Final = 10

#: Formats des enregistreurs mobiles (expo-audio produit du M4A) et des
#: navigateurs. Le type annoncé varie d'un client à l'autre pour le même
#: conteneur, d'où les doublons.
SUPPORTED_AUDIO_TYPES: Final[frozenset[str]] = frozenset(
    {
        "audio/m4a",
        "audio/x-m4a",
        "audio/mp4",
        "audio/aac",
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/flac",
    }
)
#: Une réponse à une question de CV dure quelques dizaines de secondes.
#: Cinq minutes laissent une large marge sans ouvrir la porte à un podcast.
MAX_AUDIO_BYTES: Final = 10 * 1024 * 1024
MAX_AUDIO_SECONDS: Final = 300.0

#: Ce qu'on lit du type annoncé : `audio/m4a; codecs=mp4a` → `audio/m4a`.
MIN_AUDIO_BYTES: Final = 1024


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    """Un document inspecté, prêt pour l'extraction.

    Attributes:
        payload: Les octets à transmettre — ceux reçus, ou le texte extrait
            d'un DOCX, que le modèle ne lit pas nativement.
        content_type: Le type réel des octets transmis.
        pages: Nombre de pages, quand le format en a.
    """

    payload: bytes
    content_type: str
    pages: int | None = None


@dataclass(frozen=True, slots=True)
class AudioInfo:
    """Un enregistrement inspecté."""

    content_type: str
    duration_seconds: float | None = None


def plain_content_type(declared: str | None) -> str:
    """Le type MIME sans paramètres, en minuscules."""
    return (declared or "").split(";")[0].strip().lower()


def check_document_type(content_type: str) -> None:
    """Refuse un type de document hors liste, avant de lire le moindre octet."""
    if content_type not in SUPPORTED_DOCUMENT_TYPES:
        raise UnsupportedUploadError(
            "Ce type de fichier n'est pas accepté : envoie un PDF, un DOCX ou une image.",
            reason="document_type_unsupported",
            field="file",
        )


def check_document_size(size: int) -> None:
    if size == 0:
        raise UnsupportedUploadError("Le fichier est vide.", reason="document_empty", field="file")
    if size > MAX_DOCUMENT_BYTES:
        raise UnsupportedUploadError(
            f"Le fichier dépasse {MAX_DOCUMENT_BYTES // (1024 * 1024)} Mo.",
            reason="document_too_large",
            field="file",
        )


def check_document(info: DocumentInfo) -> None:
    """Applique les règles à un document inspecté.

    Raises:
        UnsupportedUploadError: Trop de pages, ou rien d'exploitable.
    """
    if info.pages is not None and info.pages > MAX_DOCUMENT_PAGES:
        raise UnsupportedUploadError(
            f"Le document fait {info.pages} pages ; un CV en compte {MAX_DOCUMENT_PAGES} au plus.",
            reason="document_too_many_pages",
            field="file",
        )
    if info.pages == 0 or not info.payload.strip():
        raise UnsupportedUploadError(
            "Le document ne contient rien de lisible.", reason="document_empty", field="file"
        )


def check_audio_type(content_type: str) -> None:
    if content_type not in SUPPORTED_AUDIO_TYPES:
        raise UnsupportedUploadError(
            "Ce format audio n'est pas accepté.",
            reason="audio_type_unsupported",
            field="audio_file",
        )


def check_audio_size(size: int) -> None:
    if size < MIN_AUDIO_BYTES:
        raise UnsupportedUploadError(
            "L'enregistrement est vide ou trop court.", reason="audio_empty", field="audio_file"
        )
    if size > MAX_AUDIO_BYTES:
        raise UnsupportedUploadError(
            f"L'enregistrement dépasse {MAX_AUDIO_BYTES // (1024 * 1024)} Mo.",
            reason="audio_too_large",
            field="audio_file",
        )


def check_audio(info: AudioInfo) -> None:
    """Applique les règles à un enregistrement inspecté."""
    if info.duration_seconds is not None and info.duration_seconds > MAX_AUDIO_SECONDS:
        raise UnsupportedUploadError(
            f"L'enregistrement dépasse {int(MAX_AUDIO_SECONDS // 60)} minutes.",
            reason="audio_too_long",
            field="audio_file",
        )
