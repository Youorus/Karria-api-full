from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class CreateCheckoutSessionRequest(BaseModel):
    """Requête de création d'une session de paiement Stripe Checkout (v2)."""

    payment_type: Literal["coffee", "template"] = Field(
        ..., description="Type de paiement : 'coffee' pour soutien, 'template' pour modèle de CV."
    )
    coffee_count: Optional[int] = Field(
        default=1, ge=1, le=10, description="Nombre de cafés (1 café = 5,00 €)."
    )
    custom_amount_eur: Optional[float] = Field(
        default=None, ge=1.50, description="Montant libre en Euros (minimum 1,50 €)."
    )
    template_code: Optional[str] = Field(
        default=None, description="Code du modèle de CV à acheter."
    )
    return_url: Optional[str] = Field(
        default="https://karria.fr/payment-success",
        description="URL de retour après le paiement.",
    )


class CheckoutSessionResponse(BaseModel):
    """Réponse de création de session Stripe Checkout."""

    checkout_url: str = Field(..., description="URL de redirection Stripe Checkout.")
    session_id: str = Field(..., description="ID de session Stripe.")
    amount_cents: int = Field(..., description="Montant total en centimes d'Euro.")
    currency: str = Field(default="eur", description="Devise (EUR).")


class PaymentStatusResponse(BaseModel):
    """Statut d'une session de paiement Stripe."""

    session_id: str
    status: str
    payment_type: Optional[str] = None
