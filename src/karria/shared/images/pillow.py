"""L'adaptateur Pillow : le seul fichier du projet qui importe `PIL`.

Ce qu'il fait d'une image reçue
-------------------------------
1. **Reconnaît** le format aux octets, jamais au type annoncé : un HEIC
   étiqueté `image/jpeg` par un client trop confiant est un HEIC.
2. **Refuse** ce qui n'est pas une image, et ce qui ferait exploser la
   mémoire une fois décodé (voir `MAX_PIXELS`).
3. **Redresse** selon l'orientation EXIF : une photo prise en portrait
   s'affiche en portrait, sans que chaque client ait à lire l'EXIF.
4. **Retire** les métadonnées. C'est une décision de confidentialité
   avant d'être une économie d'octets : une pièce d'identité photographiée
   avec le téléphone porte les coordonnées GPS du domicile, et un selfie
   le modèle de l'appareil. Rien de cela n'a à vivre dans un bucket.
5. **Réduit** à la plus grande dimension de la politique, puis
   **recompresse** par paliers jusqu'à tenir dans le plafond d'octets —
   en qualité d'abord, en dimensions ensuite, jamais l'inverse.
6. **Convertit** vers JPEG, sauf transparence à conserver (PNG).

Ce qu'il ne fait pas
--------------------
Réencoder une image déjà conforme. Un JPEG sans EXIF, dans les
dimensions et sous le plafond, ressort **intact** : chaque réencodage
dégrade, et les photos de l'application arrivent déjà compressées.

Pourquoi Pillow, et pillow-heif
-------------------------------
Pillow est la bibliothèque d'images de référence en Python ; ses roues
embarquent libjpeg, libpng et libwebp, donc rien à installer dans
l'image Docker. Le HEIC des iPhone récents n'en fait pas partie :
pillow-heif l'apporte, avec libheif embarquée de la même façon. libvips
aurait été plus rapide, mais exige une bibliothèque système et un second
adaptateur à maintenir — pour quelques dizaines d'images par jour, le
gain ne vaut pas la dépendance (ADR-0009).
"""

from __future__ import annotations

from io import BytesIO
from typing import Final

from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError
from pillow_heif.as_plugin import register_heif_opener

from karria.shared.images.ports import (
    MAX_PIXELS,
    ImageCannotFitError,
    ImagePolicy,
    ImageTooLargeError,
    NormalizedImage,
    NotAnImageError,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)

# Enregistré une fois, à l'import : Pillow sait alors ouvrir `.heic` et
# `.heif` comme n'importe quel autre format.
register_heif_opener()

# Un fichier tronqué — un envoi coupé par le réseau — se décode jusqu'où
# il va plutôt que de tout refuser : mieux vaut une photo avec une bande
# grise en bas qu'un envoi à refaire sur un réseau qui a déjà coupé.
ImageFile.LOAD_TRUNCATED_IMAGES = True

_CONTENT_TYPE_BY_FORMAT: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "HEIF": "image/heic",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}

#: Palier de descente en qualité, puis facteur de réduction des
#: dimensions quand la qualité plancher ne suffit pas.
_QUALITY_STEP: Final = 10
_SHRINK_FACTOR: Final = 0.8
#: En dessous, on ne réduit plus : une image de 480 px de large qui ne
#: tient toujours pas dans le plafond n'est pas une photo, c'est un
#: problème.
_MIN_EDGE: Final = 480


