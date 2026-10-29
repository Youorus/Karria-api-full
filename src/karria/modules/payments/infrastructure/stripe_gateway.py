"""Stripe Checkout, par le SDK officiel : implémente `PaymentGateway`.

Un `StripeClient` par processus, construit avec la clé de la configuration —
jamais `stripe.api_key` global, qu'un autre code pourrait écraser. Le SDK est
synchrone : chaque appel part dans un fil.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import stripe

from karria.modules.payments.application import (
    CheckoutSession,
    PaymentRecord,
    WebhookEvent,
    WebhookSignatureError,
)
from karria.modules.payments.domain import PaymentsUnavailableError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

_NOT_FOUND = "resource_missing"


class StripeGateway:
    """Implémente `PaymentGateway`."""

    def __init__(self, secret_key: str, webhook_secret: str) -> None:
        self._client = stripe.StripeClient(secret_key)
        self._webhook_secret = webhook_secret

    async def create_checkout(
        self,
        *,
        amount_cents: int,
        currency: str,
        label: str,
        success_url: str,
        cancel_url: str,
        metadata: Mapping[str, str],
    ) -> CheckoutSession:
        """`checkout.sessions.create`."""
        params: Any = {
            "mode": "payment",
            "line_items": [
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {"name": label},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": dict(metadata),
        }
        try:
            session = await asyncio.to_thread(self._client.v1.checkout.sessions.create, params)
        except stripe.StripeError as exc:
            logger.error("payments.stripe_failed", operation="create", error=exc.user_message)
            raise PaymentsUnavailableError("stripe_error") from exc
        if not session.url:
            raise PaymentsUnavailableError("stripe_no_url")
        return CheckoutSession(id=session.id, url=session.url)

    async def retrieve(self, session_id: str) -> PaymentRecord | None:
        """`checkout.sessions.retrieve` ; `None` si Stripe ne la connaît pas."""
        try:
            session = await asyncio.to_thread(
                self._client.v1.checkout.sessions.retrieve, session_id
            )
        except stripe.InvalidRequestError as exc:
            if exc.code == _NOT_FOUND:
                return None
            logger.error("payments.stripe_failed", operation="retrieve", error=exc.user_message)
            raise PaymentsUnavailableError("stripe_error") from exc
        except stripe.StripeError as exc:
            logger.error("payments.stripe_failed", operation="retrieve", error=exc.user_message)
            raise PaymentsUnavailableError("stripe_error") from exc
        raw: Any = session.metadata or {}
        metadata: dict[str, str] = {str(k): str(v) for k, v in raw.items()}
        return PaymentRecord(
            session_id=session.id,
            paid=session.payment_status == "paid",
            payment_type=metadata.get("payment_type") or None,
            user_id=metadata.get("user_id") or None,
        )

    def verify_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """`construct_event` : la signature prouve l'origine."""
        if not self._webhook_secret:
            raise WebhookSignatureError("aucun secret de webhook configuré")
        try:
            event = self._client.construct_event(payload, signature, self._webhook_secret)
        except (stripe.SignatureVerificationError, ValueError) as exc:
            raise WebhookSignatureError(str(exc)) from exc
        data: Any = event.data.object
        metadata = dict(getattr(data, "metadata", None) or {})
        return WebhookEvent(
            kind=str(event.type),
            session_id=getattr(data, "id", None),
            payment_type=metadata.get("payment_type") or None,
            user_id=metadata.get("user_id") or None,
        )
