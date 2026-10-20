"""Adaptateur S3 — unique implémentation réseau du port `ObjectStorage`.

Un seul adaptateur couvre **tous** les fournisseurs du catalogue : le
protocole S3 est le même partout, seules changent l'adresse et quelques
bornes, décrites en données dans `providers.py`. C'est cette uniformité qui
rend la bascule de fournisseur gratuite côté code.

Ce fichier est le seul du projet à connaître `aioboto3`. Le vérifier est
mécanique :

    grep -rl "boto\\|s3\\." src/ → shared/storage/ uniquement

"""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from karria.shared.config import StorageSettings
from karria.shared.logging import get_logger
from karria.shared.storage.buckets import REGISTRY, Bucket, check_payload, physical_name
from karria.shared.storage.errors import ObjectNotFoundError, StorageError
from karria.shared.storage.ports import ObjectSummary, PresignedUpload, StoredObject

logger = get_logger(__name__)

#: Codes par lesquels S3 signale une clé ou un bucket absent. Ils diffèrent
#: selon l'opération : `GetObject` rend `NoSuchKey`, `HeadObject` rend un
#: `404` nu. Les traiter ensemble évite de confondre une absence avec une
#: panne — la confusion exacte que produit un `except ClientError` en bloc.
_MISSING_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})


