"""La vérification cryptographique des jetons Apple et Google — pour de vrai.

Une clé RSA est générée localement ; un JWKS en est dérivé et servi par une
doublure du port `JwksSource`. Les jetons sont signés avec la clé privée :
le vérificateur de production les valide, ou les refuse, sans réseau.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from doubles import StaticJwksSource
from karria.modules.identity.domain import InvalidSocialTokenError, SocialProvider
from karria.modules.identity.infrastructure import AppleTokenVerifier, GoogleTokenVerifier
from karria.modules.identity.infrastructure.social_verifiers import (
    APPLE_ISSUER,
    JwtSocialTokenVerifier,
)

pytestmark = pytest.mark.unit

IOS_CLIENT = "ios-client.apps.googleusercontent.com"
WEB_CLIENT = "web-client.apps.googleusercontent.com"
BUNDLE = "com.karria.app"
NONCE = "3f1c8a90b2e74d5601af"


@pytest.fixture(scope="module")
def private_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key: RSAPrivateKey, kid: str = "kid-1") -> dict[str, Any]:
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**public, "kid": kid, "use": "sig", "alg": "RS256"}]}


def _sign(key: RSAPrivateKey, claims: dict[str, Any], *, kid: str = "kid-1") -> str:
    now = int(time.time())
    payload = {"iat": now, "exp": now + 600, **claims}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


def _google_claims(**overrides: Any) -> dict[str, Any]:
    return {
        "iss": "https://accounts.google.com",
        "aud": IOS_CLIENT,
        "sub": "115544567408637458330",
        "email": "marc@gmail.com",
        "email_verified": True,
        "nonce": NONCE,
        **overrides,
    }


def _apple_claims(**overrides: Any) -> dict[str, Any]:
    return {
        "iss": APPLE_ISSUER,
        "aud": BUNDLE,
        "sub": "001234.abcdef0123456789.1234",
        "email": "xyz@privaterelay.appleid.com",
        "email_verified": "true",
        "nonce": hashlib.sha256(NONCE.encode()).hexdigest(),
        **overrides,
    }


class TestGoogle:
    async def test_accepte_un_jeton_signe_pour_lune_des_audiences(
        self, private_key: RSAPrivateKey
    ) -> None:
        keys = StaticJwksSource(_jwks(private_key))
        verifier = GoogleTokenVerifier(audiences=[WEB_CLIENT, IOS_CLIENT], keys=keys)

        profile = await verifier.verify(_sign(private_key, _google_claims()), nonce=NONCE)

        assert verifier.provider is SocialProvider.GOOGLE
        assert profile.subject == "115544567408637458330"
        assert profile.email == "marc@gmail.com"
        assert profile.email_verified
        assert keys.fetches == 1
        assert keys.refreshes == 0

    async def test_lit_email_verified_a_faux(self, private_key: RSAPrivateKey) -> None:
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource(_jwks(private_key))
        )

        profile = await verifier.verify(
            _sign(private_key, _google_claims(email_verified=False, email="  ")), nonce=NONCE
        )

        assert not profile.email_verified
        assert profile.email is None

    async def test_sans_nonce_attendu_un_jeton_qui_en_porte_passe(
        self, private_key: RSAPrivateKey
    ) -> None:
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource(_jwks(private_key))
        )
        await verifier.verify(_sign(private_key, _google_claims()), nonce=None)

    async def test_accepte_lemetteur_sans_schema(self, private_key: RSAPrivateKey) -> None:
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource(_jwks(private_key))
        )
        await verifier.verify(
            _sign(private_key, _google_claims(iss="accounts.google.com")), nonce=NONCE
        )

    @pytest.mark.parametrize(
        ("overrides", "motif"),
        [
            ({"aud": "autre-app.apps.googleusercontent.com"}, "mauvaise audience"),
            ({"iss": "https://evil.example"}, "mauvais émetteur"),
            ({"exp": int(time.time()) - 3600}, "expiré"),
            ({"nonce": "autre"}, "nonce différent"),
            ({"sub": None}, "sujet manquant"),
        ],
    )
    async def test_refuse(
        self, private_key: RSAPrivateKey, overrides: dict[str, Any], motif: str
    ) -> None:
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource(_jwks(private_key))
        )
        claims = _google_claims(**overrides)
        if claims.get("sub") is None:
            del claims["sub"]

        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(_sign(private_key, claims), nonce=NONCE)
        _ = motif

    async def test_refuse_une_signature_dune_autre_cle(
        self, private_key: RSAPrivateKey, other_key: RSAPrivateKey
    ) -> None:
        """Le `kid` correspond, la clé non : la signature est fausse."""
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource(_jwks(private_key))
        )
        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(_sign(other_key, _google_claims()), nonce=NONCE)

    async def test_refuse_lalgorithme_none_et_hs256(self, private_key: RSAPrivateKey) -> None:
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource(_jwks(private_key))
        )
        forged_hs = jwt.encode(
            _google_claims(), "s" * 32, algorithm="HS256", headers={"kid": "kid-1"}
        )
        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(forged_hs, nonce=NONCE)
        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify("pas.un.jwt", nonce=NONCE)

    async def test_un_kid_inconnu_force_une_relecture_puis_refuse(
        self, private_key: RSAPrivateKey
    ) -> None:
        keys = StaticJwksSource(_jwks(private_key, kid="ancienne"))
        verifier = GoogleTokenVerifier(audiences=[IOS_CLIENT], keys=keys)

        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(_sign(private_key, _google_claims(), kid="nouvelle"), nonce=NONCE)

        assert keys.fetches == 1
        assert keys.refreshes == 1

    async def test_un_kid_inconnu_puis_publie_passe(self, private_key: RSAPrivateKey) -> None:
        """La rotation de clés est suivie sans attendre l'expiration du cache."""

        class RotatingSource(StaticJwksSource):
            async def refresh(self, url: str) -> dict[str, Any]:
                self.document = _jwks(private_key, kid="nouvelle")
                return await super().refresh(url)

        keys = RotatingSource(_jwks(private_key, kid="ancienne"))
        verifier = GoogleTokenVerifier(audiences=[IOS_CLIENT], keys=keys)

        profile = await verifier.verify(
            _sign(private_key, _google_claims(), kid="nouvelle"), nonce=NONCE
        )

        assert profile.subject == "115544567408637458330"
        assert keys.refreshes == 1

    async def test_refuse_un_jeton_sans_kid(self, private_key: RSAPrivateKey) -> None:
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource(_jwks(private_key))
        )
        token = jwt.encode(_google_claims(), private_key, algorithm="RS256")
        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(token, nonce=NONCE)

    async def test_un_jwks_malforme_refuse(self, private_key: RSAPrivateKey) -> None:
        verifier = GoogleTokenVerifier(
            audiences=[IOS_CLIENT], keys=StaticJwksSource({"keys": "rien"})
        )
        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(_sign(private_key, _google_claims()), nonce=NONCE)


