from __future__ import annotations

import asyncio
import os
from typing import Any, Optional
import structlog
import stripe

from karria.modules.payments.api.schemas import (
    CreateCheckoutSessionRequest,
    CheckoutSessionResponse,
    PaymentStatusResponse,
)

logger = structlog.get_logger(__name__)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")


class StripeGatewayV2:
    """Gestionnaire d'encaissement Stripe pour Karria API v2."""

    def __init__(self) -> None:
        self.secret_key = STRIPE_SECRET_KEY
        self.webhook_secret = STRIPE_WEBHOOK_SECRET
        if self.secret_key:
            stripe.api_key = self.secret_key

    async def open_checkout_session(
        self, req: CreateCheckoutSessionRequest, user_id: Optional[str] = None
    ) -> CheckoutSessionResponse:
        """Ouvre une session de paiement Stripe Checkout (1 café = 5 € / montant libre)."""

        amount_cents = 500
        item_name = "Soutien Équipe Karria ☕"

        if req.payment_type == "coffee":
            if req.custom_amount_eur is not None and req.custom_amount_eur >= 1.50:
                amount_cents = int(round(req.custom_amount_eur * 100))
                item_name = f"Soutien Équipe Karria — {req.custom_amount_eur:.2f} € ☕"
            else:
                count = req.coffee_count or 1
                amount_cents = count * 500  # 1 café = 5,00 €
                s_plural = "s" if count > 1 else ""
                item_name = f"Soutien Équipe Karria ({count} café{s_plural}) ☕"

        elif req.payment_type == "template":
            amount_cents = 499  # Prix par défaut d'un modèle CV Premium (4,99 €)
            item_name = f"Modèle CV Premium — {req.template_code or 'Standard'}"

        # Si Stripe n'est pas configuré en local, URL de démo sécurisée
        if not self.secret_key:
            logger.warning("payments.stripe_not_configured_using_demo")
            demo_url = f"https://karria.fr/demo-payment?amount={amount_cents}&type={req.payment_type}"
            return CheckoutSessionResponse(
                checkout_url=demo_url,
                session_id=f"demo_sess_{amount_cents}",
                amount_cents=amount_cents,
                currency="eur",
            )

        return_url = req.return_url or "https://karria.fr/payment-success"

        params: dict[str, Any] = {
            "mode": "payment",
            "payment_method_types": ["card"],
            "line_items": [
                {
                    "price_data": {
                        "currency": "eur",
                        "product_data": {"name": item_name},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            "success_url": f"{return_url}?session_id={{CHECKOUT_SESSION_ID}}&status=success",
            "cancel_url": f"{return_url}?status=cancelled",
            "metadata": {
                "payment_type": req.payment_type,
                "user_id": user_id or "",
                "template_code": req.template_code or "",
                "amount_cents": str(amount_cents),
            },
        }

        try:
            session = await asyncio.to_thread(stripe.checkout.Session.create, **params)
            logger.info("payments.session_opened", session_id=session.id, amount_cents=amount_cents)
            return CheckoutSessionResponse(
                checkout_url=session.url or return_url,
                session_id=session.id,
                amount_cents=amount_cents,
                currency="eur",
            )
        except Exception as exc:
            logger.error("payments.session_failed", error=str(exc))
            raise RuntimeError(f"Échec Stripe: {exc}") from exc

    async def get_status(self, session_id: str) -> PaymentStatusResponse:
        """Vérifie le statut d'une session Stripe."""
        if session_id.startswith("demo_"):
            return PaymentStatusResponse(session_id=session_id, status="paid", payment_type="demo")

        if not self.secret_key:
            return PaymentStatusResponse(session_id=session_id, status="unpaid")

        try:
            session = await asyncio.to_thread(stripe.checkout.Session.retrieve, session_id)
            pay_status = "paid" if session.payment_status == "paid" else "unpaid"
            pay_type = session.metadata.get("payment_type") if session.metadata else None
            return PaymentStatusResponse(
                session_id=session.id,
                status=pay_status,
                payment_type=pay_type,
            )
        except Exception as exc:
            logger.error("payments.status_failed", session_id=session_id, error=str(exc))
            return PaymentStatusResponse(session_id=session_id, status="unpaid")

    def handle_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Traite les notifications Webhook Stripe."""
        if not self.webhook_secret:
            return {"status": "ignored", "reason": "no_webhook_secret"}

        try:
            event = stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
        except Exception as exc:
            logger.error("payments.webhook_invalid_signature", error=str(exc))
            raise ValueError("Signature Webhook invalide") from exc

        event_type = event.get("type")
        logger.info("payments.webhook_received", type=event_type)

        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            metadata = session.get("metadata", {})
            return {
                "status": "success",
                "payment_type": metadata.get("payment_type"),
                "user_id": metadata.get("user_id"),
            }

        return {"status": "handled", "type": event_type}
