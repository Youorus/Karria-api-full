"""Stripe, par le SDK officiel : implémente `BillingGateway`.

Un `StripeClient` par processus, construit avec la clé de la configuration —
jamais `stripe.api_key` global, qu'un autre code pourrait écraser. Le SDK est
synchrone : chaque appel part dans un fil.

Les prix sont retrouvés par `lookup_key`
----------------------------------------
Le domaine décide les montants (`domain/catalog.py`). Ici, chaque prix est
cherché chez Stripe par sa clé ; s'il manque ou si son montant diffère, un
prix neuf est créé et la clé lui est **transférée** : un montant changé dans
le code devient le prix facturé au premier achat, sans rien saisir dans le
tableau de bord. Les prix sont **TTC** (`tax_behavior=inclusive`) : 4,99 €
affiché, 4,99 € prélevé ; Stripe Tax, s'il est activé, ventile la TVA.

L'état est relu, jamais déduit
-------------------------------
Une notification ne sert qu'à désigner ce qu'elle concerne ; l'état est relu
(`fetch_subscription`, `retrieve_checkout`). Stripe ne garantit pas l'ordre.

Ce qui ne franchit jamais ce fichier
-------------------------------------
Aucun type `stripe.*` : les méthodes rendent des objets du domaine ou de
`application/ports.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import stripe

from karria.modules.payments.application import (
    BillingEvent,
    BillingEventKind,
    CheckoutRecord,
    CheckoutRequest,
    CheckoutSession,
    WebhookSignatureError,
)
from karria.modules.payments.domain import (
    CheckoutMode,
    PaymentsUnavailableError,
    PriceSpec,
    ProviderSubscription,
    SubscriptionStatus,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)

_NOT_FOUND = "resource_missing"

#: Les notifications traitées. Tout le reste s'acquitte sans effet.
_EVENT_KINDS: Final[dict[str, BillingEventKind]] = {
    "checkout.session.completed": BillingEventKind.CHECKOUT_COMPLETED,
    "customer.subscription.created": BillingEventKind.SUBSCRIPTION_CHANGED,
    "customer.subscription.updated": BillingEventKind.SUBSCRIPTION_CHANGED,
    "customer.subscription.deleted": BillingEventKind.SUBSCRIPTION_CHANGED,
    "invoice.payment_failed": BillingEventKind.INVOICE_PAYMENT_FAILED,
}

#: Le vocabulaire de Stripe, ramené aux quatre situations de Karria.
_STATUSES: Final[dict[str, SubscriptionStatus]] = {
    # Aucun essai n'est proposé ; s'il en venait un, il donne accès.
    "trialing": SubscriptionStatus.ACTIVE,
    "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "unpaid": SubscriptionStatus.PAST_DUE,
    "paused": SubscriptionStatus.PAST_DUE,
    "canceled": SubscriptionStatus.CANCELED,
    "incomplete": SubscriptionStatus.NONE,
    "incomplete_expired": SubscriptionStatus.NONE,
}


def _field(obj: Any, key: str) -> Any:
    """Lit un champ d'un objet Stripe, ou `None` : dictionnaire nu ou ressource du SDK."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _identifier(value: object) -> str | None:
    """Un identifiant, que Stripe donne en chaîne ou développé en objet."""
    if isinstance(value, str):
        return value or None
    identifier = _field(value, "id")
    return str(identifier) if identifier else None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, int) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        logger.warning("payments.bad_user_reference", value=str(value)[:80])
        return None


def _int(value: object) -> int | None:
    try:
        return int(str(value)) if value not in (None, "") else None
    except ValueError:
        return None


def translate_event(event: Any) -> BillingEvent:
    """Réduit une notification vérifiée à ce qu'elle désigne."""
    event_type = str(_field(event, "type") or "")
    event_id = str(_field(event, "id") or "")
    kind = _EVENT_KINDS.get(event_type, BillingEventKind.IGNORED)
    obj: Any = _field(_field(event, "data"), "object")
    if kind is BillingEventKind.CHECKOUT_COMPLETED:
        return BillingEvent(kind, event_id, event_type, session_id=_identifier(obj))
    if kind is BillingEventKind.SUBSCRIPTION_CHANGED:
        return BillingEvent(kind, event_id, event_type, subscription_id=_identifier(obj))
    if kind is BillingEventKind.INVOICE_PAYMENT_FAILED:
        # Depuis la version d'API « basil », l'abonnement d'une facture est
        # sous `parent.subscription_details.subscription` ; l'ancien
        # emplacement est lu en second.
        details = _field(_field(obj, "parent"), "subscription_details")
        subscription_id = _identifier(_field(details, "subscription")) or _identifier(
            _field(obj, "subscription")
        )
        if subscription_id is None:
            return BillingEvent(BillingEventKind.IGNORED, event_id, event_type)
        return BillingEvent(
            kind,
            event_id,
            event_type,
            subscription_id=subscription_id,
            invoice_id=_identifier(obj),
        )
    return BillingEvent(kind, event_id, event_type)


