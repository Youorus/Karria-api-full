"""La vérification cryptographique des jetons Apple et Google.

Ce que ce fichier garantit
--------------------------
Qu'un `VerifiedSocialProfile` n'existe que si le fournisseur a bien signé
ce qu'il contient. C'est le seul endroit du système qui transforme une
affirmation du client en preuve — tout le reste en dépend (ADR-0011).

Les cinq contrôles, et ce que coûte chacun s'il manque
------------------------------------------------------
- **Signature** — sans elle, n'importe qui forge un jeton.
- **Émetteur** (`iss`) — un jeton signé par un autre service passerait.
- **Audience** (`aud`) — un jeton émis pour une autre application est
  valide et signé ; l'accepter laisse cette application usurper nos
  comptes. C'est le contrôle qu'on oublie le plus souvent.
- **Expiration** (`exp`) — un jeton intercepté resterait utilisable.
- **Nonce** — un jeton valide rejoué plus tard ouvrirait une session.

PyJWT vérifie les quatre premiers ; le nonce est à nous, parce que lui seul
sait quelle valeur l'application avait émise.

Les clés publiques viennent du port `JwksSource` : en production, le
téléchargement mis en cache ; en test, un JWKS construit d'une clé RSA
locale — ce qui permet de tester la **vraie** vérification de signature.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Final

import jwt

from karria.modules.identity.application.ports import JwksSource
from karria.modules.identity.domain import (
    InvalidSocialTokenError,
    SocialProvider,
    VerifiedSocialProfile,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)

APPLE_ISSUER: Final = "https://appleid.apple.com"
APPLE_JWKS_URL: Final = "https://appleid.apple.com/auth/keys"

GOOGLE_ISSUERS: Final = frozenset({"https://accounts.google.com", "accounts.google.com"})
GOOGLE_JWKS_URL: Final = "https://www.googleapis.com/oauth2/v3/certs"

#: Tolérance d'horloge, en secondes. Les serveurs dérivent de quelques
#: secondes ; refuser un jeton émis « dans le futur » de deux secondes
#: ferait échouer des connexions légitimes.
LEEWAY_SECONDS: Final = 30

#: Le seul algorithme que les deux fournisseurs emploient. Une liste
#: fermée : accepter `none` ou HS256 est la faille classique des JWT.
_ALGORITHMS: Final = ["RS256"]


class JwtSocialTokenVerifier:
    """Le tronc commun des deux fournisseurs : JWT RS256 contre un JWKS.

    Écrire deux fois la séquence, c'est deux occasions d'oublier
    `verify_aud` — et cet oubli ne se voit pas : tout fonctionne, et
    l'audience n'est plus vérifiée.
    """

    def __init__(
        self,
        *,
        provider: SocialProvider,
        jwks_url: str,
        audiences: list[str],
        issuers: frozenset[str],
        keys: JwksSource,
    ) -> None:
        self._provider = provider
        self._jwks_url = jwks_url
        self._audiences = audiences
        self._issuers = issuers
        self._keys = keys

    @property
    def provider(self) -> SocialProvider:
        """Le fournisseur dont ce vérificateur accepte les jetons."""
        return self._provider

    async def verify(self, token: str, *, nonce: str | None) -> VerifiedSocialProfile:
        """Vérifie et rend ce que le fournisseur affirme."""
        claims = await self._decode(token)
        self._check_nonce(claims, self._expected_nonce(nonce))
        return self._profile(claims)

    # Points de variation par fournisseur -------------------------------

    def _expected_nonce(self, nonce: str | None) -> str | None:
        """La valeur que le fournisseur a signée, à partir de celle de l'app."""
        return nonce

    def _profile(self, claims: dict[str, Any]) -> VerifiedSocialProfile:
        return VerifiedSocialProfile(
            provider=self._provider,
            subject=str(claims["sub"]),
            email=_clean(claims.get("email")),
            email_verified=_truthy(claims.get("email_verified", False)),
        )

    # Tronc commun --------------------------------------------------------

    async def _decode(self, token: str) -> dict[str, Any]:
        """Vérifie signature, émetteur, audience, expiration. Lève sinon."""
        try:
            key = await self._signing_key(token)
            # PyJWT est synchrone et la vérification RSA coûte quelques
            # millisecondes : sans ce fil, elle retiendrait la boucle.
            claims = await asyncio.to_thread(self._decode_blocking, token, key)
        except InvalidSocialTokenError:
            raise
        except Exception as exc:
            # Le détail reste ici, jamais dans la réponse : dire *lequel*
            # des contrôles a échoué renseignerait qui forge un jeton.
            logger.info(
                "social.token_rejected",
                provider=self._provider.value,
                reason=type(exc).__name__,
                detail=str(exc)[:120],
            )
            raise InvalidSocialTokenError from exc
        if claims.get("iss") not in self._issuers:
            logger.info("social.token_rejected", provider=self._provider.value, reason="issuer")
            raise InvalidSocialTokenError
        return claims

    async def _signing_key(self, token: str) -> Any:
        """Retrouve la clé publique désignée par le `kid` de l'en-tête.

        Un `kid` absent du document en cache déclenche **une** relecture :
        c'est ainsi qu'une rotation de clés chez le fournisseur est suivie
        sans attendre l'expiration du cache.
        """
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise InvalidSocialTokenError
        document = await self._keys.fetch(self._jwks_url)
        entry = _find_key(document, kid)
        if entry is None:
            document = await self._keys.refresh(self._jwks_url)
            entry = _find_key(document, kid)
        if entry is None:
            logger.info(
                "social.token_rejected", provider=self._provider.value, reason="unknown_kid"
            )
            raise InvalidSocialTokenError
        return jwt.PyJWK(entry, algorithm="RS256").key

    def _decode_blocking(self, token: str, key: Any) -> dict[str, Any]:
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=_ALGORITHMS,
            audience=self._audiences,
            leeway=LEEWAY_SECONDS,
            options={
                "require": ["exp", "iat", "sub", "iss", "aud"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
            },
        )
        return claims

    def _check_nonce(self, claims: dict[str, Any], expected: str | None) -> None:
        """Le contrôle que PyJWT ne peut pas faire à notre place.

        Quand aucun nonce n'est attendu, un jeton qui en porte un est
        accepté : c'est le cas d'un flux qui n'en a pas émis.
        """
        if expected is None:
            return
        if claims.get("nonce") != expected:
            logger.info("social.token_rejected", provider=self._provider.value, reason="nonce")
            raise InvalidSocialTokenError


