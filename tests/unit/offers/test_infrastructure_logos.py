"""Les logos côté adaptateurs : téléchargement poli et sûr, mise au carré, stock par condensat."""

from __future__ import annotations

from io import BytesIO

import httpx
import pytest
from PIL import Image

from karria.modules.offers.application import ProcessedLogo
from karria.modules.offers.domain.companies import ImageTraits
from karria.modules.offers.infrastructure import (
    HttpLogoFetcher,
    PillowLogoProcessor,
    StorageLogoStore,
)
from karria.modules.offers.infrastructure.logos import MAX_IMAGE_BYTES, is_public_address
from karria.shared.config import StorageSettings
from karria.shared.storage import Bucket, InMemoryObjectStorage

pytestmark = pytest.mark.unit

PNG = b"\x89PNG-octets"


def public(_host: str) -> list[str]:
    return ["93.184.216.34"]


def fetcher(handler: httpx.MockTransport, **kwargs: object) -> HttpLogoFetcher:
    client = httpx.AsyncClient(transport=handler)
    return HttpLogoFetcher(client, resolver=kwargs.pop("resolver", public), interval=0.0, **kwargs)  # type: ignore[arg-type]


def routes(table: dict[str, httpx.Response | Exception]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        found = table.get(str(request.url), httpx.Response(404))
        if isinstance(found, Exception):
            raise found
        return found

    return httpx.MockTransport(handle)


def image(status: int = 200, content: bytes = PNG, kind: str = "image/png") -> httpx.Response:
    return httpx.Response(status, content=content, headers={"content-type": kind})


class TestTelechargement:
    async def test_une_image(self) -> None:
        got = await fetcher(routes({"https://x.fr/l.png": image()})).fetch_image(
            "https://x.fr/l.png"
        )
        assert got.payload == PNG

    async def test_les_redirections_sont_suivies_et_bornees(self) -> None:
        table: dict[str, httpx.Response | Exception] = {
            "https://x.fr/a": httpx.Response(301, headers={"location": "/b"}),
            "https://x.fr/b": image(),
        }
        assert (await fetcher(routes(table)).fetch_image("https://x.fr/a")).payload == PNG

        loop = {"https://x.fr/a": httpx.Response(302, headers={"location": "/a"})}
        got = await fetcher(routes(loop)).fetch_image("https://x.fr/a")  # type: ignore[arg-type]
        assert got.payload is None
        assert got.permanent

    @pytest.mark.parametrize(
        ("response", "permanent"),
        [
            (httpx.Response(404), True),
            (httpx.Response(503), False),
            (httpx.Response(429), False),
            (httpx.ReadTimeout("lent"), False),
            (httpx.ConnectError("refus"), False),
            (image(kind="text/html"), True),
            (image(content=b"x" * (MAX_IMAGE_BYTES + 1)), True),
        ],
    )
    async def test_les_echecs(self, response: httpx.Response | Exception, permanent: bool) -> None:
        got = await fetcher(routes({"https://x.fr/l": response})).fetch_image("https://x.fr/l")
        assert got.payload is None
        assert got.permanent is permanent

    async def test_une_taille_annoncee_trop_grande_n_est_pas_lue(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "image/png", "content-length": str(MAX_IMAGE_BYTES + 1)},
                stream=httpx.ByteStream(b"x"),
            )

        got = await fetcher(httpx.MockTransport(handle)).fetch_image("https://x.fr/l")
        assert got.payload is None

    async def test_une_image_trop_lourde_sans_taille_annoncee_est_coupee(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                stream=httpx.ByteStream(b"x" * (MAX_IMAGE_BYTES + 1)),
            )

        got = await fetcher(httpx.MockTransport(handle)).fetch_image("https://x.fr/l")
        assert got.payload is None
        assert got.permanent

    @pytest.mark.parametrize(
        ("url", "addresses"),
        [
            ("http://169.254.169.254/latest", ["169.254.169.254"]),
            ("https://intranet.local/l.png", ["10.0.0.3"]),
            ("https://vide.fr/l.png", []),
            ("ftp://x.fr/l.png", ["93.184.216.34"]),
            ("https:///l.png", ["93.184.216.34"]),
        ],
    )
    async def test_jamais_une_adresse_privee(self, url: str, addresses: list[str]) -> None:
        def handle(request: httpx.Request) -> httpx.Response:  # pragma: no cover - jamais appelé
            raise AssertionError(request.url)

        got = await fetcher(httpx.MockTransport(handle), resolver=lambda _h: addresses).fetch_image(
            url
        )
        assert got.payload is None

    async def test_une_redirection_vers_le_reseau_interne_est_refusee(self) -> None:
        def resolve(host: str) -> list[str]:
            return ["127.0.0.1"] if host == "interne" else ["93.184.216.34"]

        table = {"https://x.fr/a": httpx.Response(302, headers={"location": "http://interne/"})}
        got = await fetcher(routes(table), resolver=resolve).fetch_image("https://x.fr/a")  # type: ignore[arg-type]
        assert got.payload is None

    async def test_un_nom_introuvable(self) -> None:
        def fail(_host: str) -> list[str]:
            raise OSError("nxdomain")

        got = await fetcher(routes({}), resolver=fail).fetch_image("https://nulle.part/l")
        assert got.payload is None

    @pytest.mark.parametrize(
        ("address", "public_ip"),
        [("93.184.216.34", True), ("192.168.1.1", False), ("::1", False), ("x", False)],
    )
    def test_adresses(self, address: str, public_ip: bool) -> None:
        assert is_public_address(address) is public_ip

    async def test_la_cadence_par_site_espace_les_departs(self) -> None:
        client = httpx.AsyncClient(transport=routes({"https://x.fr/l": image()}))
        polite = HttpLogoFetcher(client, resolver=public, interval=0.01, concurrency=1)
        for _ in range(2):
            assert (await polite.fetch_image("https://x.fr/l")).payload == PNG

    def test_le_client_de_production(self) -> None:
        client = HttpLogoFetcher.build_client()
        assert "KarriaLogoBot" in client.headers["user-agent"]
        assert not client.follow_redirects


