"""Port du stockage objet.

Déclaré par la couche qui a **besoin** du service, pas par celle qui le
rend. Un `Protocol` plutôt qu'une classe abstraite : l'adaptateur n'hérite
de rien, donc n'importe rien — le découplage joue dans les deux sens, et
l'adaptateur en mémoire des tests tient en quelques dizaines de lignes.

La règle numéro un du stockage objet
------------------------------------
**En base de données on stocke `StoredObject.key`, jamais une URL.** Une
URL présignée expire ; une URL publique encode le fournisseur. La V1 a
stocké des URLs, et il a fallu écrire une fonction de rattrapage
(`extract_object_key`, une cinquantaine de lignes de parsing) pour
retrouver les clés a posteriori — puis la maintenir.

Une clé reste valide à l'identique chez un autre fournisseur. C'est
précisément ce qui rend la bascule possible sans migration de base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from karria.shared.storage.buckets import Bucket


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Référence d'un objet écrit. C'est **ceci** que l'on persiste."""

    bucket: Bucket
    key: str
    size: int
    content_type: str
    etag: str


@dataclass(frozen=True, slots=True)
class ObjectSummary:
    """Entrée de listage."""

    key: str
    size: int
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """Autorisation d'écriture directe, remise au client.

    Le client envoie ses octets à `url` en `PUT`, avec exactement le
    `content_type` annoncé — la signature le couvre — puis confirme à l'API
    en lui rendant `key`.
    """

    url: str
    key: str
    content_type: str
    expires_at: datetime
    max_bytes: int


class ObjectStorage(Protocol):
    """Stockage d'objets binaires adressés par clé."""

    async def put(
        self,
        bucket: Bucket,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        cache_seconds: int | None = None,
    ) -> StoredObject:
        """Écrit un objet et retourne sa référence.

        Raises:
            PayloadRejectedError: Taille ou type MIME hors de la politique du
                bucket — vérifié **avant** tout appel réseau.
            StorageError: Le stockage a refusé ou n'a pas répondu.
        """
        ...

    async def get(self, bucket: Bucket, key: str) -> bytes:
        """Lit un objet.

        Raises:
            ObjectNotFoundError: La clé n'existe pas.
            StorageError: Toute autre défaillance.
        """
        ...

    async def delete(self, bucket: Bucket, key: str) -> None:
        """Supprime un objet.

        **Idempotent** : supprimer une clé absente n'est pas une erreur. En
        revanche une panne réseau lève — la V1 renvoyait `False` dans ce
        cas, ce qui rend une panne indistinguable d'une suppression réussie.
        """
        ...

    async def exists(self, bucket: Bucket, key: str) -> bool:
        """Indique si la clé existe."""
        ...

    async def list(self, bucket: Bucket, prefix: str) -> list[ObjectSummary]:
        """Liste les objets sous un préfixe."""
        ...

    async def presigned_get_url(
        self,
        bucket: Bucket,
        key: str,
        *,
        ttl_seconds: int | None = None,
        download_as: str | None = None,
    ) -> str:
        """Produit une URL de lecture temporaire.

        Args:
            download_as: Force le téléchargement sous ce nom de fichier
                plutôt qu'un affichage dans le navigateur. C'est le bon
                endroit pour restituer le nom d'origine d'un document —
                sans l'avoir mis dans la clé.
        """
        ...

    def public_url(self, bucket: Bucket, key: str) -> str:
        """L'adresse **stable** d'un objet d'un bucket en lecture publique.

        Calcul local, sans réseau ni signature : la même clé donne toujours la
        même URL, que le téléphone peut garder en cache aussi longtemps que
        l'objet vit. Réservé aux buckets `public_read` (les logos).

        Raises:
            StorageError: Le bucket n'est pas en lecture publique.
        """
        ...

    async def presigned_put_url(
        self,
        bucket: Bucket,
        key: str,
        *,
        content_type: str,
        ttl_seconds: int | None = None,
    ) -> PresignedUpload:
        """Produit une autorisation d'écriture directe depuis le client.

        C'est ce qui évite de faire transiter un fichier de plusieurs
        mégaoctets par l'API. La V1 ne l'a jamais eu : chaque envoi y
        occupe un worker applicatif pendant toute la durée du transfert.
        """
        ...

    async def check_health(self) -> None:
        """Vérifie que les buckets déclarés sont joignables.

        Raises:
            StorageError: Un bucket est absent ou le service ne répond pas.
        """
        ...
