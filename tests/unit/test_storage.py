"""Politique des buckets, adaptateur en mémoire et catalogue de fournisseurs.

Le point central de ce fichier : la **promesse de bascule**. Changer de
fournisseur doit se réduire à une variable d'environnement. Ce qui le
garantit n'est pas une intention, c'est le fait que chaque particularité
soit décrite en données et qu'aucun aiguillage par fournisseur n'existe
hors de la fabrique.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from karria.shared.config import PROFILES, Provider, StorageSettings
from karria.shared.storage import (
    REGISTRY,
    Bucket,
    InMemoryObjectStorage,
    ObjectNotFoundError,
    PayloadRejectedError,
    check_payload,
    physical_name,
)

pytestmark = pytest.mark.unit

JPEG = "image/jpeg"


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage(StorageSettings(), "test")


class TestCatalogueDesFournisseurs:
    def test_chaque_fournisseur_a_un_profil(self) -> None:
        """Ajouter une valeur à l'énumération sans son profil casse ici.

        C'est le filet qui évite qu'un fournisseur déclaré mais non décrit
        n'échoue au premier envoi de fichier, en production.
        """
        assert set(Provider) == set(PROFILES)

    @pytest.mark.parametrize(
        ("provider", "endpoint"),
        [
            (Provider.SCALEWAY, "https://s3.fr-par.scw.cloud"),
            (Provider.OVH, "https://s3.gra.io.cloud.ovh.net"),
            (Provider.BACKBLAZE, "https://s3.eu-central-003.backblazeb2.com"),
            (Provider.MINIO, "http://localhost:9000"),
        ],
    )
    def test_lendpoint_est_derive_du_fournisseur(self, provider: Provider, endpoint: str) -> None:
        """Personne ne recopie une URL d'endpoint dans un `.env`.

        Une URL saisie à la main est une faute de frappe en attente, et elle
        échoue au premier envoi — donc chez un utilisateur, pas au
        déploiement.
        """
        settings = StorageSettings(
            provider=provider, access_key=SecretStr("k"), secret_key=SecretStr("s")
        )

        assert settings.resolved_endpoint == endpoint

    def test_aws_laisse_le_sdk_choisir_son_endpoint(self) -> None:
        settings = StorageSettings(
            provider=Provider.AWS, access_key=SecretStr("k"), secret_key=SecretStr("s")
        )

        assert settings.resolved_endpoint is None

    def test_cloudflare_encode_le_compte_dans_lendpoint(self) -> None:
        settings = StorageSettings(
            provider=Provider.CLOUDFLARE_R2,
            account_id="abc123",
            access_key=SecretStr("k"),
            secret_key=SecretStr("s"),
        )

        assert settings.resolved_endpoint == "https://abc123.r2.cloudflarestorage.com"

    def test_une_region_explicite_prime_sur_le_defaut(self) -> None:
        settings = StorageSettings(
            provider=Provider.SCALEWAY,
            region="nl-ams",
            access_key=SecretStr("k"),
            secret_key=SecretStr("s"),
        )

        assert settings.resolved_endpoint == "https://s3.nl-ams.scw.cloud"

    def test_un_endpoint_force_court_circuite_la_derivation(self) -> None:
        """Échappatoire, le temps d'ajouter un profil au catalogue."""
        settings = StorageSettings(
            provider=Provider.SCALEWAY,
            endpoint_url="https://exotique.example.com",
            access_key=SecretStr("k"),
            secret_key=SecretStr("s"),
        )

        assert settings.resolved_endpoint == "https://exotique.example.com"

    def test_minio_adresse_par_le_chemin(self) -> None:
        """MinIO n'accepte pas l'adressage par nom d'hôte."""
        assert PROFILES[Provider.MINIO].addressing == "path"
        assert PROFILES[Provider.SCALEWAY].addressing == "virtual"