class TestIconesDuSite:
    async def test_lues_sur_la_page_d_accueil_apres_redirection(self) -> None:
        html = '<head><link rel="apple-touch-icon" href="/a.png"></head>' + " " * 600_000
        table: dict[str, httpx.Response | Exception] = {
            "https://x.fr/": httpx.Response(301, headers={"location": "https://www.x.fr/"}),
            "https://www.x.fr/": httpx.Response(
                200, content=html.encode(), headers={"content-type": "text/html"}
            ),
        }
        assert await fetcher(routes(table)).site_icons("x.fr") == ["https://www.x.fr/a.png"]

    async def test_une_page_refusee(self) -> None:
        assert (
            await fetcher(routes({"https://x.fr/": httpx.Response(403)})).site_icons("x.fr") == []
        )


def png(
    size: tuple[int, int], color: tuple[int, ...], mode: str = "RGB", draw: bool = True
) -> bytes:
    canvas = Image.new(mode, size, color)
    if draw:
        mark = (200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30)
        for x in range(size[0] // 4, 3 * size[0] // 4):
            for y in range(size[1] // 3, 2 * size[1] // 3):
                canvas.putpixel((x, y), mark)
    buffer = BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


class TestMiseAuCarre:
    def test_un_bandeau_blanc_devient_un_carre_rogne(self) -> None:
        processed = PillowLogoProcessor().process(png((160, 60), (255, 255, 255)))

        assert processed is not None
        assert set(processed.renditions) == {128, 256}
        assert (processed.traits.width, processed.traits.height) == (80, 20)
        assert processed.traits.coverage == 1.0
        assert len(processed.digest) == 32
        with Image.open(BytesIO(processed.renditions[256])) as out:
            assert out.size == (256, 256)
            assert out.format == "WEBP"
            assert out.convert("RGB").getpixel((2, 2)) == pytest.approx((255, 255, 255), abs=3)

    def test_la_transparence_est_gardee(self) -> None:
        processed = PillowLogoProcessor().process(png((100, 100), (0, 0, 0, 0), "RGBA"))

        assert processed is not None
        assert processed.traits.width == 50
        assert 0 < processed.traits.coverage <= 1
        with Image.open(BytesIO(processed.renditions[128])) as out:
            assert out.convert("RGBA").getpixel((1, 1))[3] == 0

    def test_un_aplat_n_a_rien_de_visible(self) -> None:
        processed = PillowLogoProcessor().process(png((64, 64), (10, 20, 30), draw=False))
        assert processed is not None
        assert processed.traits.coverage == 0.0
        assert processed.renditions == {}

    def test_des_octets_illisibles(self) -> None:
        assert PillowLogoProcessor().process(b"pas une image") is None

    def test_une_image_demesuree_est_refusee(self) -> None:
        buffer = BytesIO()
        Image.new("1", (8_000, 8_000)).save(buffer, "PNG")
        assert PillowLogoProcessor().process(buffer.getvalue()) is None

    def test_le_condensat_suit_le_contenu(self) -> None:
        first = PillowLogoProcessor().process(png((100, 100), (255, 255, 255)))
        same = PillowLogoProcessor().process(png((100, 100), (255, 255, 255)))
        other = PillowLogoProcessor().process(png((100, 100), (0, 0, 255)))
        assert first is not None
        assert same is not None
        assert other is not None
        assert first.digest == same.digest != other.digest


class TestStock:
    async def test_range_chaque_taille_sous_le_condensat(self) -> None:
        storage = InMemoryObjectStorage(StorageSettings(), "test")
        store = StorageLogoStore(storage)
        logo = ProcessedLogo(
            digest="ab" * 16,
            traits=ImageTraits(10, 10, 1.0),
            renditions={256: b"grand", 128: b"petit"},
        )

        assert not await store.has(logo.digest)
        await store.put(logo)

        assert await store.has(logo.digest)
        assert await storage.get(Bucket.LOGOS, f"{logo.digest}/128.webp") == b"petit"
