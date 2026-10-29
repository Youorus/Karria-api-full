"""Routes du module `payments` : ouvrir un paiement, le relire, recevoir Stripe.

Les deux premières exigent la session de la personne : une session de
paiement porte son identifiant, et seule elle peut en relire l'état. Le
webhook, lui, est appelé par Stripe et s'authentifie par sa signature.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Path, Request, status

from karria.modules.payments.api.dependencies import (
    HandleWebhookDep,
    OpenCheckoutDep,
    PaymentStatusDep,
)
from karria.modules.payments.api.schemas import (
    CheckoutSessionResponse,
    CreateCheckoutSessionRequest,
    PaymentStatusResponse,
    WebhookAcknowledged,
)
from karria.shared.auth import CurrentPrincipalDep
from karria.shared.errors import ErrorCode, error_responses
from karria.shared.http.dependencies import SettingsDep

router = APIRouter(prefix="/payments", tags=["Paiements"])

#: Stripe signe des corps de quelques kilo-octets ; au-delà, ce n'est pas lui.
WEBHOOK_MAX_BYTES = 256 * 1024


@router.post(
    "/checkout-session",
    response_model=CheckoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ouvrir une session Stripe Checkout",
    description=(
        "Un café (5 €, jusqu'à 10) ou un montant libre (1,50 à 500 €) pour soutenir l'équipe, "
        "ou un modèle de CV Premium. Rend l'adresse de la page de paiement. "
        "`422 return_url_not_allowed` si l'adresse de retour ne vise pas Karria ; "
        "`503 dependency_failure` sans prestataire configuré."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def create_checkout_session(
    body: CreateCheckoutSessionRequest,
    principal: CurrentPrincipalDep,
    open_checkout: OpenCheckoutDep,
    settings: SettingsDep,
) -> CheckoutSessionResponse:
    """Ouvre."""
    opened = await open_checkout.execute(
        principal.subject_id, body.to_order(), body.return_url or settings.payments.return_url
    )
    return CheckoutSessionResponse(
        checkout_url=opened.session.url,
        session_id=opened.session.id,
        amount_cents=opened.amount_cents,
        currency=opened.currency,
    )


@router.get(
    "/status/{session_id}",
    response_model=PaymentStatusResponse,
    summary="L'état d'un paiement",
    description="`404 not_found` si la session n'existe pas ou n'a pas été ouverte par la personne.",
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def get_payment_status(
    session_id: Annotated[str, Path(max_length=128, description="Identifiant Stripe (`cs_…`).")],
    principal: CurrentPrincipalDep,
    get_status: PaymentStatusDep,
) -> PaymentStatusResponse:
    """Relit."""
    record = await get_status.execute(principal.subject_id, session_id)
    return PaymentStatusResponse(
        session_id=record.session_id,
        status="paid" if record.paid else "unpaid",
        payment_type=record.payment_type,
    )


@router.post(
    "/webhook",
    response_model=WebhookAcknowledged,
    summary="Webhook Stripe",
    description=(
        "Appelé par Stripe. La signature (`stripe-signature`) est vérifiée avec le secret du "
        "webhook ; `422 signature_invalid` sinon. L'événement est journalisé ; rien n'est "
        "encore livré à la réception."
    ),
    responses=error_responses(ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE),
)
async def stripe_webhook(
    request: Request,
    handle: HandleWebhookDep,
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
) -> WebhookAcknowledged:
    """Reçoit."""
    payload = await request.body()
    if len(payload) > WEBHOOK_MAX_BYTES:
        payload = b""  # une signature ne vérifiera jamais un corps tronqué : refus propre
    event = handle.execute(payload, stripe_signature)
    return WebhookAcknowledged(type=event.kind)
