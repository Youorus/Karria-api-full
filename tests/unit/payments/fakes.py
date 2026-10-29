"""Doublures du module `payments` : prestataire, dépôts et ports voisins, en mémoire."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from karria.modules.payments.application import (
    BillingEvent,
    BillingEventKind,
    CheckoutRecord,
    CheckoutRequest,
    CheckoutSession,
    WebhookSignatureError,
)
from karria.modules.payments.domain import (
    BillingAccount,
    PremiumOffer,
    PriceSpec,
    ProviderSubscription,
    Purchase,
    SubscriptionStatus,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@dataclass(slots=True)
class FakeGateway:
    """`BillingGateway` en mémoire : retient les ouvertures, sert des états scriptés.

    `events` : signature → événement rendu par `read_event` ; toute autre
    signature ment. `checkouts` et `subscriptions` : ce que le prestataire
    « sait » ; les tests y posent l'état attendu après paiement.
    """

    customers: list[tuple[UUID, str | None]] = field(default_factory=list)
    prices: dict[str, PriceSpec] = field(default_factory=dict)
    opened: list[CheckoutRequest] = field(default_factory=list)
    checkouts: dict[str, CheckoutRecord] = field(default_factory=dict)
    subscriptions: dict[str, ProviderSubscription] = field(default_factory=dict)
    events: dict[str, BillingEvent] = field(default_factory=dict)
    portals: list[tuple[str, str]] = field(default_factory=list)
    deleted_customers: list[str] = field(default_factory=list)

    async def ensure_customer(self, user_id: UUID, email: str | None) -> str:
        self.customers.append((user_id, email))
        return f"cus_{len(self.customers)}"

    async def ensure_price(self, spec: PriceSpec) -> str:
        self.prices[spec.lookup_key] = spec
        return f"price_{spec.lookup_key}"

    async def open_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        self.opened.append(request)
        session_id = f"cs_{len(self.opened)}"
        self.checkouts[session_id] = CheckoutRecord(
            session_id=session_id,
            mode=request.mode,
            paid=False,
            amount_cents=int(request.metadata["amount_cents"]),
            currency="eur",
            user_id=UUID(request.metadata["user_id"]),
            kind=request.metadata.get("kind"),
            coffee_count=(
                int(request.metadata["coffee_count"])
                if "coffee_count" in request.metadata
                else None
            ),
            template_id=(
                UUID(request.metadata["template_id"]) if "template_id" in request.metadata else None
            ),
            customer_id=request.customer_id,
        )
        return CheckoutSession(id=session_id, url=f"https://checkout.stripe.test/{session_id}")

    async def retrieve_checkout(self, session_id: str) -> CheckoutRecord | None:
        return self.checkouts.get(session_id)

    async def open_portal(self, customer_id: str, return_url: str) -> str:
        self.portals.append((customer_id, return_url))
        return f"https://billing.stripe.test/{customer_id}"

    def read_event(self, payload: bytes, signature: str) -> BillingEvent:
        event = self.events.get(signature)
        if event is None:
            raise WebhookSignatureError("signature")
        return event

    async def fetch_subscription(self, subscription_id: str) -> ProviderSubscription:
        return self.subscriptions[subscription_id]

    async def delete_customer(self, customer_id: str) -> None:
        self.deleted_customers.append(customer_id)

    # --- Aides de scénario --------------------------------------------

    def pay(self, session_id: str, *, subscription_id: str | None = None) -> CheckoutRecord:
        """Le prestataire a encaissé cette session."""
        record = self.checkouts[session_id]
        paid = CheckoutRecord(
            session_id=record.session_id,
            mode=record.mode,
            paid=True,
            amount_cents=record.amount_cents,
            currency=record.currency,
            user_id=record.user_id,
            kind=record.kind,
            coffee_count=record.coffee_count,
            template_id=record.template_id,
            subscription_id=subscription_id,
            customer_id=record.customer_id,
        )
        self.checkouts[session_id] = paid
        return paid

    def subscription(
        self,
        subscription_id: str,
        customer_id: str,
        *,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
        period_end: datetime | None = None,
        cancel_scheduled: bool = False,
        ends_at: datetime | None = None,
        user_id: UUID | None = None,
    ) -> ProviderSubscription:
        remote = ProviderSubscription(
            subscription_id=subscription_id,
            customer_id=customer_id,
            status=status,
            current_period_ends_at=period_end,
            cancel_scheduled=cancel_scheduled,
            ends_at=ends_at,
            user_id=user_id,
        )
        self.subscriptions[subscription_id] = remote
        return remote

    def event(
        self,
        signature: str,
        kind: BillingEventKind,
        *,
        event_id: str | None = None,
        session_id: str | None = None,
        subscription_id: str | None = None,
        invoice_id: str | None = None,
    ) -> BillingEvent:
        event = BillingEvent(
            kind=kind,
            event_id=event_id or f"evt_{signature}",
            type=kind.value,
            session_id=session_id,
            subscription_id=subscription_id,
            invoice_id=invoice_id,
        )
        self.events[signature] = event
        return event


@dataclass(slots=True)
class InMemoryAccounts:
    """`BillingAccountRepository`."""

    rows: dict[UUID, BillingAccount] = field(default_factory=dict)
    locks: int = 0

    async def find_for_user(self, user_id: UUID, *, lock: bool = False) -> BillingAccount | None:
        self.locks += lock
        return self.rows.get(user_id)

    async def find_by_customer(
        self, customer_id: str, *, lock: bool = False
    ) -> BillingAccount | None:
        self.locks += lock
        return next((a for a in self.rows.values() if a.customer_id == customer_id), None)

    async def save(self, account: BillingAccount) -> None:
        self.rows[account.user_id] = account

    async def delete_for_user(self, user_id: UUID) -> None:
        self.rows.pop(user_id, None)


@dataclass(slots=True)
class InMemoryPurchases:
    """`PurchaseRepository`."""

    rows: list[Purchase] = field(default_factory=list)

    async def add(self, purchase: Purchase) -> bool:
        if any(p.session_id == purchase.session_id for p in self.rows):
            return False
        self.rows.append(purchase)
        return True

    async def has_session(self, session_id: str) -> bool:
        return any(p.session_id == session_id for p in self.rows)

    async def of_user(self, user_id: UUID) -> list[Purchase]:
        return [p for p in self.rows if p.user_id == user_id]

    async def delete_for_user(self, user_id: UUID) -> None:
        self.rows = [p for p in self.rows if p.user_id != user_id]


@dataclass(slots=True)
class InMemoryLedger:
    """`WebhookLedger`."""

    seen: dict[str, str] = field(default_factory=dict)

    async def record(self, event_id: str, event_type: str) -> bool:
        if event_id in self.seen:
            return False
        self.seen[event_id] = event_type
        return True


@dataclass(slots=True)
class FakeDirectory:
    """`AccountDirectory` : une adresse par compte connu."""

    emails: dict[UUID, str] = field(default_factory=dict)

    async def email_of(self, user_id: UUID) -> str | None:
        return self.emails.get(user_id)


@dataclass(slots=True)
class FakeTemplateStore:
    """`TemplateStore` : des modèles en vente, des possessions, des remises retenues."""

    offers: dict[UUID, PremiumOffer] = field(default_factory=dict)
    owned: set[tuple[UUID, UUID]] = field(default_factory=set)
    granted: list[tuple[UUID, UUID]] = field(default_factory=list)

    def sell(self, label: str = "Meridian", amount_cents: int = 499) -> PremiumOffer:
        offer = PremiumOffer(uuid4(), label, amount_cents)
        self.offers[offer.template_id] = offer
        return offer

    async def premium_offers(self) -> list[PremiumOffer]:
        return list(self.offers.values())

    async def premium_offer(self, template_id: UUID) -> PremiumOffer | None:
        return self.offers.get(template_id)

    async def owns(self, user_id: UUID, template_id: UUID) -> bool:
        return (user_id, template_id) in self.owned

    async def grant(self, user_id: UUID, template_id: UUID) -> None:
        self.granted.append((user_id, template_id))
        self.owned.add((user_id, template_id))