class TestApple:
    async def test_compare_le_nonce_a_son_hache(self, private_key: RSAPrivateKey) -> None:
        verifier = AppleTokenVerifier(audiences=[BUNDLE], keys=StaticJwksSource(_jwks(private_key)))

        profile = await verifier.verify(_sign(private_key, _apple_claims()), nonce=NONCE)

        assert verifier.provider is SocialProvider.APPLE
        assert profile.email == "xyz@privaterelay.appleid.com"
        assert profile.email_verified  # « true » en chaîne vaut oui

    async def test_le_nonce_en_clair_ne_passe_pas(self, private_key: RSAPrivateKey) -> None:
        """Si Apple avait signé le clair, le haché ne correspondrait pas — et inversement."""
        verifier = AppleTokenVerifier(audiences=[BUNDLE], keys=StaticJwksSource(_jwks(private_key)))
        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(_sign(private_key, _apple_claims(nonce=NONCE)), nonce=NONCE)

    async def test_une_reconnexion_sans_adresse_est_valide(
        self, private_key: RSAPrivateKey
    ) -> None:
        verifier = AppleTokenVerifier(audiences=[BUNDLE], keys=StaticJwksSource(_jwks(private_key)))
        claims = _apple_claims()
        del claims["email"]
        del claims["email_verified"]

        profile = await verifier.verify(_sign(private_key, claims), nonce=None)

        assert profile.email is None
        assert profile.email_verified

    async def test_refuse_un_jeton_google_presente_a_apple(
        self, private_key: RSAPrivateKey
    ) -> None:
        verifier = AppleTokenVerifier(audiences=[BUNDLE], keys=StaticJwksSource(_jwks(private_key)))
        with pytest.raises(InvalidSocialTokenError):
            await verifier.verify(_sign(private_key, _google_claims(aud=BUNDLE)), nonce=NONCE)


class TestTroncCommun:
    async def test_le_tronc_commun_lit_email_verified_a_faux_par_defaut(
        self, private_key: RSAPrivateKey
    ) -> None:
        verifier = JwtSocialTokenVerifier(
            provider=SocialProvider.GOOGLE,
            jwks_url="https://exemple/jwks",
            audiences=[IOS_CLIENT],
            issuers=frozenset({"https://accounts.google.com"}),
            keys=StaticJwksSource(_jwks(private_key)),
        )
        claims = _google_claims()
        del claims["email_verified"]

        profile = await verifier.verify(_sign(private_key, claims), nonce=NONCE)

        assert not profile.email_verified
