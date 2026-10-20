"""Ce que toute route fait d'un fichier reçu, avant de bâtir sa clé.

Le point d'entrée unique de la normalisation des images (ADR-0009).
Chaque route qui reçoit un fichier l'appelle **avant** de construire la
clé et d'écrire : c'est ainsi que l'extension de la clé et le type
enregistré chez le fournisseur disent la vérité sur les octets, et non
ce que le client a annoncé.

Ce qui n'est pas une image — un PDF de billet, une note vocale — passe
intact, avec le type annoncé : la politique du bucket le jugera ensuite,
comme avant. Ce qui **prétend** être une image et n'en est pas une est
refusé ici, avec un message qui le dit — plutôt que rangé illisible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from karria.shared.images import (
    ImageError,
    normalize_image,
    sniff_content_type,
)
from karria.shared.storage.buckets import REGISTRY, Bucket
from karria.shared.storage.errors import PayloadRejectedError
from karria.shared.storage.keys import extension_for

#: Plafond appliqué quand le bucket n'en fixe aucun. Aligné sur la valeur
#: par défaut de `STORAGE__MAX_UPLOAD_BYTES` : la normalisation ne doit
#: pas produire plus que ce que l'adaptateur acceptera d'écrire.
_DEFAULT_MAX_BYTES: Final = 25 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparedUpload:
    """Des octets prêts à écrire, et le type qui leur correspond vraiment."""

    payload: bytes
    content_type: str

    @property
    def extension(self) -> str:
        """L'extension à donner à la clé."""
        return extension_for(self.content_type)

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")


def prepare_upload(bucket: Bucket, payload: bytes, declared_content_type: str) -> PreparedUpload:
    """Normalise un fichier reçu selon la politique du bucket.

    Args:
        declared_content_type: Ce que le client annonce. Ne sert qu'aux
            fichiers qui ne sont pas des images ; pour une image, le type
            réel est lu dans les octets.

    Raises:
        PayloadRejectedError: Le fichier se présente comme une image et
            n'en est pas une, est trop grand pour être décodé, ou ne peut
            pas être réduit sous le plafond sans devenir illisible.
    """
    policy = REGISTRY[bucket]
    sniffed = sniff_content_type(payload)
    declared = (declared_content_type or "").split(";")[0].strip().lower()

    if sniffed is None:
        if declared.startswith("image/"):
            # Annoncé image, pas une image : refuser ici donne une
            # consigne, là où le bucket ne dirait que « type inconnu ».
            raise PayloadRejectedError("Ce fichier n'est pas une image lisible.")
        return PreparedUpload(payload=payload, content_type=declared)

    if policy.image is None:
        # Un bucket sans politique d'image ne normalise pas : on corrige
        # seulement le type annoncé, pour que la clé et l'objet soient
        # cohérents avec les octets.
        return PreparedUpload(payload=payload, content_type=sniffed)

    limit = policy.max_bytes if policy.max_bytes is not None else _DEFAULT_MAX_BYTES
    try:
        image = normalize_image(payload, policy.image, max_bytes=limit)
    except ImageError as exc:
        raise PayloadRejectedError(str(exc)) from exc
    return PreparedUpload(payload=image.payload, content_type=image.content_type)
