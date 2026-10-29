"""Doublures du module `payments`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from karria.modules.payments.application import (
    CheckoutSession,
    PaymentRecord,
    WebhookEvent,
    WebhookSignatureError,
)


@dataclass(slots=True)
class FakeGateway:
    """`PaymentGateway` en mémoire : retient les ouvertures, sert des sessions scriptées."""

    sessions: dict[str, PaymentRecord] = field(default_factory=dict)
    opened: list[dict[str, object]] = field(default_factory=list)
    valid_signature: str = "ok"

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
        """Retient et rend une session."""
        self.opened.append(
            {
                "amount_cents": amount_cents,
                "currency": currency,
                "label": label,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "metadata": dict(metadata),
            }
        )
        session_id = f"cs_{len(self.opened)}"
        self.sessions[session_id] = PaymentRecord(
            session_id=session_id,
            paid=False,
            payment_type=metadata.get("payment_type"),
            user_id=metadata.get("user_id"),
        )
        return CheckoutSession(id=session_id, url=f"https://checkout.stripe.test/{session_id}")

    async def retrieve(self, session_id: str) -> PaymentRecord | None:
        """Scripté."""
        return self.sessions.get(session_id)

    def verify_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """`valid_signature` passe, tout le reste ment."""
        if signature != self.valid_signature:
            raise WebhookSignatureError("signature")
        return WebhookEvent(
            kind="checkout.session.completed",
            session_id=payload.decode() or None,
            payment_type="coffee",
            user_id="u",
        )
