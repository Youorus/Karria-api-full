"""Câblage du module `payments` : la configuration → le prestataire → les cas d'usage."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from karria.modules.payments.application import (
    GetPaymentStatus,
    HandleWebhook,
    OpenCheckout,
    PaymentGateway,
)
from karria.modules.payments.infrastructure import StripeGateway
from karria.shared.http.dependencies import SettingsDep


@lru_cache(maxsize=1)
def _gateway(secret_key: str, webhook_secret: str) -> StripeGateway:
    """Un client Stripe par processus (connexions réutilisées)."""
    return StripeGateway(secret_key, webhook_secret)


def get_payment_gateway(settings: SettingsDep) -> PaymentGateway | None:
    """Le prestataire, ou `None` sans clé : les cas d'usage répondent alors 503."""
    secret = settings.payments.secret_key.get_secret_value()
    if not secret:
        return None
    return _gateway(secret, settings.payments.webhook_secret.get_secret_value())


GatewayDep = Annotated[PaymentGateway | None, Depends(get_payment_gateway)]


def get_open_checkout(gateway: GatewayDep) -> OpenCheckout:
    return OpenCheckout(gateway)


def get_payment_status(gateway: GatewayDep) -> GetPaymentStatus:
    return GetPaymentStatus(gateway)


def get_handle_webhook(gateway: GatewayDep) -> HandleWebhook:
    return HandleWebhook(gateway)


OpenCheckoutDep = Annotated[OpenCheckout, Depends(get_open_checkout)]
PaymentStatusDep = Annotated[GetPaymentStatus, Depends(get_payment_status)]
HandleWebhookDep = Annotated[HandleWebhook, Depends(get_handle_webhook)]
