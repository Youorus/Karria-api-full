"""Crée et configure les buckets déclarés au registre.

    make storage-provision

**Idempotent** : à relancer à chaque déploiement sans crainte. Un bucket
qui existe déjà voit seulement sa configuration réappliquée.

C'est ce script qui réalise la promesse « je déclare mon bucket et c'est
tout » : on n'en crée aucun à la main. Une valeur dans `Bucket`, une entrée
dans `REGISTRY`, cette commande — et l'usage est ouvert, dans tous les
environnements, chez n'importe quel fournisseur du catalogue.

Ce qu'il applique, dans l'ordre :

1. crée le bucket s'il manque, avec la contrainte de localisation ;
2. active le versionnement si la politique le demande ;
3. pose la règle d'expiration automatique ;
4. n'ouvre la lecture publique que si le bucket la déclare — jamais par
   défaut, un bucket public étant une fuite de données à un mauvais nom de
   fichier près ;
5. pose la configuration CORS quand le navigateur écrit directement, en
   n'autorisant **que** les origines du frontend.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from typing import Any, Final

from botocore.exceptions import ClientError

from karria.shared.config import Settings, get_settings
from karria.shared.logging import configure_logging, get_logger
from karria.shared.storage import REGISTRY, Bucket, BucketPolicy, S3ObjectStorage

logger = get_logger("scripts.storage_provision")


#: Ce que le fournisseur répond quand le bucket existe déjà et nous
#: appartient. Rattrapé plutôt que propagé : le script doit pouvoir se
#: relancer autant de fois qu'on veut, puisqu'il tourne après **chaque**
#: déploiement qui ajoute une migration.
_ALREADY_OURS: Final = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})


async def _ensure_bucket(client: Any, name: str, region: str) -> bool:
    """Crée le bucket s'il n'existe pas. Retourne `True` s'il a été créé.

    `head_bucket` ne suffit pas à décider. Selon le fournisseur et les
    droits de la clé, il répond 404 sur un bucket qui existe pourtant —
    MinIO le fait quand la clé n'a pas le droit de le lister. On tente
    donc la création, et l'on traite « il est déjà à vous » comme le
    succès que c'est.

    Sans ce rattrapage, le script échoue à sa **deuxième** exécution, et
    l'on découvre la panne pendant un déploiement plutôt qu'à froid.
    """
    try:
        await client.head_bucket(Bucket=name)
    except ClientError:
        # `us-east-1` est la région implicite de S3 : lui passer une
        # contrainte de localisation explicite est refusé par AWS.
        parameters: dict[str, Any] = {"Bucket": name}
        if region and region != "us-east-1":
            parameters["CreateBucketConfiguration"] = {"LocationConstraint": region}
        try:
            await client.create_bucket(**parameters)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in _ALREADY_OURS:
                raise
            return False
        return True
    return False


async def _apply_policy(client: Any, name: str, policy: BucketPolicy, settings: Settings) -> None:
    """Réapplique la politique déclarée au registre."""
    await client.put_bucket_versioning(
        Bucket=name,
        VersioningConfiguration={"Status": "Enabled" if policy.versioning else "Suspended"},
    )

    if policy.expire_after_days is not None:
        await client.put_bucket_lifecycle_configuration(
            Bucket=name,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "expiration-automatique",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": policy.expire_after_days},
                    }
                ]
            },
        )

    if policy.public_read:
        await client.put_bucket_policy(
            Bucket=name,
            Policy=(
                '{"Version":"2012-10-17","Statement":[{"Sid":"PublicRead",'
                '"Effect":"Allow","Principal":"*","Action":"s3:GetObject",'
                f'"Resource":"arn:aws:s3:::{name}/*"}}]}}'
            ),
        )

    # CORS : indispensable dès qu'un navigateur écrit par URL présignée.
    # Jamais `*` sur un bucket qui reçoit des écritures — ce serait offrir à
    # n'importe quel site la capacité d'y déposer des fichiers au nom d'un
    # de nos utilisateurs.
    if not settings.storage.profile.supports_bucket_cors:
        logger.info("storage.cors_skipped", bucket=name, provider=settings.storage.provider.value)
        return
    await client.put_bucket_cors(
        Bucket=name,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedOrigins": settings.security.allowed_origins,
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        },
    )


async def provision(settings: Settings) -> int:
    """Provisionne tous les buckets du registre. Retourne le code de sortie."""
    if not settings.storage.profile.durable:
        logger.error(
            "storage.provision_impossible",
            reason="provider_not_durable",
            provider=settings.storage.provider.value,
        )
        print("Rien à provisionner : le fournisseur configuré ne stocke rien durablement.")
        return 1

    created: list[str] = []
    existing: list[str] = []

    async with AsyncExitStack() as stack:
        storage = await S3ObjectStorage.open(
            settings.storage, settings.app.environment.value, stack
        )
        client = storage._client

        for bucket in Bucket:
            name = storage.bucket_name(bucket)
            was_created = await _ensure_bucket(client, name, settings.storage.resolved_region)
            await _apply_policy(client, name, REGISTRY[bucket], settings)
            (created if was_created else existing).append(name)

    logger.info(
        "storage.provisioned",
        provider=settings.storage.provider.value,
        environment=settings.app.environment.value,
        created=created,
        already_present=existing,
    )
    print(f"Buckets créés      : {', '.join(created) or 'aucun'}")
    print(f"Buckets existants  : {', '.join(existing) or 'aucun'}")
    return 0


def main() -> int:
    """Point d'entrée."""
    settings = get_settings()
    configure_logging(settings.observability)
    return asyncio.run(provision(settings))


if __name__ == "__main__":
    sys.exit(main())
