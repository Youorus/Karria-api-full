"""Ce qu'une image normalisée est, et ce qu'on lui demande.

Le socle technique des images (ADR-0009). Aucun métier ici : un
avatar, une pièce d'identité et une photo de colis sont trois images, et
ce module ne sait pas les distinguer. Ce qui les distingue — jusqu'où
réduire, à quelle qualité — est une **politique**, portée par le bucket
qui les reçoit (`shared/storage/buckets.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Formats que l'on **écrit**. Tout ce qui entre et n'en fait pas partie
#: est converti. Trois formats que tout navigateur et tout téléphone
#: affichent sans conversion ; le HEIC, lui, ne s'ouvre que sur Apple.
OUTPUT_CONTENT_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp"})

#: Plafond de pixels décodés, tous usages confondus. Une image qui
#: prétend faire 40 000 x 40 000 pixels tient dans quelques kilooctets
#: compressés et occupe des gigaoctets une fois ouverte : c'est la
#: « bombe de décompression », et on la refuse **avant** de décoder.
MAX_PIXELS: Final = 50_000_000


@dataclass(frozen=True, slots=True)
class ImagePolicy:
    """Jusqu'où réduire une image, et à quel prix en qualité.

    Attributes:
        max_edge: Plus grande dimension conservée, en pixels. Au-delà,
            l'image est réduite en gardant ses proportions.
        quality: Qualité JPEG de départ. On descend par paliers si
            l'image ne tient pas dans le plafond du bucket.
        min_quality: Sous ce seuil, on préfère réduire les dimensions
            plutôt que d'écraser davantage : un texte de pièce d'identité
            illisible ne sert plus à rien.
        keep_alpha: Conserver la transparence (PNG) quand l'image en a.
            Faux pour les photos : un fond transparent n'y a pas de sens
            et le PNG pèse dix fois le JPEG.
    """

    max_edge: int
    quality: int = 85
    min_quality: int = 60
    keep_alpha: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    """Ce qui ressort de la normalisation : des octets prêts à écrire."""

    payload: bytes
    content_type: str
    width: int
    height: int
    #: Vrai si les octets sont ceux reçus, intacts : l'image était déjà
    #: conforme et n'a pas été réencodée — donc pas dégradée.
    untouched: bool = False


class ImageError(Exception):
    """Base des refus du socle image. Le message est destiné à la personne."""


class NotAnImageError(ImageError):
    """Les octets ne forment aucune image lisible."""

    def __init__(self) -> None:
        super().__init__("Ce fichier n'est pas une image lisible.")


class ImageTooLargeError(ImageError):
    """L'image est trop grande pour être décodée sans risque."""

    def __init__(self, pixels: int) -> None:
        super().__init__(
            f"Cette image est trop grande ({pixels / 1_000_000:.0f} mégapixels) : "
            "réduisez-la avant de l'envoyer."
        )


class ImageCannotFitError(ImageError):
    """Même réduite au minimum, l'image ne tient pas dans le plafond."""

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(
            "Impossible de réduire cette image sous "
            f"{limit_bytes / (1024 * 1024):.1f} Mo sans la rendre illisible."
        )