def translate_subscription(subscription: Any) -> ProviderSubscription:
    """Traduit un abonnement Stripe en état du domaine.

    Depuis la version « basil », `current_period_end` est sur chaque
    **élément** ; nous n'en créons qu'un, on lit le premier, puis l'ancien
    emplacement. Une résiliation programmée se dit par `cancel_at_period_end`
    ou par une date `cancel_at` : les deux signifient la même chose.
    """
    raw = str(_field(subscription, "status") or "")
    status = _STATUSES.get(raw)
    if status is None:
        logger.warning("payments.unknown_provider_status", status=raw)
        status = SubscriptionStatus.PAST_DUE
    items = _field(_field(subscription, "items"), "data") or []
    item = items[0] if items else None
    period_end = _timestamp(_field(item, "current_period_end")) or _timestamp(
        _field(subscription, "current_period_end")
    )
    cancel_at = _timestamp(_field(subscription, "cancel_at"))
    cancel_scheduled = bool(_field(subscription, "cancel_at_period_end")) or cancel_at is not None
    ended_at = _timestamp(_field(subscription, "ended_at"))
    metadata = _field(subscription, "metadata") or {}
    return ProviderSubscription(
        subscription_id=str(_field(subscription, "id") or ""),
        customer_id=_identifier(_field(subscription, "customer")) or "",
        status=status,
        current_period_ends_at=period_end,
        cancel_scheduled=cancel_scheduled,
        ends_at=ended_at or cancel_at or (period_end if cancel_scheduled else None),
        user_id=_uuid(_field(metadata, "user_id")),
    )


def translate_checkout(session: Any) -> CheckoutRecord:
    """Traduit une session Checkout : mode, paiement, métadonnées posées à l'ouverture."""
    raw: Any = _field(session, "metadata") or {}
    metadata: dict[str, str] = {str(k): str(v) for k, v in dict(raw).items()}
    mode: CheckoutMode = "subscription" if _field(session, "mode") == "subscription" else "payment"
    return CheckoutRecord(
        session_id=str(_field(session, "id") or ""),
        mode=mode,
        paid=_field(session, "payment_status") == "paid",
        amount_cents=int(_field(session, "amount_total") or 0),
        currency=str(_field(session, "currency") or "eur"),
        user_id=_uuid(metadata.get("user_id")),
        kind=metadata.get("kind") or None,
        coffee_count=_int(metadata.get("coffee_count")),
        template_id=_uuid(metadata.get("template_id")),
        subscription_id=_identifier(_field(session, "subscription")),
        customer_id=_identifier(_field(session, "customer")),
    )


