"""Traduction des exceptions en réponses HTTP.

Contrat
-------
- **Seul endroit du projet** où une erreur devient un statut HTTP. Le reste
  du code lève des exceptions typées et ignore l'existence du protocole.
- **Aucune exception ne s'échappe.** Le handler générique garantit qu'un bug
  imprévu produit une réponse propre au format standard, jamais une page
  d'erreur Starlette ni une trace d'exécution.
- **Rien ne fuite.** Une exception non maîtrisée est journalisée
  intégralement côté serveur et renvoyée au client sous forme d'un message
  générique accompagné du `correlation_id`. Le client obtient de quoi
  signaler l'incident, un attaquant n'obtient rien.
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from karria.shared.errors.exceptions import ErrorCode, KarriaError
from karria.shared.errors.schemas import ErrorDetail, ErrorResponse
from karria.shared.logging import get_correlation_id, get_logger

logger = get_logger(__name__)

#: Table de correspondance unique entre intention métier et statut HTTP.
#: Ajouter un `ErrorCode` sans l'ajouter ici est détecté par les tests.
STATUS_BY_ERROR_CODE: Final[dict[ErrorCode, int]] = {
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.ALREADY_EXISTS: status.HTTP_409_CONFLICT,
    ErrorCode.INVALID_INPUT: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.RULE_VIOLATION: status.HTTP_409_CONFLICT,
    ErrorCode.UNAUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    ErrorCode.GONE: status.HTTP_410_GONE,
    ErrorCode.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.DEPENDENCY_FAILURE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.INTERNAL: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

_GENERIC_INTERNAL_MESSAGE: Final = (
    "Une erreur interne est survenue. Communiquez l'identifiant de corrélation au support."
)


def _build_response(
    *, http_status: int, code: ErrorCode, message: str, details: dict[str, object]
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details),
        correlation_id=get_correlation_id(),
    )
    return JSONResponse(status_code=http_status, content=payload.model_dump(mode="json"))


async def _handle_karria_error(_request: Request, exc: Exception) -> Response:
    """Erreur maîtrisée : on fait confiance à son code et à son message."""
    assert isinstance(exc, KarriaError)  # noqa: S101 — garanti par l'enregistrement
    http_status = STATUS_BY_ERROR_CODE.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Une erreur métier (4xx) est un fonctionnement nominal, pas un incident :
    # la journaliser en ERROR noierait les vraies pannes sous le bruit.
    log = logger.warning if http_status < status.HTTP_500_INTERNAL_SERVER_ERROR else logger.error
    log("request.failed", error_code=exc.code.value, http_status=http_status, detail=exc.details)

    return _build_response(
        http_status=http_status, code=exc.code, message=exc.message, details=exc.details
    )


async def _handle_request_validation_error(_request: Request, exc: Exception) -> Response:
    """Validation Pydantic : ramenée au format d'erreur standard."""
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    fields = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
            "reason": error["msg"],
        }
        for error in exc.errors()
    ]
    logger.warning("request.invalid", fields=fields)
    # `field` et `reason` à plat, en plus de la liste : c'est la forme que
    # l'app lit pour afficher l'erreur **sous le champ concerné**
    # (`details.field`), comme pour une erreur métier. Le premier champ
    # fautif suffit — l'app n'en affiche qu'un à la fois.
    details: dict[str, object] = {"fields": fields, "reason": "validation_error"}
    if fields:
        details["field"] = fields[0]["field"]
    return _build_response(
        http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code=ErrorCode.INVALID_INPUT,
        message="La requête est invalide.",
        details=details,
    )


async def _handle_http_exception(_request: Request, exc: Exception) -> Response:
    """`HTTPException` levée par Starlette (404 de routage, 405, etc.)."""
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = (
        ErrorCode.NOT_FOUND
        if exc.status_code == status.HTTP_404_NOT_FOUND
        else ErrorCode.INTERNAL
        if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR
        else ErrorCode.INVALID_INPUT
    )
    return _build_response(
        http_status=exc.status_code, code=code, message=str(exc.detail), details={}
    )


async def _handle_unexpected_error(_request: Request, exc: Exception) -> Response:
    """Filet de sécurité : tout ce qui n'a pas été prévu.

    La trace complète part dans les logs, le client ne reçoit qu'un message
    générique et le `correlation_id` qui relie les deux.
    """
    logger.exception("request.unhandled_error", error_type=type(exc).__name__)
    return _build_response(
        http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=ErrorCode.INTERNAL,
        message=_GENERIC_INTERNAL_MESSAGE,
        details={},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Branche tous les gestionnaires d'erreurs sur l'application."""
    app.add_exception_handler(KarriaError, _handle_karria_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)
