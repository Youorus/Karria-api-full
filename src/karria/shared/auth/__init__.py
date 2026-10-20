"""API publique de l'authentification et de l'autorisation transverses."""

from karria.shared.auth.dependencies import (
    CurrentPrincipalDep,
    OptionalPrincipalDep,
    PrincipalResolver,
    require_authentication,
    require_permissions,
    resolve_principal,
)
from karria.shared.auth.principal import Principal

__all__ = [
    "CurrentPrincipalDep",
    "OptionalPrincipalDep",
    "Principal",
    "PrincipalResolver",
    "require_authentication",
    "require_permissions",
    "resolve_principal",
]
