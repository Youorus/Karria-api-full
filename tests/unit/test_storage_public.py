"""Le bucket public des logos : adresse stable, cache immuable, refus des buckets privés."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from karria.shared.config import Provider, StorageSettings
from karria.shared.storage import (
    REGISTRY,
    Bucket,
    InMemoryObjectStorage,
    S3ObjectStorage,
    StorageError,
)

pytestmark = pytest.mark.unit


def settings(provider: Provider = Provider.MINIO, **changes: Any) -> StorageSettings:
    values: dict[str, Any] = {
        "provider": provider,
        "access_key": SecretStr("cle"),
        "secret_key": SecretStr("secret"),
    }
    if provider is Provider.MINIO:
        values["endpoint_url"] = "http://karria-minio:9000"
    values.update(changes)
    return StorageSettings(**values)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def put_object(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"ETag": '"e"'}


def test_seul_le_bucket_des_logos_est_public() -> None:
    assert [b for b, policy in REGISTRY.items() if policy.public_read] == [Bucket.LOGOS]


@pytest.mark.parametrize(
    ("config", "url"),
    [
        (
            settings(public_endpoint_url="https://storage.karria.fr/"),
            "https://storage.karria.fr/karria-production-logos/ab/128.webp",
        ),
        (settings(), "http://karria-minio:9000/karria-production-logos/ab/128.webp"),
        (
            settings(Provider.AWS, region="eu-west-3"),
            "https://karria-production-logos.s3.eu-west-3.amazonaws.com/ab/128.webp",
        ),
        (
            settings(Provider.SCALEWAY),
            "https://karria-production-logos.s3.fr-par.scw.cloud/ab/128.webp",
        ),
    ],
)
def test_adresse_publique(config: StorageSettings, url: str) -> None:
    storage = S3ObjectStorage(object(), config, "production")
    assert storage.public_url(Bucket.LOGOS, "ab/128.webp") == url


def test_un_bucket_prive_n_a_pas_d_adresse_publique() -> None:
    with pytest.raises(StorageError):
        S3ObjectStorage(object(), settings(), "production").public_url(Bucket.MEDIA, "a")
    with pytest.raises(StorageError):
        InMemoryObjectStorage(settings(), "test").public_url(Bucket.MEDIA, "a")


def test_en_memoire_l_adresse_est_stable() -> None:
    storage = InMemoryObjectStorage(settings(), "test")
    assert storage.public_url(Bucket.LOGOS, "ab/1.webp") == storage.public_url(
        Bucket.LOGOS, "ab/1.webp"
    )


@pytest.mark.parametrize(
    ("bucket", "content_type", "header"),
    [
        (Bucket.LOGOS, "image/webp", "public, max-age=31536000, immutable"),
        (Bucket.MEDIA, "image/jpeg", "max-age=86400"),
    ],
)
async def test_le_cache_d_un_logo_est_immuable(
    bucket: Bucket, content_type: str, header: str
) -> None:
    client = Recorder()
    await S3ObjectStorage(client, settings(), "test").put(
        bucket, "ab/128.webp", b"x", content_type=content_type
    )
    assert client.calls[0]["CacheControl"] == header
