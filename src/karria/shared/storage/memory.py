"""Adaptateur en mémoire du port `ObjectStorage`.

Un dictionnaire, **la même politique de validation**, aucune I/O. C'est ce
qui permet d'exercer les cas d'usage qui écrivent des fichiers en quelques
millisecondes, sans MinIO, sans conteneur et sans réseau.

Le point qui compte : la validation passe par `check_payload`, exactement
comme l'adaptateur S3. Une doublure plus permissive que le vrai système
laisserait passer en test ce qui échouerait en production — et c'est
précisément le genre de divergence qu'on ne découvre qu'au déploiement.

Les URL qu'il produit sont **fictives** et le disent (`https://storage.local/…`).
Elles ne sont pas téléchargeables : ce que les tests vérifient à ce niveau,
c'est que l'application signe la bonne clé dans le bon bucket, pas que le
protocole S3 fonctionne — ce dernier point relève des tests d'intégration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import md5
from urllib.parse import quote

from karria.shared.config import StorageSettings
from karria.shared.storage.buckets import REGISTRY, Bucket, check_payload, physical_name
from karria.shared.storage.errors import ObjectNotFoundError
from karria.shared.storage.ports import ObjectSummary, PresignedUpload, StoredObject


@dataclass(frozen=True, slots=True)
class _Entry:
    payload: bytes
    content_type: str
    etag: str
    last_modified: datetime


class InMemoryObjectStorage:
    """Implémente `ObjectStorage` dans un dictionnaire."""

    def __init__(self, settings: StorageSettings, environment: str = "test") -> None:
        self._settings = settings
        self._environment = environment
        self.objects: dict[tuple[Bucket, str], _Entry] = {}

    def bucket_name(self, bucket: Bucket) -> str:
        """Nom physique du bucket, dérivé comme chez un vrai fournisseur."""
        return physical_name(
            bucket, prefix=self._settings.bucket_prefix, environment=self._environment
        )

    async def put(
        self,
        bucket: Bucket,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        cache_seconds: int | None = None,
    ) -> StoredObject:
        """Écrit un objet après avoir appliqué la politique du bucket."""
        _ = cache_seconds
        check_payload(
            bucket,
            size=len(payload),
            content_type=content_type,
            size_ceiling=self._settings.max_upload_bytes,
        )
        # `usedforsecurity=False` : l'ETag de S3 est une empreinte de
        # contenu, pas une garantie cryptographique. Sans ce drapeau, le
        # calcul échoue sur un système en mode FIPS.
        etag = md5(payload, usedforsecurity=False).hexdigest()
        self.objects[(bucket, key)] = _Entry(
            payload=payload,
            content_type=content_type,
            etag=etag,
            last_modified=datetime.now(UTC),
        )
        return StoredObject(
            bucket=bucket, key=key, size=len(payload), content_type=content_type, etag=etag
        )

    async def get(self, bucket: Bucket, key: str) -> bytes:
        """Lit un objet."""
        entry = self.objects.get((bucket, key))
        if entry is None:
            raise ObjectNotFoundError(
                "L'objet demandé n'existe pas.",
                operation="get_object",
                bucket=self.bucket_name(bucket),
                key=key,
            )
        return entry.payload

    async def delete(self, bucket: Bucket, key: str) -> None:
        """Supprime un objet. Idempotent, comme S3."""
        self.objects.pop((bucket, key), None)

    async def exists(self, bucket: Bucket, key: str) -> bool:
        """Indique si la clé existe."""
        return (bucket, key) in self.objects

    async def list(self, bucket: Bucket, prefix: str) -> list[ObjectSummary]:
        """Liste les objets sous un préfixe, triés par clé comme le fait S3."""
        return sorted(
            (
                ObjectSummary(key=key, size=len(entry.payload), last_modified=entry.last_modified)
                for (owner, key), entry in self.objects.items()
                if owner is bucket and key.startswith(prefix)
            ),
            key=lambda summary: summary.key,
        )

    async def presigned_get_url(
        self,
        bucket: Bucket,
        key: str,
        *,
        ttl_seconds: int | None = None,
        download_as: str | None = None,
    ) -> str:
        """Produit une URL fictive, reconnaissable comme telle."""
        ttl = self._ttl(ttl_seconds)
        url = f"https://storage.local/{self.bucket_name(bucket)}/{quote(key)}?expires={ttl}"
        if download_as is not None:
            url = f"{url}&filename={quote(download_as)}"
        return url

    async def presigned_put_url(
        self,
        bucket: Bucket,
        key: str,
        *,
        content_type: str,
        ttl_seconds: int | None = None,
    ) -> PresignedUpload:
        """Produit une autorisation d'écriture fictive."""
        check_payload(
            bucket, size=0, content_type=content_type, size_ceiling=self._settings.max_upload_bytes
        )
        ttl = self._ttl(ttl_seconds)
        policy = REGISTRY[bucket]
        return PresignedUpload(
            url=f"https://storage.local/{self.bucket_name(bucket)}/{quote(key)}?upload={ttl}",
            key=key,
            content_type=content_type,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            max_bytes=min(
                policy.max_bytes or self._settings.max_upload_bytes,
                self._settings.max_upload_bytes,
            ),
        )

    async def check_health(self) -> None:
        """Toujours sain : il n'y a rien qui puisse tomber."""
        return

    def _ttl(self, requested: int | None) -> int:
        ttl = requested if requested is not None else self._settings.presign_ttl_seconds
        return min(ttl, self._settings.profile.max_presign_seconds)
