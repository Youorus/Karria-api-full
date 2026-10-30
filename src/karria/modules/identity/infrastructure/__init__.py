"""Adaptateurs du module `identity` : Tortoise, SHA-256, SMTP, JWKS."""

from karria.modules.identity.infrastructure.devices import UnknownMainDeviceLookup
from karria.modules.identity.infrastructure.jwks import HttpJwksSource
from karria.modules.identity.infrastructure.mail import MailLoginCodeSender
from karria.modules.identity.infrastructure.providers import SettingsAiProviderCatalog
from karria.modules.identity.infrastructure.repositories import (
    TortoiseAiConsentRepository,
    TortoiseDepartureFeedbackRepository,
    TortoiseLoginChallengeRepository,
    TortoiseProviderIdentityRepository,
    TortoiseSessionRepository,
    TortoiseUserRepository,
)
from karria.modules.identity.infrastructure.social_verifiers import (
    AppleTokenVerifier,
    GoogleTokenVerifier,
    JwtSocialTokenVerifier,
)
from karria.modules.identity.infrastructure.throttle import InMemoryLoginThrottle
from karria.modules.identity.infrastructure.tokens import Sha256TokenFactory

__all__ = [
    "AppleTokenVerifier",
    "GoogleTokenVerifier",
    "HttpJwksSource",
    "InMemoryLoginThrottle",
    "JwtSocialTokenVerifier",
    "MailLoginCodeSender",
    "SettingsAiProviderCatalog",
    "Sha256TokenFactory",
    "TortoiseAiConsentRepository",
    "TortoiseDepartureFeedbackRepository",
    "TortoiseLoginChallengeRepository",
    "TortoiseProviderIdentityRepository",
    "TortoiseSessionRepository",
    "TortoiseUserRepository",
    "UnknownMainDeviceLookup",
]
