"""API publique de la gestion d'erreurs."""

from karria.shared.errors.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusinessRuleViolationError,
    DependencyUnavailableError,
    DomainError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ErrorCode,
    InfrastructureError,
    InvalidInputError,
    KarriaError,
    RateLimitedError,
    ResourceGoneError,
)
from karria.shared.errors.handlers import STATUS_BY_ERROR_CODE, register_exception_handlers
from karria.shared.errors.openapi import error_responses
from karria.shared.errors.schemas import ErrorDetail, ErrorResponse

__all__ = [
    "STATUS_BY_ERROR_CODE",
    "AuthenticationError",
    "AuthorizationError",
    "BusinessRuleViolationError",
    "DependencyUnavailableError",
    "DomainError",
    "EntityAlreadyExistsError",
    "EntityNotFoundError",
    "ErrorCode",
    "ErrorDetail",
    "ErrorResponse",
    "InfrastructureError",
    "InvalidInputError",
    "KarriaError",
    "RateLimitedError",
    "ResourceGoneError",
    "error_responses",
    "register_exception_handlers",
]
