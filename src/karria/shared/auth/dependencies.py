"""Dépendances FastAPI d'authentification et d'autorisation.

Contrat
-------
- **Une seule couture.** `resolve_principal` est le point de substitution
  unique : y brancher un émetteur de jetons authentifie toute l'application
  d'un coup, et le remplacer dans un test authentifie toute la suite de la
  même manière. Aucune route ne lit d'en-tête elle-même.
- **Refus par défaut.** Tant qu'aucun mécanisme d'authentification n'est
  livré, l'implémentation par défaut ne reconnaît personne et toute route
  protégée répond `401`. Il n'existe volontairement aucun mode de
  développement lisant un en-tête `X-User-Id` : une porte dérobée de
  commodité survit toujours jusqu'en production.
- **Le refus ne renseigne pas.** Le client reçoit « Accès refusé. » sans
  savoir quelle permission lui manque ; le détail part dans les journaux,
  où il sert au support et à personne d'autre.
- **Une porte d'IA, refusée par défaut.** `get_ai_consent_gate` est la
  seconde couture du socle : une route qui confie des données d'une
  personne à un modèle tiers dépend d'`AiConsentedPrincipalDep`, et c'est
  `bootstrap/wiring.py` qui branche le registre des consentements. Non
  branchée, la porte refuse (403 `ai_consent_required`) : un oubli se voit,
  il ne laisse pas passer.
- **L'accès anonyme est une décision de route, et il est compté.**
  `allow_anonymous` admet un appelant sans session sous un limiteur par
  origine dont le quota appartient au module qui ouvre la route.

Voir l'ADR-0007 pour la justification complète, l'ADR-0025 pour la porte
d'IA et l'accès anonyme.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Final, Protocol

from fastapi import Depends, Request

from karria.shared.auth.principal import Principal
from karria.shared.errors import AuthenticationError, AuthorizationError, RateLimitedError
from karria.shared.http.ratelimit import SlidingWindowLimiter, client_ip
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: La raison fine que l'app branche quand le consentement à l'IA manque.
AI_CONSENT_REQUIRED: Final = "ai_consent_required"
#: La raison fine d'un appel anonyme refusé pour excès.
ANONYMOUS_RATE_LIMITED: Final = "anonymous_rate_limited"
#: La raison fine d'un jeton présenté mais refusé sur une route qui admet l'anonyme.
SESSION_EXPIRED: Final = "session_expired"


class PrincipalResolver(Protocol):
    """Port : reconnaît l'appelant d'une requête entrante.

    Retourne `None` pour un appelant anonyme — ce qui n'est pas une erreur :
    certaines routes sont publiques, et c'est à elles d'en décider.
    """

    async def resolve(self, request: Request) -> Principal | None:
        """Reconnaît l'appelant, ou `None` s'il est anonyme."""
        ...


async def resolve_principal(request: Request) -> Principal | None:
    """Reconnaît l'appelant de la requête courante.

    Implémentation par défaut : personne n'est reconnu. Elle sera remplacée
    par surcharge de cette dépendance — dans `bootstrap/wiring.py` — le
    jour où un émetteur de jetons existera ; aucune route n'aura à changer.
    """
    _ = request
    return None


OptionalPrincipalDep = Annotated[Principal | None, Depends(resolve_principal)]


async def require_authentication(principal: OptionalPrincipalDep) -> Principal:
    """Exige un appelant authentifié, sinon lève `AuthenticationError`."""
    if principal is None:
        raise AuthenticationError("Authentification requise.")
    return principal


CurrentPrincipalDep = Annotated[Principal, Depends(require_authentication)]


def require_permissions(*required: str) -> Callable[[Principal], Principal]:
    """Construit une dépendance exigeant toutes les permissions indiquées.

    À utiliser sous forme d'alias, déclaré une fois par le module qui
    possède la ressource :

        AdminReadDep = Annotated[Principal, Depends(require_permissions("users:read"))]

    Args:
        *required: Permissions au format `<ressource>:<action>`.

    Returns:
        Une dépendance qui retourne le `Principal` ou lève
        `AuthorizationError`.
    """
    # Dédoublonné en conservant l'ordre : le journal de refus reste stable.
    expected = tuple(dict.fromkeys(required))

    def guard(principal: CurrentPrincipalDep) -> Principal:
        missing = principal.missing_permissions(expected)
        if missing:
            logger.warning(
                "auth.permission_denied",
                subject_id=str(principal.subject_id),
                required=list(expected),
                missing=list(missing),
            )
            raise AuthorizationError("Accès refusé.")
        return principal

    return guard


# ---------------------------------------------------------------------
# Le jeton nu — pour les connexions qui n'ont pas de `Request`
# ---------------------------------------------------------------------


class TokenResolver(Protocol):
    """Port : reconnaît l'appelant à partir de son seul jeton d'accès.

    La couture `resolve_principal` lit l'en-tête d'une `Request` ; une
    connexion WebSocket n'en a pas, et FastAPI ne lui injecte pas de
    `Request`. Le même module d'identité répond aux deux : c'est
    `bootstrap/wiring.py` qui branche ce port, comme l'autre.
    """

    async def resolve(self, token: str) -> Principal | None:
        """Reconnaît le porteur du jeton, ou `None` s'il n'est pas valide."""
        ...


class RefusingTokenResolver:
    """Le défaut : aucun jeton n'est reconnu, parce que rien n'a été branché."""

    async def resolve(self, token: str) -> Principal | None:
        """Refuse, et le dit : un branchement oublié ne doit pas servir en silence."""
        _ = token
        logger.error("auth.token_resolver_unwired")
        return None


def get_token_resolver() -> TokenResolver:
    """La résolution d'un jeton nu. `bootstrap/wiring.py` la remplace par celle d'`identity`."""
    return RefusingTokenResolver()


TokenResolverDep = Annotated[TokenResolver, Depends(get_token_resolver)]


def bearer_token(authorization: str | None) -> str | None:
    """Extrait le jeton d'un en-tête `Authorization: Bearer …`, ou `None`."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


