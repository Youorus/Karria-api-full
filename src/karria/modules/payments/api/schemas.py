"""Contrat HTTP du module `payments`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from karria.modules.payments.domain import (
    MAX_COFFEES,
    MAX_CUSTOM_AMOUNT_CENTS,
    MIN_CUSTOM_AMOUNT_CENTS,
    CheckoutOrder,
    PaymentType,
)


class CreateCheckoutSessionRequest(BaseModel):
    """Ouvrir une session Stripe Checkout."""

    model_config = ConfigDict(extra="forbid")

    payment_type: Literal["coffee", "template"] = Field(
        description="`coffee` : soutien à l'équipe ; `template` : un modèle de CV Premium."
    )
    coffee_count: int = Field(
        default=1, ge=1, le=MAX_COFFEES, description="Nombre de cafés (un café = 5,00 €)."
    )
    custom_amount_eur: float | None = Field(
        default=None,
        ge=MIN_CUSTOM_AMOUNT_CENTS / 100,
        le=MAX_CUSTOM_AMOUNT_CENTS / 100,
        description="Montant libre en euros ; prime sur `coffee_count`.",
    )
    template_code: str | None = Field(
        default=None, max_length=64, description="Code du modèle de CV à acheter."
    )
    return_url: str | None = Field(
        default=None,
        max_length=2_048,
        description=(
            "Où revenir après le paiement : `https://karria.fr/…` ou `karria://…`. "
            "Absent : l'adresse par défaut du serveur. `422 return_url_not_allowed` sinon."
        ),
    )

    def to_order(self) -> CheckoutOrder:
        """La commande du domaine."""
        return CheckoutOrder(
            payment_type=PaymentType(self.payment_type),
            coffee_count=self.coffee_count,
            custom_amount_cents=(
                round(self.custom_amount_eur * 100) if self.custom_amount_eur is not None else None
            ),
            template_code=self.template_code,
        )


class CheckoutSessionResponse(BaseModel):
    """La session ouverte."""

    checkout_url: str = Field(description="Adresse de la page de paiement Stripe.")
    session_id: str
    amount_cents: int = Field(description="Montant total, en centimes d'euro.")
    currency: str = Field(default="eur")


class PaymentStatusResponse(BaseModel):
    """L'état d'une session."""

    session_id: str
    status: Literal["paid", "unpaid"]
    payment_type: str | None = None


class WebhookAcknowledged(BaseModel):
    """Stripe n'attend qu'un 2xx ; on dit ce qu'on a reçu."""

    received: bool = True
    type: str
