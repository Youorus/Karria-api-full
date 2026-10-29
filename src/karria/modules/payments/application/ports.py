"""Ce dont l'encaissement a besoin d'un prestataire de paiement — sans le nommer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """Une session de paiement ouverte chez le prestataire."""

    id: str
    url: str


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    """L'état d'une session telle que le prestataire la connaît."""

    session_id: str
    paid: bool
    payment_type: str | None
    #: L'identifiant de la personne, recopié des métadonnées à l'ouverture.
    user_id: str | None


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """Un événement du prestataire, signature vérifiée."""

    kind: str
    session_id: str | None = None
    payment_type: str | None = None
    user_id: str | None = None


class WebhookSignatureError(Exception):
    """La signature de l'événement ne correspond pas : il n'est pas de Stripe."""


class PaymentGateway(Protocol):
    """Le prestataire : ouvrir, relire, authentifier ses événements."""

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
        """Ouvre une session de paiement."""
        ...

    async def retrieve(self, session_id: str) -> PaymentRecord | None:
        """Relit une session ; `None` si elle n'existe pas."""
        ...

    def verify_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """Vérifie la signature et traduit l'événement ; `WebhookSignatureError` sinon."""
        ...
