"""Les erreurs du module : code du socle, raison fine dans `details`."""

from __future__ import annotations

from uuid import uuid4

import pytest

from karria.modules.offers.domain import (
    AssistantRateLimitedError,
    ConversationNotFoundError,
    CriteriaIncompleteError,
    CriteriaNotFoundError,
    ModelOutputInvalidError,
    ModelUnavailableError,
    OfferNotFoundError,
    SearchUnavailableError,
)
from karria.shared.errors import ErrorCode

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error", "code", "reason"),
    [
        (OfferNotFoundError(uuid4()), ErrorCode.NOT_FOUND, "offer_not_found"),
        (ConversationNotFoundError(uuid4()), ErrorCode.NOT_FOUND, "conversation_not_found"),
        (CriteriaNotFoundError(), ErrorCode.NOT_FOUND, "criteria_not_found"),
        (CriteriaIncompleteError(["job"]), ErrorCode.INVALID_INPUT, "criteria_incomplete"),
        (
            AssistantRateLimitedError(retry_after=60),
            ErrorCode.RATE_LIMITED,
            "assistant_rate_limited",
        ),
        (ModelUnavailableError("timeout"), ErrorCode.DEPENDENCY_FAILURE, "ai_unavailable"),
        (ModelOutputInvalidError(), ErrorCode.DEPENDENCY_FAILURE, "ai_output_invalid"),
        (SearchUnavailableError("Qdrant"), ErrorCode.DEPENDENCY_FAILURE, "search_unavailable"),
    ],
)
def test_chaque_erreur_porte_son_code_et_sa_raison(
    error: Exception, code: ErrorCode, reason: str
) -> None:
    assert error.code is code  # type: ignore[attr-defined]
    assert error.details["reason"] == reason  # type: ignore[attr-defined]


def test_les_criteres_incomplets_disent_ce_qui_manque() -> None:
    assert CriteriaIncompleteError(["job", "location"]).details["missing"] == ["job", "location"]


def test_loffre_introuvable_dit_laquelle() -> None:
    offer_id = uuid4()
    assert OfferNotFoundError(offer_id).details["offer_id"] == str(offer_id)
