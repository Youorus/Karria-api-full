"""API publique de l'authentification et de l'autorisation transverses."""

from karria.shared.auth.dependencies import (
    AI_CONSENT_REQUIRED,
    ANONYMOUS_RATE_LIMITED,
    AiConsentedPrincipalDep,
    AiConsentGate,
    AiConsentGateDep,
    CurrentPrincipalDep,
    OptionalPrincipalDep,
    PrincipalResolver,
    RefusingAiConsentGate,
    allow_anonymous,
    get_ai_consent_gate,
    require_ai_consent,
    require_authentication,
    require_permissions,
    resolve_principal,
)
from karria.shared.auth.principal import Principal

__all__ = [
    "AI_CONSENT_REQUIRED",
    "ANONYMOUS_RATE_LIMITED",
    "AiConsentGate",
    "AiConsentGateDep",
    "AiConsentedPrincipalDep",
    "CurrentPrincipalDep",
    "OptionalPrincipalDep",
    "Principal",
    "PrincipalResolver",
    "RefusingAiConsentGate",
    "allow_anonymous",
    "get_ai_consent_gate",
    "require_ai_consent",
    "require_authentication",
    "require_permissions",
    "resolve_principal",
]
