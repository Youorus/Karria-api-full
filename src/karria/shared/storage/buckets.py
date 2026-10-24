"""Registre des buckets.

Un bucket est déclaré ici avec sa politique ; son nom physique en est
dérivé. Le code métier ne manipule **jamais** de chaîne : il passe
`Bucket.MEDIA`. Une faute de frappe devient ainsi une erreur de typage
attrapée par mypy, et non un 404 découvert en production.

Ajouter un usage se lit donc : une valeur dans `Bucket`, une entrée dans
`REGISTRY`, `make storage-provision`. Rien d'autre — pas d'appel à créer,
pas de configuration à propager, pas de secret à ajouter.

Ce que la V1 faisait à la place : un seul bucket pour tous les usages et
tous les environnements, sans limite de taille ni de type. Un fichier de
recette pouvait écraser un fichier de production ; un exécutable pouvait
se ranger parmi les CV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from karria.shared.images import ImagePolicy
from karria.shared.storage.errors import PayloadRejectedError


class Bucket(StrEnum):
    """Buckets logiques de l'application."""

    #: Documents de candidature : CV importés, CV et lettres générés en
    #: PDF. Privé, versionné — un CV écrasé par erreur doit pouvoir se
    #: retrouver — et conservation longue.
    DOCUMENTS = "documents"
    #: Photos de profil, photos améliorées, visuels de modèles. Privé,
    #: servi par URL signée.
    MEDIA = "media"
    #: Travaux intermédiaires : un fichier audio le temps d'une
    #: transcription, un rendu avant validation. Purgé automatiquement.
    TEMP = "temp"
    #: Données brutes de collecte d'offres (un fichier JSONL compressé par
    #: passage), gardées le temps de pouvoir rejouer une normalisation.
    LANDING = "landing"
    #: Modèles de CV : PDF source, spécification, aperçus, icônes, et la
    #: bibliothèque de polices libres (`fonts/…`), téléchargée une fois.
    #: Privé ; la galerie sert ses aperçus par URL signée.
    TEMPLATES = "templates"


@dataclass(frozen=True, slots=True)
class BucketPolicy:
    """Politique d'un bucket, appliquée au provisionnement et à l'écriture.

    Attributes:
        public_read: Lecture anonyme. `False` par défaut, et cela doit
            rester l'exception : un bucket public est une fuite de données
            à un mauvais nom de fichier près. On sert par URL signée.
        versioning: Conserve les versions antérieures. À activer sur ce
            dont la perte est irréversible.
        expire_after_days: Suppression automatique. `None` = conservation
            indéfinie.
        allowed_content_types: Types MIME acceptés à l'écriture, vérifiés
            côté application **avant** tout appel réseau.
        max_bytes: Taille maximale d'un objet. `None` = plafond global de
            la configuration.
        cache_seconds: Durée de cache suggérée aux clients pour les objets
            de ce bucket.
        image: Ce qu'on fait d'une image reçue — jusqu'où la réduire, à
            quelle qualité — appliqué par `prepare_upload` avant l'écriture
            (ADR-0009). `None` : les images ne sont pas retouchées.
    """

    public_read: bool = False
    versioning: bool = False
    expire_after_days: int | None = None
    allowed_content_types: frozenset[str] = field(default_factory=frozenset)
    max_bytes: int | None = None
    cache_seconds: int | None = None
    image: ImagePolicy | None = None


REGISTRY: Final[dict[Bucket, BucketPolicy]] = {
    Bucket.DOCUMENTS: BucketPolicy(
        versioning=True,
        allowed_content_types=frozenset(
            {
                "application/pdf",
                "image/jpeg",
                "image/png",
                # Les CV exportés en Word (ADR-0020).
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        ),
        max_bytes=10 * 1024 * 1024,
        # Un CV photographié se lit : réduction douce, qualité haute,
        # plancher élevé — une ligne d'expérience floue ne s'extrait pas.
        image=ImagePolicy(max_edge=3_000, quality=92, min_quality=80),
    ),
    Bucket.MEDIA: BucketPolicy(
        allowed_content_types=frozenset({"image/jpeg", "image/png", "image/webp"}),
        max_bytes=5 * 1024 * 1024,
        # Une photo de profil ne change pas souvent, et sa clé change à
        # chaque remplacement : elle peut être mise en cache longuement.
        cache_seconds=86_400,
        # Avatars et photos de CV : ce qu'un écran de téléphone affiche,
        # rien de plus. Le fichier est lu bien plus souvent qu'écrit —
        # c'est à la lecture qu'on économise.
        image=ImagePolicy(max_edge=2_048, quality=85),
    ),
    Bucket.TEMP: BucketPolicy(expire_after_days=7),
    Bucket.TEMPLATES: BucketPolicy(
        allowed_content_types=frozenset(
            {"application/pdf", "application/json", "image/png", "image/webp", "font/ttf"}
        ),
        max_bytes=20 * 1024 * 1024,
        # Une version de modèle est immuable : ses aperçus se gardent en cache.
        cache_seconds=86_400,
    ),
    Bucket.LANDING: BucketPolicy(
        allowed_content_types=frozenset({"application/gzip", "application/x-ndjson"}),
        max_bytes=200 * 1024 * 1024,
        expire_after_days=90,
    ),
}


def check_payload(bucket: Bucket, *, size: int, content_type: str, size_ceiling: int) -> None:
    """Confronte un contenu à la politique du bucket, **avant** tout réseau.

    Partagé par tous les adaptateurs : la règle doit être identique en
    mémoire et sur S3, faute de quoi les tests décriraient un système plus
    permissif que celui qui part en production.

    Refuser après avoir transmis les octets serait doublement coûteux :
    la bande passante est déjà consommée, et l'appelant a attendu pour rien.

    Args:
        size_ceiling: Plafond global de la configuration, appliqué lorsque
            le bucket n'en impose pas de plus strict.

    Raises:
        PayloadRejectedError: Taille dépassée ou type MIME hors politique.
    """
    policy = REGISTRY[bucket]
    limit = min(policy.max_bytes, size_ceiling) if policy.max_bytes else size_ceiling
    if size > limit:
        megabytes = limit / (1024 * 1024)
        msg = f"Le fichier dépasse la taille maximale autorisée ({megabytes:.1f} Mo)."
        raise PayloadRejectedError(msg)
    if policy.allowed_content_types and content_type not in policy.allowed_content_types:
        acceptes = ", ".join(sorted(policy.allowed_content_types))
        msg = f"Ce type de fichier n'est pas accepté. Types autorisés : {acceptes}."
        raise PayloadRejectedError(msg, field="content_type")


def physical_name(bucket: Bucket, *, prefix: str, environment: str) -> str:
    """Nom réel du bucket chez le fournisseur.

    Le nom porte l'environnement : c'est ce qui empêche une instance de
    recette d'écraser un fichier de production. La séparation par
    identifiants ne suffit pas — une clé d'API mal copiée est vite arrivée,
    un nom de bucket différent, jamais.

    Exemple : `karria-production-media`.
    """
    return f"{prefix}-{environment}-{bucket.value}"