def sniff_content_type(payload: bytes) -> str | None:
    """Le type MIME réel des octets, ou `None` s'ils ne sont pas une image.

    Ne décode rien : Pillow ne lit que l'en-tête. C'est ce qui permet de
    l'appeler sur tout fichier reçu, PDF et audio compris, pour un coût
    négligeable.
    """
    try:
        with Image.open(BytesIO(payload)) as image:
            return _CONTENT_TYPE_BY_FORMAT.get(image.format or "", "application/octet-stream")
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def normalize_image(payload: bytes, policy: ImagePolicy, *, max_bytes: int) -> NormalizedImage:
    """Rend l'image conforme à la politique et au plafond d'octets.

    Raises:
        NotAnImageError: Les octets ne sont pas une image.
        ImageTooLargeError: Trop de pixels pour être décodée sans risque.
        ImageCannotFitError: Même réduite au minimum, elle ne tient pas.
    """
    try:
        source = Image.open(BytesIO(payload))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise NotAnImageError from exc

    pixels = source.width * source.height
    if pixels > MAX_PIXELS:
        raise ImageTooLargeError(pixels)

    original_format = source.format or ""
    has_exif = bool(source.getexif())
    if _already_conformant(source, policy, size=len(payload), max_bytes=max_bytes, exif=has_exif):
        return NormalizedImage(
            payload=payload,
            content_type=_CONTENT_TYPE_BY_FORMAT[original_format],
            width=source.width,
            height=source.height,
            untouched=True,
        )

    try:
        source.load()
        transposed = ImageOps.exif_transpose(source)
    except (OSError, ValueError) as exc:
        raise NotAnImageError from exc

    keep_alpha = policy.keep_alpha and _has_alpha(transposed)
    if keep_alpha:
        image = transposed.convert("RGBA")
    elif _has_alpha(transposed):
        image = _flatten_on_white(transposed.convert("RGBA"))
    else:
        image = transposed.convert("RGB")

    image = _fit(image, policy.max_edge)
    encoded, content_type, final = _encode_within(
        image, policy, max_bytes=max_bytes, alpha=keep_alpha
    )
    logger.info(
        "images.normalized",
        source_format=original_format,
        source_bytes=len(payload),
        result_bytes=len(encoded),
        width=final.width,
        height=final.height,
        content_type=content_type,
    )
    return NormalizedImage(
        payload=encoded, content_type=content_type, width=final.width, height=final.height
    )


def _already_conformant(
    image: Image.Image, policy: ImagePolicy, *, size: int, max_bytes: int, exif: bool
) -> bool:
    """L'image peut-elle partir telle quelle ?

    Oui si son format est un format de sortie, qu'elle ne porte aucune
    métadonnée EXIF, qu'elle tient dans les dimensions et sous le
    plafond. Un PNG n'est gardé tel quel que si la politique conserve la
    transparence — sinon il repart en JPEG, dix fois plus léger.
    """
    content_type = _CONTENT_TYPE_BY_FORMAT.get(image.format or "")
    if content_type not in {"image/jpeg", "image/png"} or exif:
        # Tout le reste — HEIC, WebP, GIF, BMP, TIFF — est converti : un
        # seul format de sortie photo, que tout affiche sans conversion.
        return False
    if content_type == "image/png" and not policy.keep_alpha:
        return False
    if max(image.width, image.height) > policy.max_edge or size > max_bytes:
        return False
    return content_type != "image/png" or _has_alpha(image)


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)


def _flatten_on_white(image: Image.Image) -> Image.Image:
    """Pose une image transparente sur du blanc : le JPEG n'a pas de canal alpha."""
    background = Image.new("RGB", image.size, (255, 255, 255))
    background.paste(image, mask=image.getchannel("A"))
    return background


def _fit(image: Image.Image, max_edge: int) -> Image.Image:
    """Réduit à la plus grande dimension demandée, proportions conservées."""
    if max(image.width, image.height) <= max_edge:
        return image
    copy = image.copy()
    copy.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return copy


def _encode_within(
    image: Image.Image, policy: ImagePolicy, *, max_bytes: int, alpha: bool
) -> tuple[bytes, str, Image.Image]:
    """Encode en descendant la qualité, puis les dimensions, jusqu'à tenir.

    Rend aussi l'image **finale** : ses dimensions sont celles à annoncer,
    pas celles d'avant la descente.

    Sans aucune métadonnée : ni EXIF, ni profil ICC, ni commentaire. Le
    profil de couleur part aussi — les téléphones l'écrivent en sRGB,
    ce que tout afficheur suppose déjà.
    """
    if alpha:
        encoded = _encode_png(image)
        if len(encoded) <= max_bytes:
            return encoded, "image/png", image
        # Trop lourd même en PNG : on renonce à la transparence plutôt
        # qu'à l'image. C'est dit dans les journaux.
        logger.info("images.alpha_dropped", png_bytes=len(encoded), max_bytes=max_bytes)
        image = _flatten_on_white(image)

    current = image
    quality = policy.quality
    while True:
        encoded = _encode_jpeg(current, quality)
        if len(encoded) <= max_bytes:
            return encoded, "image/jpeg", current
        if quality - _QUALITY_STEP >= policy.min_quality:
            quality -= _QUALITY_STEP
            continue
        edge = max(current.width, current.height)
        if edge <= _MIN_EDGE:
            raise ImageCannotFitError(max_bytes)
        current = _fit(current, max(_MIN_EDGE, int(edge * _SHRINK_FACTOR)))
        quality = policy.quality


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def _encode_png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
