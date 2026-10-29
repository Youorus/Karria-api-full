"""Cas d'usage de l'encaissement : ouvrir un paiement, le relire, écouter le prestataire.

Chaque cas d'usage ne connaît que le port `PaymentGateway`. Sans prestataire
configuré, il n'y a **pas** de mode démonstration : la route répond 503
`payments_unavailable`. Un faux « payé » rendu à un client qui s'y fierait
pour débloquer un modèle serait une faille, pas une commodité.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from karria.modules.payments.application.ports import (
    CheckoutSession,
    PaymentGateway,
    PaymentRecord,
    WebhookEvent,
    WebhookSignatureError,
)
from karria.modules.payments.domain import (
    CheckoutOrder,
    PaymentsUnavailableError,
    price,
    validate_return_url,
)
from karria.shared.errors.exceptions import EntityNotFoundError, InvalidInputError
from karria.shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OpenedCheckout:
    """Ce que l'ouverture rend à l'app."""

    session: CheckoutSession
    amount_cents: int
    currency: str


class OpenCheckout:
    """Décide le prix, contrôle le retour, ouvre la session au nom de la personne."""

    def __init__(self, gateway: PaymentGateway | None) -> None:
        self._gateway = gateway

    async def execute(self, user_id: UUID, order: CheckoutOrder, return_url: str) -> OpenedCheckout:
        """Ouvre. `PaymentsUnavailableError` sans prestataire."""
        if self._gateway is None:
            raise PaymentsUnavailableError("payments_not_configured")
        priced = price(order)
        back = validate_return_url(return_url)
        separator = "&" if "?" in back else "?"
        session = await self._gateway.create_checkout(
            amount_cents=priced.amount_cents,
            currency=priced.currency,
            label=priced.label,
            success_url=f"{back}{separator}session_id={{CHECKOUT_SESSION_ID}}&status=success",
            cancel_url=f"{back}{separator}status=cancelled",
            metadata={
                "payment_type": order.payment_type.value,
                "user_id": str(user_id),
                "template_code": order.template_code or "",
                "amount_cents": str(priced.amount_cents),
            },
        )
        logger.info(
            "payments.checkout_opened",
            user_id=str(user_id),
            session_id=session.id,
            payment_type=order.payment_type.value,
            amount_cents=priced.amount_cents,
        )
        return OpenedCheckout(session, priced.amount_cents, priced.currency)


class GetPaymentStatus:
    """L'état d'une session — seulement pour la personne qui l'a ouverte."""

    def __init__(self, gateway: PaymentGateway | None) -> None:
        self._gateway = gateway

    async def execute(self, user_id: UUID, session_id: str) -> PaymentRecord:
        """Relit. `404` si la session n'existe pas ou n'appartient pas à la personne."""
        if self._gateway is None:
            raise PaymentsUnavailableError("payments_not_configured")
        record = await self._gateway.retrieve(session_id)
        if record is None or record.user_id != str(user_id):
            # Même réponse dans les deux cas : ne pas dire qu'une session existe.
            raise EntityNotFoundError("Paiement", session_id)
        return record


class HandleWebhook:
    """Authentifie un événement du prestataire et le journalise.

    Rien n'est encore **livré** à la réception (ni modèle débloqué, ni reçu
    envoyé) : l'app relit le statut par `GET /payments/status/{id}`. Le jour
    où un paiement doit produire un effet, c'est ici qu'un événement du bus
    partira.
    """

    def __init__(self, gateway: PaymentGateway | None) -> None:
        self._gateway = gateway

    def execute(self, payload: bytes, signature: str | None) -> WebhookEvent:
        """Vérifie et traduit. `InvalidInputError` si la signature manque ou ment."""
        if self._gateway is None:
            raise PaymentsUnavailableError("payments_not_configured")
        if not signature:
            raise InvalidInputError(
                "En-tête stripe-signature manquant.", details={"reason": "signature_missing"}
            )
        try:
            event = self._gateway.verify_webhook(payload, signature)
        except WebhookSignatureError as exc:
            logger.warning("payments.webhook_rejected")
            raise InvalidInputError(
                "Signature de l'événement invalide.", details={"reason": "signature_invalid"}
            ) from exc
        logger.info(
            "payments.webhook_received",
            kind=event.kind,
            session_id=event.session_id,
            payment_type=event.payment_type,
            user_id=event.user_id,
        )
        return event