class TestCoherenceDeLaConfiguration:
    def test_des_secrets_vides_sont_refuses(self) -> None:
        with pytest.raises(ValidationError, match="STORAGE__ACCESS_KEY"):
            StorageSettings(provider=Provider.SCALEWAY)

    def test_le_mode_memoire_nexige_aucun_secret(self) -> None:
        assert StorageSettings(provider=Provider.MEMORY).resolved_endpoint is None

    def test_cloudflare_exige_un_identifiant_de_compte(self) -> None:
        with pytest.raises(ValidationError, match="STORAGE__ACCOUNT_ID"):
            StorageSettings(
                provider=Provider.CLOUDFLARE_R2,
                access_key=SecretStr("k"),
                secret_key=SecretStr("s"),
            )

    def test_un_ttl_au_dela_du_plafond_du_fournisseur_echoue_au_demarrage(self) -> None:
        """Une signature de huit jours serait refusée à la première lecture.

        Mieux vaut que le conteneur ne démarre pas.
        """
        with pytest.raises(ValidationError, match="PRESIGN_TTL_SECONDS"):
            StorageSettings(
                provider=Provider.SCALEWAY,
                presign_ttl_seconds=8 * 24 * 3600,
                access_key=SecretStr("k"),
                secret_key=SecretStr("s"),
            )


class TestNommageDesBuckets:
    def test_le_nom_porte_le_prefixe_et_lenvironnement(self) -> None:
        """C'est ce qui empêche la recette d'écraser un fichier de production.

        La séparation par identifiants ne suffit pas : une clé d'API mal
        copiée est vite arrivée, un nom de bucket différent, jamais.
        """
        assert (
            physical_name(Bucket.MEDIA, prefix="karria", environment="production")
            == "karria-production-media"
        )

    def test_deux_environnements_ne_partagent_jamais_un_bucket(self) -> None:
        recette = physical_name(Bucket.MEDIA, prefix="karria", environment="staging")
        production = physical_name(Bucket.MEDIA, prefix="karria", environment="production")

        assert recette != production

    def test_chaque_bucket_declare_a_une_politique(self) -> None:
        """Une valeur ajoutée à l'énumération sans politique casse ici."""
        assert set(Bucket) == set(REGISTRY)

    def test_aucun_bucket_nest_public_par_defaut(self) -> None:
        """Un bucket public est une fuite de données à un mauvais nom près."""
        assert not any(policy.public_read for policy in REGISTRY.values())


class TestPolitiqueALecriture:
    def test_accepte_un_contenu_conforme(self) -> None:
        check_payload(Bucket.MEDIA, size=1024, content_type=JPEG, size_ceiling=10_000_000)

    def test_refuse_au_dela_de_la_taille_du_bucket(self) -> None:
        with pytest.raises(PayloadRejectedError, match="taille maximale"):
            check_payload(
                Bucket.MEDIA, size=6 * 1024 * 1024, content_type=JPEG, size_ceiling=100_000_000
            )

    def test_le_plafond_global_sapplique_sil_est_plus_strict(self) -> None:
        with pytest.raises(PayloadRejectedError):
            check_payload(Bucket.MEDIA, size=2048, content_type=JPEG, size_ceiling=1024)

    def test_refuse_un_type_mime_hors_politique(self) -> None:
        with pytest.raises(PayloadRejectedError, match="type de fichier"):
            check_payload(
                Bucket.MEDIA,
                size=10,
                content_type="application/x-msdownload",
                size_ceiling=10_000_000,
            )

    def test_lerreur_nomme_le_champ_concerne(self) -> None:
        with pytest.raises(PayloadRejectedError) as capture:
            check_payload(Bucket.MEDIA, size=10, content_type="text/html", size_ceiling=10_000_000)

        assert capture.value.details["field"] == "content_type"

    def test_un_bucket_sans_liste_blanche_accepte_tout_type(self) -> None:
        check_payload(
            Bucket.TEMP, size=10, content_type="application/octet-stream", size_ceiling=10_000
        )


