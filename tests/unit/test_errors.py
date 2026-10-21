"""Le contrat d'erreur doit rester complet et sans fuite."""

from __future__ import annotations

import pytest

from karria.shared.errors import (
    STATUS_BY_ERROR_CODE,
    AuthorizationError,
    EntityNotFoundError,
    ErrorCode,
    error_responses,
)

pytestmark = pytest.mark.unit


def test_chaque_code_derreur_a_un_statut_http() -> None:
    """Ajouter un `ErrorCode` sans le mapper doit casser la suite ici.

    C'est le filet qui évite qu'une nouvelle erreur métier remonte
    silencieusement en 500 le jour de sa première occurrence.
    """
    non_mappes = set(ErrorCode) - set(STATUS_BY_ERROR_CODE)
    assert not non_mappes, f"Codes sans statut HTTP : {non_mappes}"


def test_lerreur_de_ressource_porte_un_contexte_exploitable() -> None:
    error = EntityNotFoundError("Commande", "abc-123")

    assert error.code is ErrorCode.NOT_FOUND
    assert error.details == {"entity": "Commande", "identifier": "abc-123"}


def test_lerreur_dautorisation_ne_revele_rien() -> None:
    """Le message ne doit pas renseigner un attaquant sur l'existence d'une ressource."""
    error = AuthorizationError("Accès refusé.")

    assert error.code is ErrorCode.FORBIDDEN
    assert error.details == {}


def test_les_reponses_openapi_sont_composables() -> None:
    responses = error_responses(ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN)

    assert set(responses) == {404, 403}


def test_lerreur_de_rythme_porte_toujours_le_delai() -> None:
    """Un client qui ne sait pas quand réessayer réessaie tout de suite."""
    from karria.shared.errors import RateLimitedError, ResourceGoneError  # noqa: PLC0415

    error = RateLimitedError("Trop vite.", retry_after=30, details={"reason": "x"})

    assert error.code is ErrorCode.RATE_LIMITED
    assert error.retry_after == 30
    assert error.details == {"reason": "x", "retry_after": 30}
    assert STATUS_BY_ERROR_CODE[ErrorCode.RATE_LIMITED] == 429
    assert ResourceGoneError("Parti.").code is ErrorCode.GONE
    assert STATUS_BY_ERROR_CODE[ErrorCode.GONE] == 410
