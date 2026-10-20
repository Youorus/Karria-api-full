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

Voir l'ADR-0007 pour la justification complète.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Protocol

from fastapi import Depends, Request

from karria.shared.auth.principal import Principal
from karria.shared.errors import AuthenticationError, AuthorizationError
from karria.shared.logging import get_logger

logger = get_logger(__name__)


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