class AppleTokenVerifier(JwtSocialTokenVerifier):
    """Vérifie un `identityToken` d'Apple.

    L'audience est le *bundle identifier* de l'application iOS.

    **Le nonce d'Apple est haché** : `expo-apple-authentication` soumet le
    SHA-256 du nonce à Apple, qui le signe tel quel, et l'app nous transmet
    la valeur en clair. On compare donc au haché — comparer au clair
    refuserait *toutes* les connexions Apple, sans qu'aucune trace ne
    distingue ce cas d'un vrai rejeu.

    Apple ne signe que des adresses qu'il a lui-même vérifiées, relais
    `@privaterelay.appleid.com` compris : `email_verified` est tenu pour
    vrai s'il manque. Et Apple n'envoie l'adresse qu'à la **première**
    connexion : les suivantes rendent `email=None`, et c'est le `sub` qui
    reconnaît la personne.
    """

    def __init__(self, *, audiences: list[str], keys: JwksSource) -> None:
        super().__init__(
            provider=SocialProvider.APPLE,
            jwks_url=APPLE_JWKS_URL,
            audiences=audiences,
            issuers=frozenset({APPLE_ISSUER}),
            keys=keys,
        )

    def _expected_nonce(self, nonce: str | None) -> str | None:
        return None if nonce is None else hashlib.sha256(nonce.encode()).hexdigest()

    def _profile(self, claims: dict[str, Any]) -> VerifiedSocialProfile:
        return VerifiedSocialProfile(
            provider=SocialProvider.APPLE,
            subject=str(claims["sub"]),
            email=_clean(claims.get("email")),
            email_verified=_truthy(claims.get("email_verified", True)),
        )


class GoogleTokenVerifier(JwtSocialTokenVerifier):
    """Vérifie un `idToken` de Google.

    Google émet un client OAuth **par plateforme** ; le jeton porte celui
    qui l'a émis, d'où la liste d'audiences. Google dit explicitement si
    l'adresse est vérifiée, et il arrive qu'elle ne le soit pas : ne pas le
    lire transformerait le rattachement par adresse en porte d'entrée.
    """

    def __init__(self, *, audiences: list[str], keys: JwksSource) -> None:
        super().__init__(
            provider=SocialProvider.GOOGLE,
            jwks_url=GOOGLE_JWKS_URL,
            audiences=audiences,
            issuers=GOOGLE_ISSUERS,
            keys=keys,
        )


def _find_key(document: dict[str, Any], kid: str) -> dict[str, Any] | None:
    keys = document.get("keys")
    if not isinstance(keys, list):
        return None
    for entry in keys:
        if isinstance(entry, dict) and entry.get("kid") == kid:
            return entry
    return None


def _truthy(value: object) -> bool:
    """Lit un booléen qui arrive parfois en chaîne (`"true"` chez Apple)."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _clean(value: object) -> str | None:
    """Une chaîne vide vaut absence."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