class TestAdaptateurEnMemoire:
    async def test_aller_retour(self, storage: InMemoryObjectStorage) -> None:
        stored = await storage.put(Bucket.MEDIA, "a/b.jpg", b"octets", content_type=JPEG)

        assert stored.key == "a/b.jpg"
        assert stored.size == 6
        assert await storage.get(Bucket.MEDIA, "a/b.jpg") == b"octets"

    async def test_applique_la_meme_politique_que_s3(self, storage: InMemoryObjectStorage) -> None:
        """Une doublure plus permissive que le vrai système laisserait passer
        en test ce qui échouerait en production.
        """
        with pytest.raises(PayloadRejectedError):
            await storage.put(Bucket.MEDIA, "a/b.exe", b"x", content_type="application/exe")

    async def test_lire_une_cle_absente_leve_une_erreur_distincte(
        self, storage: InMemoryObjectStorage
    ) -> None:
        """Un fichier absent est un cas métier ; un service injoignable, un
        incident. Les confondre fait passer une panne pour une absence.
        """
        with pytest.raises(ObjectNotFoundError):
            await storage.get(Bucket.MEDIA, "inexistant.jpg")

    async def test_la_suppression_est_idempotente(self, storage: InMemoryObjectStorage) -> None:
        await storage.put(Bucket.MEDIA, "a.jpg", b"x", content_type=JPEG)

        await storage.delete(Bucket.MEDIA, "a.jpg")
        await storage.delete(Bucket.MEDIA, "a.jpg")  # ne doit pas lever

        assert not await storage.exists(Bucket.MEDIA, "a.jpg")

    async def test_les_buckets_sont_cloisonnes(self, storage: InMemoryObjectStorage) -> None:
        await storage.put(Bucket.MEDIA, "meme-cle", b"media", content_type=JPEG)
        await storage.put(Bucket.TEMP, "meme-cle", b"temp", content_type=JPEG)

        assert await storage.get(Bucket.MEDIA, "meme-cle") == b"media"
        assert await storage.get(Bucket.TEMP, "meme-cle") == b"temp"

    async def test_le_listage_est_filtre_par_prefixe_et_trie(
        self, storage: InMemoryObjectStorage
    ) -> None:
        for key in ("users/b.jpg", "users/a.jpg", "autre/c.jpg"):
            await storage.put(Bucket.MEDIA, key, b"x", content_type=JPEG)

        found = await storage.list(Bucket.MEDIA, "users/")

        assert [entry.key for entry in found] == ["users/a.jpg", "users/b.jpg"]

    async def test_lurl_signee_designe_le_bucket_physique(
        self, storage: InMemoryObjectStorage
    ) -> None:
        url = await storage.presigned_get_url(Bucket.MEDIA, "a/b.jpg")

        assert "karria-test-media" in url
        assert "a/b.jpg" in url

    async def test_lautorisation_denvoi_porte_ses_bornes(
        self, storage: InMemoryObjectStorage
    ) -> None:
        upload = await storage.presigned_put_url(Bucket.MEDIA, "a/b.jpg", content_type=JPEG)

        assert upload.key == "a/b.jpg"
        assert upload.content_type == JPEG
        assert upload.max_bytes == REGISTRY[Bucket.MEDIA].max_bytes

    async def test_le_ttl_est_borne_par_le_plafond_du_fournisseur(self) -> None:
        settings = StorageSettings(provider=Provider.MEMORY, presign_ttl_seconds=3600)
        storage = InMemoryObjectStorage(settings, "test")

        url = await storage.presigned_get_url(Bucket.MEDIA, "a.jpg", ttl_seconds=10**9)

        assert f"expires={settings.profile.max_presign_seconds}" in url

    async def test_un_type_hors_politique_est_refuse_avant_meme_la_signature(
        self, storage: InMemoryObjectStorage
    ) -> None:
        """Signer une autorisation pour un type refusé produirait une URL
        que le fournisseur rejetterait — après que l'utilisateur a envoyé
        ses octets.
        """
        with pytest.raises(PayloadRejectedError):
            await storage.presigned_put_url(Bucket.MEDIA, "a.exe", content_type="application/exe")
