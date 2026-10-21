"""Les adaptateurs légers d'`identity` : jetons, limiteur, envoi du code, JWKS."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from karria.modules.identity.domain import InvalidSocialTokenError, TooManyAttemptsError
from karria.modules.identity.infrastructure import (
    HttpJwksSource,
    InMemoryLoginThrottle,
    MailLoginCodeSender,
    Sha256TokenFactory,
)
from karria.shared.errors import DependencyUnavailableError
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError
from karria.shared.i18n import Language
from karria.shared.mail import MailDeliveryError, MailMessage, OutgoingMail
from karria.shared.mail.console import ConsoleMailer

pytestmark = pytest.mark.unit


class TestSha256TokenFactory:
    def test_les_jetons_sont_longs_et_uniques(self) -> None:
        factory = Sha256TokenFactory("poivre")
        a, b = factory.new_token(), factory.new_token()
        assert a != b
        assert len(a) >= 40

    def test_lempreinte_depend_du_poivre(self) -> None:
        assert Sha256TokenFactory("a").fingerprint("x") != Sha256TokenFactory("b").fingerprint("x")
        assert Sha256TokenFactory("a").fingerprint("x") == Sha256TokenFactory("a").fingerprint("x")

    def test_le_code_a_toujours_six_chiffres(self) -> None:
        factory = Sha256TokenFactory()
        codes = {factory.new_numeric_code(6) for _ in range(50)}
        assert all(len(c) == 6 and c.isdigit() for c in codes)
        assert len(codes) > 1

    def test_matches_compare_a_lempreinte(self) -> None:
        factory = Sha256TokenFactory("p")
        assert factory.matches("123456", factory.fingerprint("123456"))
        assert not factory.matches("123457", factory.fingerprint("123456"))


class TestInMemoryLoginThrottle:
    async def test_bloque_au_plafond_puis_libere_avec_le_temps(self) -> None:
        now = [0.0]
        throttle = InMemoryLoginThrottle(
            max_attempts=2, window_seconds=60, monotonic=lambda: now[0]
        )

        await throttle.ensure_allowed(key="k")
        await throttle.record_failure(key="k")
        await throttle.record_failure(key="k")
        with pytest.raises(TooManyAttemptsError) as exc:
            await throttle.ensure_allowed(key="k")
        assert exc.value.details["retry_after"] == 61
        assert exc.value.details["reason"] == "auth_too_many_attempts"

        now[0] = 61.0
        await throttle.ensure_allowed(key="k")

    async def test_les_cles_sont_independantes_et_reset_efface(self) -> None:
        throttle = InMemoryLoginThrottle(max_attempts=1)
        await throttle.record_failure(key="a")
        await throttle.ensure_allowed(key="b")
        with pytest.raises(TooManyAttemptsError):
            await throttle.ensure_allowed(key="a")
        await throttle.reset(key="a")
        await throttle.ensure_allowed(key="a")


class TestMailLoginCodeSender:
    async def test_passe_par_le_gabarit_login_code(self) -> None:
        mailer = ConsoleMailer()
        sender = MailLoginCodeSender(mailer)

        await sender.send(
            email="marc@exemple.fr", code="418293", expires_in_minutes=10, language=Language.FR
        )

        mail, text = mailer.sent[0]
        assert mail.message is MailMessage.LOGIN_CODE
        assert mail.to == "marc@exemple.fr"
        assert mail.language is Language.FR
        assert "418293" in text
        assert "10 minutes" in text

    async def test_traduit_un_echec_de_transport(self) -> None:
        class BrokenMailer:
            async def send(self, mail: OutgoingMail) -> None:
                raise MailDeliveryError("refusé")

            async def check_health(self) -> None:
                return

        sender = MailLoginCodeSender(BrokenMailer())
        with pytest.raises(DependencyUnavailableError) as exc:
            await sender.send(email="m@x.fr", code="1", expires_in_minutes=10, language=Language.EN)
        assert exc.value.details["dependency"] == "e-mail"


class FakeJsonClient(JsonClient):
    """Un client qui rend ce qu'on lui dit, et compte les appels."""

    calls: ClassVar[list[str]] = []
    document: ClassVar[dict[str, Any]] = {"keys": [{"kid": "a"}]}
    failing = False

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> JsonResponse:
        _ = params
        FakeJsonClient.calls.append(f"{self.base_url}{path}")
        if FakeJsonClient.failing:
            raise OutboundError("injoignable")
        return JsonResponse(200, FakeJsonClient.document)


class TestHttpJwksSource:
    def setup_method(self) -> None:
        FakeJsonClient.calls = []
        FakeJsonClient.failing = False
        FakeJsonClient.document = {"keys": [{"kid": "a"}]}

    async def test_met_en_cache_puis_relit_apres_le_ttl(self) -> None:
        now = [0.0]
        source = HttpJwksSource(
            ttl_seconds=3600,
            monotonic=lambda: now[0],
            client_factory=FakeJsonClient,
        )

        doc1 = await source.fetch("https://appleid.apple.com/auth/keys")
        doc2 = await source.fetch("https://appleid.apple.com/auth/keys")
        now[0] = 3601.0
        doc3 = await source.fetch("https://appleid.apple.com/auth/keys")

        assert doc1 == doc2 == doc3 == {"keys": [{"kid": "a"}]}
        assert FakeJsonClient.calls == ["https://appleid.apple.com/auth/keys"] * 2

    async def test_refresh_force_la_relecture(self) -> None:
        source = HttpJwksSource(client_factory=FakeJsonClient)
        await source.fetch("https://u")
        FakeJsonClient.document = {"keys": [{"kid": "b"}]}

        assert (await source.fetch("https://u"))["keys"][0]["kid"] == "a"
        assert (await source.refresh("https://u"))["keys"][0]["kid"] == "b"

    async def test_une_panne_devient_un_jeton_invalide(self) -> None:
        FakeJsonClient.failing = True
        source = HttpJwksSource(client_factory=FakeJsonClient)
        with pytest.raises(InvalidSocialTokenError):
            await source.fetch("https://u")

    async def test_le_client_par_defaut_vise_lurl_exacte(self) -> None:
        from karria.modules.identity.infrastructure.jwks import _default_client  # noqa: PLC0415

        client = _default_client("https://www.googleapis.com/oauth2/v3/certs?x=1")
        assert client.base_url == "https://www.googleapis.com/oauth2/v3/certs"
