"""Socle image : reconnaître, redresser, alléger, convertir (ADR-0009).

Sans métier. Ce qu'un usage tolère — dimensions, qualité, transparence —
est une `ImagePolicy` portée par le bucket de destination, dans
`shared/storage/buckets.py`. Le point d'entrée des routes est
`shared.storage.prepare_upload`, qui applique cette politique à tout
fichier reçu et laisse passer intact ce qui n'est pas une image.
"""

from karria.shared.images.pillow import (
    blurhash,
    crop_to_frame,
    image_size,
    normalize_image,
    resize_to_png,
    sniff_content_type,
    to_webp,
)
from karria.shared.images.ports import (
    MAX_PIXELS,
    OUTPUT_CONTENT_TYPES,
    ImageCannotFitError,
    ImageError,
    ImagePolicy,
    ImageTooLargeError,
    NormalizedImage,
    NotAnImageError,
)

__all__ = [
    "MAX_PIXELS",
    "OUTPUT_CONTENT_TYPES",
    "ImageCannotFitError",
    "ImageError",
    "ImagePolicy",
    "ImageTooLargeError",
    "NormalizedImage",
    "NotAnImageError",
    "blurhash",
    "crop_to_frame",
    "image_size",
    "normalize_image",
    "resize_to_png",
    "sniff_content_type",
    "to_webp",
]
