"""Images produites par le module (aperçus, vignettes, photo recadrée, BlurHash) et
signature des URL avec l'adresse publique du stockage."""

from __future__ import annotations

from contextlib import AsyncExitStack
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import pytest
from PIL import Image
from pydantic import SecretStr

from doubles import sample_image
from karria.shared.config import Provider, StorageSettings
from karria.shared.images import blurhash, crop_to_frame, image_size, resize_to_png, to_webp
from karria.shared.images.pillow import _base83, _to_linear, _to_srgb
from karria.shared.storage import Bucket
from karria.shared.storage.s3 import S3ObjectStorage

pytestmark = pytest.mark.unit


def _open(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data))


class TestImagesProduites:
    def test_reduction_en_png(self) -> None:
        out = _open(resize_to_png(sample_image(200, 100), max_edge=50))
        assert (out.format, out.size) == ("PNG", (50, 25))

    def test_une_petite_image_garde_sa_taille(self) -> None:
        assert image_size(resize_to_png(sample_image(40, 30), max_edge=50)) == (40, 30)

    def test_vignette_webp(self) -> None:
        out = _open(to_webp(sample_image(300, 150, fmt="PNG", alpha=True), max_edge=60))
        assert (out.format, out.size) == ("WEBP", (60, 30))

    def test_dimensions(self) -> None:
        assert image_size(sample_image(64, 48)) == (64, 48)

    def test_recadrage_d_une_image_large_au_rapport_du_cadre(self) -> None:
        out = _open(crop_to_frame(sample_image(200, 100), ratio=1.0, width=80))
        assert (out.format, out.size, out.mode) == ("PNG", (80, 80), "RGB")

    def test_recadrage_d_une_image_haute_et_point_garde(self) -> None:
        tall = Image.new("RGB", (100, 300), (0, 0, 0))
        tall.paste((255, 255, 255), (0, 0, 100, 100))  # le haut est blanc
        buffer = BytesIO()
        tall.save(buffer, "PNG")
        top = _open(crop_to_frame(buffer.getvalue(), ratio=1.0, focus=(0.5, 0.0), width=10))
        bottom = _open(crop_to_frame(buffer.getvalue(), ratio=1.0, focus=(0.5, 1.0), width=10))
        assert top.getpixel((5, 5)) == (255, 255, 255)
        assert bottom.getpixel((5, 5)) == (0, 0, 0)

    def test_detourage_en_disque(self) -> None:
        out = _open(crop_to_frame(sample_image(100, 100), ratio=0.8, circle=True, width=40))
        assert (out.mode, out.size) == ("RGBA", (40, 50))
        assert out.getpixel((0, 0))[3] == 0
        assert out.getpixel((20, 25))[3] == 255

    def test_le_redressement_exif_precede_le_recadrage(self) -> None:
        rotated = sample_image(100, 50, exif_orientation=6)  # portrait une fois redressée
        out = _open(crop_to_frame(rotated, ratio=0.5, width=50))
        assert out.size == (50, 100)


class TestBlurHash:
    def test_reference_d_une_image_unie(self) -> None:
        img = Image.new("RGB", (16, 16), (255, 0, 0))
        buffer = BytesIO()
        img.save(buffer, "PNG")
        value = blurhash(buffer.getvalue())
        # Une image unie : aucune composante alternative, couleur moyenne rouge pur.
        assert value[0] == _base83(3 + 4 * 9, 1)
        assert value[2:6] == _base83(0xFF0000, 4)
        assert len(value) == 6 + 2 * 19

    def test_une_seule_composante(self) -> None:
        value = blurhash(sample_image(), components_x=1, components_y=1)
        assert len(value) == 6
        assert value[:2] == "00"

    def test_conversions_de_couleur(self) -> None:
        assert _to_linear(0) == 0.0
        assert _to_linear(255) == pytest.approx(1.0)
        assert _to_srgb(-1.0) == 0
        assert _to_srgb(0.001) == 3
        assert _to_srgb(1.0) == 255


class _Signer:
    def __init__(self, host: str) -> None:
        self.host = host
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def generate_presigned_url(self, op: str, *, Params: Any, ExpiresIn: int) -> str:  # noqa: N803
        self.calls.append((op, Params))
        return f"{self.host}/{Params['Bucket']}/{Params['Key']}"


def _settings(public: str | None) -> StorageSettings:
    return StorageSettings(
        provider=Provider.MINIO,
        endpoint_url="http://karria-minio:9000",
        public_endpoint_url=public,
        access_key=SecretStr("cle"),
        secret_key=SecretStr("secret"),
    )


class TestSignaturePublique:
    async def test_le_client_de_signature_signe_seul(self) -> None:
        internal, public = _Signer("http://karria-minio:9000"), _Signer("https://storage.karria.fr")
        storage = S3ObjectStorage(internal, _settings(None), "test", signing_client=public)
        url = await storage.presigned_get_url(Bucket.MEDIA, "a.jpg")
        upload = await storage.presigned_put_url(Bucket.MEDIA, "b.jpg", content_type="image/jpeg")
        assert url.startswith("https://storage.karria.fr/")
        assert upload.url.startswith("https://storage.karria.fr/")
        assert internal.calls == []

    async def test_sans_client_de_signature_le_client_principal_signe(self) -> None:
        internal = _Signer("http://karria-minio:9000")
        storage = S3ObjectStorage(internal, _settings(None), "test")
        assert (await storage.presigned_get_url(Bucket.MEDIA, "a")).startswith("http://karria")

    @pytest.mark.parametrize(
        ("public", "host"),
        [("https://storage.karria.fr", "storage.karria.fr"), (None, "karria-minio:9000")],
    )
    async def test_l_ouverture_signe_pour_l_adresse_publique(
        self, public: str | None, host: str
    ) -> None:
        """Signature locale (HMAC) : aucun appel réseau, même avec un vrai client."""
        async with AsyncExitStack() as stack:
            storage = await S3ObjectStorage.open(_settings(public), "test", stack)
            url = await storage.presigned_get_url(Bucket.MEDIA, "a.jpg")
        assert urlparse(url).netloc == host
