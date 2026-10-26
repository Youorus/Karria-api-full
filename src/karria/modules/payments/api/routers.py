from __future__ import annotations

from typing import Annotated, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from karria.modules.payments.api import schemas
from karria.modules.payments.infrastructure.stripe_gateway import StripeGatewayV2

router = APIRouter(prefix="/payments", tags=["Paiements Stripe v2"])


def get_stripe_gateway() -> StripeGatewayV2:
    return StripeGatewayV2()


StripeGatewayDep = Annotated[StripeGatewayV2, Depends(get_stripe_gateway)]


@router.post(
    "/checkout-session",
    summary="Créer une session Stripe Checkout v2",
    response_model=schemas.CheckoutSessionResponse,
)
async def create_checkout_session(
    req: schemas.CreateCheckoutSessionRequest,
    gateway: StripeGatewayDep,
) -> schemas.CheckoutSessionResponse:
    """Génère une URL Stripe Checkout pour café solidaire ou modèle CV."""
    return await gateway.open_checkout_session(req)


@router.get(
    "/status/{session_id}",
    summary="Vérifier le statut d'un paiement Stripe v2",
    response_model=schemas.PaymentStatusResponse,
)
async def get_payment_status(
    session_id: str,
    gateway: StripeGatewayDep,
) -> schemas.PaymentStatusResponse:
    """Retourne le statut d'un paiement Stripe ('paid', 'unpaid')."""
    return await gateway.get_status(session_id)


@router.post(
    "/webhook",
    summary="Webhook Stripe v2",
    status_code=status.HTTP_200_OK,
)
async def stripe_webhook(
    request: Request,
    gateway: StripeGatewayDep,
    stripe_signature: Annotated[Optional[str], Header(alias="stripe-signature")] = None,
) -> dict:
    """Reçoit et valide les événements Stripe Webhook v2."""
    payload = await request.body()

    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="En-tête stripe-signature manquant",
        )

    try:
        return gateway.handle_webhook(payload, stripe_signature)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
