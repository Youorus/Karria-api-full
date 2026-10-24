"""Construction et validation des clés objet.

Trois interdits, chacun payé par une leçon de production sur la V1 `karria_api` :

1. **Aucune donnée personnelle.** La V1 compose `users/{id}_{email}/...`.
   L'email se retrouve alors dans les journaux d'accès du fournisseur, dans
   les sauvegardes, dans toute URL partagée — et devient impossible à
   rectifier le jour où la personne en change. Un identifiant opaque, lui,
   ne dit rien de qui que ce soit.
2. **Aucun nom de fichier client tel quel.** Il porte des séparateurs, des
   séquences `..`, de l'unicode homoglyphe. On le range en métadonnée si
   l'on veut le restituer — voir `download_as` du port — mais jamais dans
   le chemin.
3. **Aucune horodatation dans une clé qu'on devra retrouver.** La date sert
   à ranger, pas à identifier. L'identifiant est l'identifiant.

La clé produite ressemble à :

    users/3f1a9c02-6f4b-4a7e-9b18-2d5c8e0a4711/avatar/2026-08-09/f3c2a71b….jpg
    └── segments ─────────────────────────────────────┘ └─ jour ─┘ └ opaque ┘
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Final

#: Longueur maximale d'une clé S3 (1 024 octets). On reste très en deçà :
#: la colonne qui la stocke est dimensionnée en conséquence.
KEY_MAX_LENGTH: Final = 512

# Un segment de clé se réduit à l'alphabet minimal qui traverse sans dommage
# une URL, un nom d'hôte, un journal et un système de fichiers.
_SAFE_SEGMENT: Final = re.compile(r"[^a-z0-9-]+")

# Forme acceptée pour une clé reçue de l'extérieur. Notamment : aucun `..`,
# aucun `/` initial ou doublé, aucun caractère d'échappement.
_VALID_KEY: Final = re.compile(r"^[a-z0-9][a-z0-9._/-]*[a-z0-9]$")


class InvalidObjectKeyError(ValueError):
    """La clé proposée n'est pas exploitable.

    Volontairement une `ValueError` et non une erreur de domaine : c'est un
    défaut de programmation ou une tentative de traversée de chemin, pas un
    cas métier. L'appelant qui reçoit une clé d'un client la valide avec
    `ensure_key_under` et traduit lui-même le refus.
    """


def _segment(raw: str | int) -> str:
    """Réduit une valeur à un segment de clé sûr."""
    value = _SAFE_SEGMENT.sub("-", str(raw).strip().lower()).strip("-")
    if not value:
        msg = "Segment de clé vide après nettoyage."
        raise InvalidObjectKeyError(msg)
    return value


def build_key(*segments: str | int, extension: str, at: datetime | None = None) -> str:
    """Compose une clé rangée par date et terminée par un identifiant opaque.

    Args:
        *segments: Fragments de rangement, du plus général au plus précis.
            **Ne jamais y passer d'e-mail, de nom ni de numéro.**
        extension: Extension du fichier, avec ou sans point.
        at: Instant de référence, injecté pour rendre la clé reproductible
            en test.

    Returns:
        Une clé unique : deux appels ne peuvent pas produire la même, ce qui
        rend l'écriture naturellement non destructive.
    """
    day = (at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d")
    ext = _segment(extension.lstrip("."))
    key = "/".join([*(_segment(s) for s in segments), day, f"{uuid.uuid4().hex}.{ext}"])
    if len(key) > KEY_MAX_LENGTH:
        msg = f"Clé trop longue ({len(key)} > {KEY_MAX_LENGTH})."
        raise InvalidObjectKeyError(msg)
    return key


def is_valid_key(key: str) -> bool:
    """Indique si une chaîne peut être une clé objet de cette application."""
    return (
        bool(key)
        and len(key) <= KEY_MAX_LENGTH
        and ".." not in key
        and "//" not in key
        and bool(_VALID_KEY.match(key))
    )


def ensure_key_under(key: str, prefix: str) -> str:
    """Valide une clé reçue de l'extérieur et vérifie qu'elle est sous `prefix`.

    C'est le contrôle qui rend l'envoi direct par URL présignée sûr. Sans
    lui, un client authentifié pourrait confirmer une clé appartenant à
    quelqu'un d'autre et s'attribuer son fichier — le serveur n'ayant jamais
    vu passer les octets, il n'a que la clé pour se défendre.

    Raises:
        InvalidObjectKeyError: La clé est mal formée ou hors du préfixe attendu.
    """
    if not is_valid_key(key):
        msg = "Clé objet invalide."
        raise InvalidObjectKeyError(msg)
    normalized = prefix if prefix.endswith("/") else f"{prefix}/"
    if not key.startswith(normalized):
        msg = "Clé objet hors du périmètre autorisé."
        raise InvalidObjectKeyError(msg)
    return key


def extension_for(content_type: str) -> str:
    """Retourne l'extension canonique d'un type MIME accepté.

    Table explicite plutôt que `mimetypes.guess_extension` : la table de la
    bibliothèque standard varie selon le système hôte, et rend `image/jpeg`
    tantôt `.jpg`, tantôt `.jpe`. Une clé dont l'extension dépend de la
    machine qui l'a écrite est une clé qu'on ne saura pas reconstituer.
    """
    return _EXTENSIONS.get(content_type, "bin")


_EXTENSIONS: Final[dict[str, str]] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/json": "json",
    "font/ttf": "ttf",
}