class StripeGateway:
    """Implémente `BillingGateway`.

    `client` se substitue dans les tests ; en production c'est un
    `StripeClient` construit avec la clé.
    """

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        *,
        automatic_tax: bool = False,
        client: Any | None = None,
    ) -> None:
        self._client: Any = client if client is not None else stripe.StripeClient(secret_key)
        self._webhook_secret = webhook_secret
        self._automatic_tax = automatic_tax
        #: Les identifiants de prix déjà résolus, par clé : un aller-retour par processus.
        self._prices: dict[str, str] = {}

    async def _call(self, operation: str, method: Any, *args: Any, **kwargs: Any) -> Any:
        """Appelle le SDK hors de la boucle ; toute panne devient `PaymentsUnavailableError`.

        Le détail part au journal, jamais au client : il nomme le prestataire
        et peut porter des identifiants internes.
        """
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except stripe.StripeError as exc:
            logger.error(
                "payments.stripe_failed",
                operation=operation,
                reason=type(exc).__name__,
                detail=str(exc)[:200],
            )
            raise PaymentsUnavailableError("stripe_error") from exc

    async def ensure_customer(self, user_id: UUID, email: str | None) -> str:
        """`customers.create`, rejouable 24 h : deux premiers appuis ne créent qu'un client."""
        params: dict[str, Any] = {"metadata": {"user_id": str(user_id)}}
        if email:
            params["email"] = email
        customer = await self._call(
            "create_customer",
            self._client.v1.customers.create,
            params,
            options={"idempotency_key": f"karria-customer:{user_id}"},
        )
        logger.info("payments.customer_created", user_id=str(user_id))
        return str(customer.id)

    async def ensure_price(self, spec: PriceSpec) -> str:
        """Le prix de cette clé, au montant du domaine ; créé (et la clé transférée) sinon."""
        cached = self._prices.get(spec.lookup_key)
        if cached is not None:
            return cached
        found = await self._call(
            "list_prices",
            self._client.v1.prices.list,
            {"lookup_keys": [spec.lookup_key], "active": True, "limit": 1},
        )
        existing = next(iter(_field(found, "data") or []), None)
        if existing is not None and _matches(existing, spec):
            price_id = str(existing.id)
        else:
            params: dict[str, Any] = {
                "currency": spec.currency,
                "unit_amount": spec.amount_cents,
                "lookup_key": spec.lookup_key,
                "transfer_lookup_key": True,
                "tax_behavior": "inclusive",
                "product_data": {"name": spec.product_name},
                "metadata": {"lookup_key": spec.lookup_key},
            }
            if spec.interval is not None:
                params["recurring"] = {"interval": spec.interval}
            created = await self._call(
                "create_price",
                self._client.v1.prices.create,
                params,
                options={"idempotency_key": f"karria-price:{spec.lookup_key}:{spec.amount_cents}"},
            )
            price_id = str(created.id)
            logger.info(
                "payments.price_created",
                lookup_key=spec.lookup_key,
                amount_cents=spec.amount_cents,
                replaced=existing is not None,
            )
        self._prices[spec.lookup_key] = price_id
        return price_id

    async def open_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        """`checkout.sessions.create`."""
        params: dict[str, Any] = {
            "mode": request.mode,
            "customer": request.customer_id,
            "line_items": [{"price": request.price_id, "quantity": 1}],
            "success_url": request.success_url,
            "cancel_url": request.cancel_url,
            "metadata": request.metadata,
            "automatic_tax": {"enabled": self._automatic_tax},
        }
        if request.mode == "subscription":
            params["subscription_data"] = {"metadata": request.metadata}
        else:
            params["payment_intent_data"] = {"metadata": request.metadata}
        if self._automatic_tax:
            # Stripe l'exige dès qu'un client existant est passé avec le calcul de la taxe.
            params["customer_update"] = {"address": "auto"}
        session = await self._call(
            "create_checkout",
            self._client.v1.checkout.sessions.create,
            params,
            options={"idempotency_key": request.idempotency_key},
        )
        url = _field(session, "url")
        if not url:
            raise PaymentsUnavailableError("stripe_no_url")
        return CheckoutSession(id=str(session.id), url=str(url))

    async def retrieve_checkout(self, session_id: str) -> CheckoutRecord | None:
        """`checkout.sessions.retrieve` ; `None` si Stripe ne la connaît pas."""
        try:
            session = await asyncio.to_thread(
                self._client.v1.checkout.sessions.retrieve, session_id
            )
        except stripe.InvalidRequestError as exc:
            if exc.code == _NOT_FOUND:
                return None
            logger.error("payments.stripe_failed", operation="retrieve", detail=str(exc)[:200])
            raise PaymentsUnavailableError("stripe_error") from exc
        except stripe.StripeError as exc:
            logger.error("payments.stripe_failed", operation="retrieve", detail=str(exc)[:200])
            raise PaymentsUnavailableError("stripe_error") from exc
        return translate_checkout(session)

    async def open_portal(self, customer_id: str, return_url: str) -> str:
        """`billing_portal.sessions.create`."""
        session = await self._call(
            "open_portal",
            self._client.v1.billing_portal.sessions.create,
            {"customer": customer_id, "return_url": return_url},
        )
        return str(session.url)

    def read_event(self, payload: bytes, signature: str) -> BillingEvent:
        """`construct_event` : la signature prouve l'origine."""
        if not self._webhook_secret:
            raise WebhookSignatureError("aucun secret de webhook configuré")
        try:
            event = self._client.construct_event(payload, signature, self._webhook_secret)
        except (stripe.SignatureVerificationError, ValueError) as exc:
            raise WebhookSignatureError(str(exc)) from exc
        return translate_event(event)

    async def fetch_subscription(self, subscription_id: str) -> ProviderSubscription:
        """`subscriptions.retrieve`."""
        subscription = await self._call(
            "fetch_subscription", self._client.v1.subscriptions.retrieve, subscription_id
        )
        return translate_subscription(subscription)

    async def delete_customer(self, customer_id: str) -> None:
        """`customers.delete` : résilie et détache ; un client déjà parti ne lève pas."""
        try:
            await asyncio.to_thread(self._client.v1.customers.delete, customer_id)
        except stripe.InvalidRequestError as exc:
            if exc.code != _NOT_FOUND:
                logger.error("payments.stripe_failed", operation="delete", detail=str(exc)[:200])
                raise PaymentsUnavailableError("stripe_error") from exc
        except stripe.StripeError as exc:
            logger.error("payments.stripe_failed", operation="delete", detail=str(exc)[:200])
            raise PaymentsUnavailableError("stripe_error") from exc
        logger.info("payments.customer_deleted")


def _matches(existing: Any, spec: PriceSpec) -> bool:
    """Le prix Stripe porte-t-il exactement ce que le domaine décide ?"""
    recurring = _field(existing, "recurring")
    interval = _field(recurring, "interval") if recurring else None
    return (
        _field(existing, "unit_amount") == spec.amount_cents
        and str(_field(existing, "currency") or "").lower() == spec.currency
        and interval == spec.interval
    )
