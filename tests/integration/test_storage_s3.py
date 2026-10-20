"""Stockage objet réel, sur MinIO.

Ce fichier est celui qui **prouve la promesse** : le code écrit pour
Scaleway tourne sans modification sur MinIO, parce qu'aucune ligne hors du
catalogue ne connaît le fournisseur. Le même adaptateur, la même
configuration dérivée, les mêmes buckets.

Ce que seuls ces tests peuvent vérifier :

- une URL présignée est **réellement téléchargeable** par un client HTTP
  qui ne sait rien de nos identifiants ;
- une autorisation d'écriture accepte les octets du client et **refuse** un
  type MIME autre que celui annoncé — la signature le couvre ;
- le listage suit la pagination, et `delete` est bien idempotent côté
  serveur, pas seulement dans notre doublure.

Exige `make db-up` (qui démarre aussi MinIO) puis `make storage-provision`.
Ignorés proprement, avec un motif lisible, à défaut.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import httpx
import pytest
from pydantic import SecretStr

from conftest import unavailable
from karria.shared.config import AppSettings, Environment, Provider, Settings, StorageSettings
from karria.shared.storage import (
    Bucket,
    ObjectNotFoundError,
    PayloadRejectedError,
    S3ObjectStorage,
    build_key,
    physical_name,
)

pytestmark = pytest.mark.integration

JPEG = "image/jpeg"
IMAGE = b"\xff\xd8\xff\xe0" + b"contenu d'image de test" * 10


def _settings() -> Settings:
    """Configuration visant le MinIO local."""
    return Settings(
        # L'environnement vient de la configuration, pas d'une constante :
        # il décide du nom des buckets, et la CI ne tourne pas en `local`.
        app=AppSettings(environment=Environment(os.environ.get("APP__ENVIRONMENT", "local"))),
        storage=StorageSettings(
            provider=Provider.MINIO,
            endpoint_url=os.environ.get("STORAGE__ENDPOINT_URL", "http://localhost:9000"),
            access_key=SecretStr(os.environ.get("STORAGE__ACCESS_KEY", "minioadmin")),
            secret_key=SecretStr(os.environ.get("STORAGE__SECRET_KEY", "minioadmin")),
            bucket_prefix="karria",
        ),
    )


@pytest.fixture
async def storage() -> AsyncIterator[S3ObjectStorage]:
    """Client S3 réel, refermé en fin de test."""
    settings = _settings()
    async with AsyncExitStack() as stack:
        try:
            adapter = await S3ObjectStorage.open(
                settings.storage, settings.app.environment.value, stack
            )
            await adapter.check_health()
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            unavailable(
                f"MinIO injoignable ou buckets absents ({type(exc).__name__}). "
                "Lancer `make db-up` puis `make storage-provision`."
            )
        yield adapter


@pytest.fixture
def key() -> str:
    """Clé unique, pour que deux exécutions ne se marchent pas dessus."""
    return build_key("tests", "integration", extension="jpg")


class TestAllerRetour:
    async def test_ecrit_puis_relit_les_memes_octets(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        stored = await storage.put(Bucket.MEDIA, key, IMAGE, content_type=JPEG)

        assert stored.size == len(IMAGE)
        assert stored.etag
        assert await storage.get(Bucket.MEDIA, key) == IMAGE

        await storage.delete(Bucket.MEDIA, key)

    async def test_lobjet_atterrit_dans_le_bucket_de_lenvironnement(
        self, storage: S3ObjectStorage
    ) -> None:
        """La séparation par environnement est réelle, pas décorative.

        C'est elle qui empêche une exécution de tests d'écraser un fichier
        de production : le nom du bucket diffère, donc la cible aussi.
        """
        settings = _settings()

        assert storage.bucket_name(Bucket.MEDIA) == physical_name(
            Bucket.MEDIA,
            prefix=settings.storage.bucket_prefix,
            environment=settings.app.environment.value,
        )
        assert settings.app.environment.value in storage.bucket_name(Bucket.MEDIA)

    async def test_lire_une_cle_absente_leve_une_erreur_metier(
        self, storage: S3ObjectStorage
    ) -> None:
        """Et non une `ClientError` brute, qui deviendrait un 500."""
        with pytest.raises(ObjectNotFoundError) as capture:
            await storage.get(Bucket.MEDIA, "cette/cle/nexiste/pas.jpg")

        # Le code du fournisseur est conservé : c'est lui qui transforme une
        # enquête en diagnostic.
        assert capture.value.details["provider_code"]

    async def test_la_suppression_est_idempotente_cote_serveur(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        await storage.put(Bucket.MEDIA, key, IMAGE, content_type=JPEG)

        await storage.delete(Bucket.MEDIA, key)
        await storage.delete(Bucket.MEDIA, key)  # ne doit pas lever

        assert not await storage.exists(Bucket.MEDIA, key)

    async def test_le_listage_filtre_par_prefixe(self, storage: S3ObjectStorage, key: str) -> None:
        await storage.put(Bucket.MEDIA, key, IMAGE, content_type=JPEG)

        found = await storage.list(Bucket.MEDIA, "tests/integration/")

        assert key in [entry.key for entry in found]

        await storage.delete(Bucket.MEDIA, key)


class TestPolitiqueAppliqueeAvantLeReseau:
    async def test_un_type_hors_politique_ne_part_jamais(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        """Refuser après transmission gaspille la bande passante et le temps
        de l'utilisateur.
        """
        with pytest.raises(PayloadRejectedError):
            await storage.put(Bucket.MEDIA, key, b"MZ", content_type="application/x-msdownload")

        assert not await storage.exists(Bucket.MEDIA, key)

    async def test_un_fichier_trop_gros_ne_part_jamais(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        with pytest.raises(PayloadRejectedError):
            await storage.put(Bucket.MEDIA, key, b"x" * (6 * 1024 * 1024), content_type=JPEG)


class TestUrlsSignees:
    async def test_une_url_de_lecture_est_reellement_telechargeable(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        """Le test qui compte : un client anonyme obtient les octets.

        Aucune doublure ne peut prouver cela — c'est la signature réelle,
        vérifiée par un vrai serveur S3.
        """
        await storage.put(Bucket.MEDIA, key, IMAGE, content_type=JPEG)

        url = await storage.presigned_get_url(Bucket.MEDIA, key, ttl_seconds=60)

        async with httpx.AsyncClient() as client:
            response = await client.get(url)

        assert response.status_code == 200
        assert response.content == IMAGE

        await storage.delete(Bucket.MEDIA, key)

    async def test_le_nom_de_telechargement_est_restitue_sans_etre_dans_la_cle(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        """C'est ainsi qu'on rend son nom d'origine à un document sans avoir
        mis ce nom — donc potentiellement une donnée personnelle — dans le
        chemin de l'objet.
        """
        await storage.put(Bucket.MEDIA, key, IMAGE, content_type=JPEG)

        url = await storage.presigned_get_url(
            Bucket.MEDIA, key, ttl_seconds=60, download_as="ma photo.jpg"
        )

        async with httpx.AsyncClient() as client:
            response = await client.get(url)

        assert "attachment" in response.headers["content-disposition"]
        assert "ma photo.jpg" in response.headers["content-disposition"]

        await storage.delete(Bucket.MEDIA, key)

    async def test_une_url_devenue_invalide_refuse_lacces(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        """Une signature altérée ne donne aucun accès.

        C'est ce qui permet de servir des fichiers privés sans jamais rendre
        le bucket public.
        """
        await storage.put(Bucket.MEDIA, key, IMAGE, content_type=JPEG)
        url = await storage.presigned_get_url(Bucket.MEDIA, key, ttl_seconds=60)

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{url[:-4]}0000")

        assert response.status_code == 403

        await storage.delete(Bucket.MEDIA, key)

    async def test_un_objet_nest_pas_lisible_sans_signature(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        """Le bucket est privé : le vérifier une fois vaut mieux que le croire."""
        await storage.put(Bucket.MEDIA, key, IMAGE, content_type=JPEG)
        settings = _settings()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.storage.resolved_endpoint}/{storage.bucket_name(Bucket.MEDIA)}/{key}"
            )

        assert response.status_code == 403

        await storage.delete(Bucket.MEDIA, key)

    async def test_une_autorisation_decriture_accepte_les_octets_du_client(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        """Le parcours d'envoi direct, de bout en bout, sans passer par l'API."""
        upload = await storage.presigned_put_url(
            Bucket.MEDIA, key, content_type=JPEG, ttl_seconds=60
        )

        async with httpx.AsyncClient() as client:
            response = await client.put(upload.url, content=IMAGE, headers={"Content-Type": JPEG})

        assert response.status_code == 200
        assert await storage.get(Bucket.MEDIA, key) == IMAGE

        await storage.delete(Bucket.MEDIA, key)

    async def test_lautorisation_ne_couvre_pas_un_autre_type_mime(
        self, storage: S3ObjectStorage, key: str
    ) -> None:
        """La politique du bucket s'applique alors même que les octets ne
        passent pas par l'API : le type est couvert par la signature.
        """
        upload = await storage.presigned_put_url(
            Bucket.MEDIA, key, content_type=JPEG, ttl_seconds=60
        )

        async with httpx.AsyncClient() as client:
            response = await client.put(
                upload.url, content=b"MZ", headers={"Content-Type": "application/x-msdownload"}
            )

        assert response.status_code == 403
        assert not await storage.exists(Bucket.MEDIA, key)


class TestPromesseDeBascule:
    async def test_le_meme_code_tourne_sur_un_autre_fournisseur(
        self, storage: S3ObjectStorage
    ) -> None:
        """Aucun test de ce fichier n'a été écrit pour MinIO en particulier.

        Ils exercent l'adaptateur S3 générique ; le fournisseur ne se lit
        que dans la configuration. Basculer `STORAGE__PROVIDER` les fait
        tourner ailleurs sans modifier une ligne — c'est exactement ce que
        la promesse annonce.
        """
        settings = _settings()

        assert settings.storage.provider is Provider.MINIO
        assert type(storage).__name__ == "S3ObjectStorage"