# ---------------------------------------------------------------------
# L'accès anonyme, compté par origine
# ---------------------------------------------------------------------


def allow_anonymous(
    limiter: SlidingWindowLimiter, *, scope: str
) -> Callable[[Request, Principal | None], Awaitable[Principal | None]]:
    """Construit une dépendance qui admet un appelant sans session, sous quota.

    « Sans session » veut dire **sans jeton** : un jeton présenté et refusé
    (expiré, révoqué) répond `401 session_expired`, pour que l'app le
    renouvelle et rejoue la requête au lieu de recevoir la version anonyme.

    Une personne connectée passe sans être comptée : son identité est déjà
    une limite. Un appelant anonyme est compté par origine (`client_ip`),
    dans le périmètre `scope` — deux modules ne partagent pas leurs quotas.

    Args:
        limiter: Le compteur, construit par le module avec **son** quota.
        scope: Le préfixe de clé, pour que les routes d'un module ne
            consomment pas le quota d'un autre.
    """

    async def guard(request: Request, principal: OptionalPrincipalDep) -> Principal | None:
        if principal is not None:
            return principal
        if bearer_token(request.headers.get("authorization")) is not None:
            # Un jeton présenté mais refusé (le plus souvent : expiré, il vit
            # 15 min) n'est **pas** un visiteur. Le servir en anonyme rendait
            # le corpus entier à une personne en pleine recherche, sans 401
            # pour que l'app renouvelle sa session — constaté le 2026-10-31 :
            # « je filtre, et il filtre sur toutes les offres ».
            raise AuthenticationError("Ta session a expiré.", details={"reason": SESSION_EXPIRED})
        retry_after = limiter.hit(f"{scope}|{client_ip(request)}")
        if retry_after is not None:
            logger.warning("auth.anonymous_throttled", scope=scope, retry_after=retry_after)
            raise RateLimitedError(
                "Trop de demandes. Réessaie dans un instant.",
                retry_after=retry_after,
                details={"reason": ANONYMOUS_RATE_LIMITED},
            )
        return None

    return guard


# ---------------------------------------------------------------------
# La porte de l'IA
# ---------------------------------------------------------------------


class AiConsentGate(Protocol):
    """Port : vérifie que la personne a accepté la remise de ses données à un modèle tiers.

    `shared/` ne sait pas ce qu'est un consentement : il sait seulement
    qu'une route d'IA doit demander la permission avant de servir.
    """

    async def ensure(self, principal: Principal) -> None:
        """Ne rend rien si le consentement vaut ; lève sinon (403 `ai_consent_required`)."""
        ...


class RefusingAiConsentGate:
    """Le défaut : personne n'a consenti, parce que personne n'a été branché."""

    async def ensure(self, principal: Principal) -> None:
        """Refuse, bruyamment : un branchement oublié ne doit pas servir en silence."""
        logger.error("auth.ai_consent_gate_unwired", subject_id=str(principal.subject_id))
        raise AuthorizationError(
            "Le consentement à l'IA est requis.", details={"reason": AI_CONSENT_REQUIRED}
        )


def get_ai_consent_gate() -> AiConsentGate:
    """La porte de l'IA. `bootstrap/wiring.py` la remplace par celle d'`identity`."""
    return RefusingAiConsentGate()


AiConsentGateDep = Annotated[AiConsentGate, Depends(get_ai_consent_gate)]


async def require_ai_consent(principal: CurrentPrincipalDep, gate: AiConsentGateDep) -> Principal:
    """Exige une session **et** un consentement à l'IA en cours de validité."""
    await gate.ensure(principal)
    return principal


AiConsentedPrincipalDep = Annotated[Principal, Depends(require_ai_consent)]