class S3ObjectStorage:
    """Implémente `ObjectStorage` au-dessus d'un service compatible S3."""

    def __init__(self, client: Any, settings: StorageSettings, environment: str) -> None:
        self._client = client
        self._settings = settings
        self._environment = environment

    # -----------------------------------------------------------------
    # Cycle de vie
    # -----------------------------------------------------------------

    @classmethod
    async def open(cls, settings: StorageSettings, environment: str, stack: AsyncExitStack) -> Self:
        """Ouvre un client durable, refermé par la pile fournie.

        Le client `aioboto3` est un gestionnaire de contexte asynchrone : il
        **doit** rester ouvert pendant toute la vie de l'application, d'où
        l'`AsyncExitStack` détenue par le `lifespan`. En ouvrir un par appel
        rétablirait une poignée de main TLS à chaque envoi de fichier.
        """
        session = aioboto3.Session()
        client = await stack.enter_async_context(
            session.client(
                "s3",
                endpoint_url=settings.resolved_endpoint,
                region_name=settings.resolved_region,
                aws_access_key_id=settings.access_key.get_secret_value(),
                aws_secret_access_key=settings.secret_key.get_secret_value(),
                config=Config(
                    # v4 est exigée par Scaleway, R2 et MinIO ; AWS l'accepte.
                    signature_version="s3v4",
                    s3={"addressing_style": settings.profile.addressing},
                    retries={"max_attempts": settings.max_attempts, "mode": "standard"},
                    connect_timeout=settings.request_timeout_seconds,
                    read_timeout=settings.request_timeout_seconds,
                ),
            )
        )
        logger.info(
            "storage.connected",
            provider=settings.provider.value,
            region=settings.resolved_region,
            addressing=settings.profile.addressing,
        )
        return cls(client, settings, environment)

    def bucket_name(self, bucket: Bucket) -> str:
        """Nom physique du bucket chez le fournisseur."""
        return physical_name(
            bucket, prefix=self._settings.bucket_prefix, environment=self._environment
        )

    # -----------------------------------------------------------------
    # Écriture
    # -----------------------------------------------------------------

    async def put(
        self,
        bucket: Bucket,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        cache_seconds: int | None = None,
    ) -> StoredObject:
        """Écrit un objet et retourne sa référence."""
        check_payload(
            bucket,
            size=len(payload),
            content_type=content_type,
            size_ceiling=self._settings.max_upload_bytes,
        )

        extra: dict[str, Any] = {"ContentType": content_type}
        seconds = cache_seconds if cache_seconds is not None else REGISTRY[bucket].cache_seconds
        if seconds is not None:
            extra["CacheControl"] = f"max-age={seconds}"

        response = await self._call(
            "put_object",
            bucket,
            key,
            Bucket=self.bucket_name(bucket),
            Key=key,
            Body=payload,
            **extra,
        )
        logger.info("storage.object_written", bucket=bucket.value, key=key, size=len(payload))
        return StoredObject(
            bucket=bucket,
            key=key,
            size=len(payload),
            content_type=content_type,
            etag=str(response["ETag"]).strip('"'),
        )

    async def delete(self, bucket: Bucket, key: str) -> None:
        """Supprime un objet.

        Idempotent : S3 ne signale pas une clé absente, et on ne transforme
        pas ce silence en erreur. Une **panne**, en revanche, lève — la
        confondre avec un succès ferait croire à une suppression qui n'a pas
        eu lieu, ce qui est le pire des deux mondes pour une demande
        d'effacement.
        """
        await self._call("delete_object", bucket, key, Bucket=self.bucket_name(bucket), Key=key)
        logger.info("storage.object_deleted", bucket=bucket.value, key=key)

    # -----------------------------------------------------------------
    # Lecture
    # -----------------------------------------------------------------

    async def get(self, bucket: Bucket, key: str) -> bytes:
        """Lit un objet."""
        response = await self._call(
            "get_object", bucket, key, Bucket=self.bucket_name(bucket), Key=key
        )
        async with response["Body"] as stream:
            payload: bytes = await stream.read()
        return payload

    async def exists(self, bucket: Bucket, key: str) -> bool:
        """Indique si la clé existe."""
        try:
            await self._call("head_object", bucket, key, Bucket=self.bucket_name(bucket), Key=key)
        except ObjectNotFoundError:
            return False
        return True

    async def list(self, bucket: Bucket, prefix: str) -> list[ObjectSummary]:
        """Liste les objets sous un préfixe, en suivant la pagination.

        La pagination n'est pas optionnelle : S3 rend mille clés au plus par
        réponse, et s'arrêter à la première page produirait un listage
        silencieusement tronqué.
        """
        paginator = self._client.get_paginator("list_objects_v2")
        summaries: list[ObjectSummary] = []
        try:
            async for page in paginator.paginate(Bucket=self.bucket_name(bucket), Prefix=prefix):
                summaries.extend(
                    ObjectSummary(
                        key=entry["Key"],
                        size=entry["Size"],
                        last_modified=entry["LastModified"],
                    )
                    for entry in page.get("Contents", [])
                )
        except (ClientError, BotoCoreError) as exc:
            raise self._translate(exc, operation="list", bucket=bucket, key=prefix) from exc
        return summaries

    # -----------------------------------------------------------------
    # Signatures
    # -----------------------------------------------------------------

    async def presigned_get_url(
        self,
        bucket: Bucket,
        key: str,
        *,
        ttl_seconds: int | None = None,
        download_as: str | None = None,
    ) -> str:
        """Produit une URL de lecture temporaire.

        Le calcul est **purement local** — une signature HMAC — donc sans
        appel réseau : signer une page entière de résultats ne coûte rien.
        `generate_presigned_url` est néanmoins une coroutine en aioboto3,
        contrairement à boto3.
        """
        params: dict[str, Any] = {"Bucket": self.bucket_name(bucket), "Key": key}
        if download_as is not None:
            # Restitue le nom d'origine sans l'avoir mis dans la clé.
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{_sanitize_filename(download_as)}"'
            )
        try:
            url: str = await self._client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=self._ttl(ttl_seconds)
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._translate(exc, operation="presign_get", bucket=bucket, key=key) from exc
        return url

    async def presigned_put_url(
        self,
        bucket: Bucket,
        key: str,
        *,
        content_type: str,
        ttl_seconds: int | None = None,
    ) -> PresignedUpload:
        """Produit une autorisation d'écriture directe depuis le client.

        Le type MIME est **couvert par la signature** : un client qui
        enverrait autre chose que ce qui a été annoncé se verrait refusé par
        le fournisseur. C'est ce qui permet d'appliquer la politique du
        bucket alors même que les octets ne passent pas par l'API.
        """
        check_payload(
            bucket, size=0, content_type=content_type, size_ceiling=self._settings.max_upload_bytes
        )
        ttl = self._ttl(ttl_seconds)
        try:
            url: str = await self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket_name(bucket),
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=ttl,
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._translate(exc, operation="presign_put", bucket=bucket, key=key) from exc

        policy = REGISTRY[bucket]
        return PresignedUpload(
            url=url,
            key=key,
            content_type=content_type,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            max_bytes=min(
                policy.max_bytes or self._settings.max_upload_bytes,
                self._settings.max_upload_bytes,
            ),
        )

    # -----------------------------------------------------------------
    # Santé
    # -----------------------------------------------------------------

    async def check_health(self) -> None:
        """Vérifie que chaque bucket déclaré répond.

        Un **listage borné à une clé**, et non un `HEAD` sur le bucket.
        Les deux disent la même chose sur un S3 nu, mais pas derrière un
        proxy : `HEAD` y revient régulièrement en 404 alors que le bucket
        existe et que l'écriture fonctionne — la sonde annonce alors une
        panne de stockage qui n'existe pas, sur la ligne que tout le
        monde lit avant de déployer.

        Le listage traverse les mêmes chemins que les opérations réelles,
        donc il échoue quand elles échouent, et seulement là.
        """
        for bucket in Bucket:
            await self._call(
                "list_objects_v2", bucket, None, Bucket=self.bucket_name(bucket), MaxKeys=1
            )

    # -----------------------------------------------------------------
    # Rouages internes
    # -----------------------------------------------------------------

    def _ttl(self, requested: int | None) -> int:
        """Borne la durée demandée par le plafond du fournisseur."""
        ttl = requested if requested is not None else self._settings.presign_ttl_seconds
        return min(ttl, self._settings.profile.max_presign_seconds)

    async def _call(self, operation: str, bucket: Bucket, key: str | None, **kwargs: Any) -> Any:
        """Exécute une opération S3 et traduit ses échecs en erreurs typées.

        Centralisé pour que la traduction soit identique partout : une seule
        façon de convertir un `ClientError`, donc une seule à maintenir.
        """
        try:
            return await getattr(self._client, operation)(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise self._translate(exc, operation=operation, bucket=bucket, key=key) from exc

    def _translate(
        self, exc: Exception, *, operation: str, bucket: Bucket, key: str | None
    ) -> StorageError:
        """Convertit une exception du SDK en erreur du domaine technique.

        Ne journalise ni la clé d'accès, ni l'URL présignée : cette dernière
        porte une signature valide plusieurs heures, et un journal survit
        bien plus longtemps qu'un incident.
        """
        code = None
        if isinstance(exc, ClientError):
            code = str(exc.response.get("Error", {}).get("Code", "")) or None

        name = self.bucket_name(bucket)
        if code in _MISSING_CODES:
            return ObjectNotFoundError(
                "L'objet demandé n'existe pas.",
                operation=operation,
                bucket=name,
                key=key,
                provider_code=code,
            )

        logger.error(
            "storage.operation_failed",
            operation=operation,
            bucket=bucket.value,
            key=key,
            provider_code=code,
            error_type=type(exc).__name__,
        )
        return StorageError(
            "Le service de stockage est temporairement indisponible.",
            operation=operation,
            bucket=name,
            key=key,
            provider_code=code,
        )


def _sanitize_filename(name: str) -> str:
    """Neutralise ce qui casserait l'en-tête `Content-Disposition`.

    Un guillemet ou un retour à la ligne dans ce nom permettrait d'injecter
    un en-tête HTTP supplémentaire dans la réponse du fournisseur.
    """
    return "".join(char for char in name if char.isprintable() and char not in '"\\\r\n')[:200]
