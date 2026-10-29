"""Ce dont l'encaissement a besoin de l'extérieur — sans nommer qui le fournit.

Cinq besoins :

- **le prestataire de paiement** (`BillingGateway`) — Stripe aujourd'hui.
  Aucun type `stripe.*` ne franchit ce fichier ;
- **la persistance** de la projection, des achats et du registre des
  notifications déjà traitées ;
- **l'adresse de la personne** (`AccountDirectory`), qui vit dans `identity`
  et que ce module n'importe pas (règle 4) : `bootstrap` la branche ;
- **les modèles de CV en vente et leur possession** (`TemplateStore`), qui
  vivent dans `documents` : branchés de même. Un modèle payé est remis par
  ce port, **dans la même transaction** que l'enregistrement de l'achat —
  jamais par le bus, qui n'est pas durable (`shared/events/bus.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from karria.modules.payments.domain import (
    BillingAccount,
    CheckoutMode,
    PremiumOffer,
    PriceSpec,
    ProviderSubscription,
    Purchase,
)


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """Une session de paiement ouverte chez le prestataire."""

    id: str
    url: str


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    """Une session à ouvrir.

    Attributes:
        idempotency_key: Rejouer la même demande rend la même session : un
            double appui n'ouvre pas deux paiements.
    """

    customer_id: str
    price_id: str
    mode: CheckoutMode
    success_url: str
    cancel_url: str
    metadata: dict[str, str]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CheckoutRecord:
    """Une session telle que le prestataire la connaît, métadonnées relues."""

    session_id: str
    mode: CheckoutMode
    paid: bool
    amount_cents: int
    currency: str
    user_id: UUID | None
    kind: str | None
    coffee_count: int | None = None
    template_id: UUID | None = None
    subscription_id: str | None = None
    customer_id: str | None = None


class BillingEventKind(StrEnum):
    """Les notifications qui nous font agir ; le reste s'acquitte sans effet."""

    CHECKOUT_COMPLETED = "checkout_completed"
    SUBSCRIPTION_CHANGED = "subscription_changed"
    INVOICE_PAYMENT_FAILED = "invoice_payment_failed"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class BillingEvent:
    """Une notification vérifiée, réduite à ce qu'elle désigne.

    Elle ne porte pas l'état : il est relu chez le prestataire, parce que les
    notifications arrivent dans le désordre.
    """

    kind: BillingEventKind
    event_id: str
    type: str
    session_id: str | None = None
    subscription_id: str | None = None
    invoice_id: str | None = None


class WebhookSignatureError(Exception):
    """La signature de l'événement ne correspond pas : il n'est pas de Stripe."""


class BillingGateway(Protocol):
    """Le prestataire : clients, prix, sessions, portail, notifications."""

    async def ensure_customer(self, user_id: UUID, email: str | None) -> str:
        """Ouvre (ou retrouve, par idempotence) le client et rend son identifiant."""
        ...

    async def ensure_price(self, spec: PriceSpec) -> str:
        """Le prix portant cette `lookup_key` et ce montant ; créé s'il manque. Rend son identifiant."""
        ...

    async def open_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        """Ouvre une session de paiement hébergée."""
        ...

    async def retrieve_checkout(self, session_id: str) -> CheckoutRecord | None:
        """Relit une session ; `None` si le prestataire ne la connaît pas."""
        ...

    async def open_portal(self, customer_id: str, return_url: str) -> str:
        """Ouvre le portail (carte, reçus, résiliation) et rend son adresse."""
        ...

    def read_event(self, payload: bytes, signature: str) -> BillingEvent:
        """Vérifie la signature et traduit l'événement ; `WebhookSignatureError` sinon."""
        ...

    async def fetch_subscription(self, subscription_id: str) -> ProviderSubscription:
        """Relit l'état courant d'un abonnement."""
        ...

    async def delete_customer(self, customer_id: str) -> None:
        """Supprime le client : ses abonnements s'arrêtent ; le prestataire garde les reçus."""
        ...


class BillingAccountRepository(Protocol):
    """La projection locale : un client et un abonnement par compte."""

    async def find_for_user(self, user_id: UUID, *, lock: bool = False) -> BillingAccount | None:
        """`lock` verrouille la ligne jusqu'à la fin de la transaction : deux
        notifications arrivent souvent à la même seconde."""
        ...

    async def find_by_customer(
        self, customer_id: str, *, lock: bool = False
    ) -> BillingAccount | None: ...

    async def save(self, account: BillingAccount) -> None: ...

    async def delete_for_user(self, user_id: UUID) -> None: ...


class PurchaseRepository(Protocol):
    """Les paiements uniques confirmés."""

    async def add(self, purchase: Purchase) -> bool:
        """Enregistre ; rend `False` si cette session était déjà enregistrée (rejeu)."""
        ...

    async def has_session(self, session_id: str) -> bool: ...

    async def of_user(self, user_id: UUID) -> list[Purchase]: ...

    async def delete_for_user(self, user_id: UUID) -> None: ...


class WebhookLedger(Protocol):
    """Les notifications déjà traitées, par identifiant d'événement."""

    async def record(self, event_id: str, event_type: str) -> bool:
        """Note l'événement ; rend `False` s'il avait déjà été noté."""
        ...


class AccountDirectory(Protocol):
    """Port : l'adresse e-mail d'un compte, pour les reçus du prestataire."""

    async def email_of(self, user_id: UUID) -> str | None:
        """Ne lève jamais : `None` si le compte est introuvable."""
        ...


class TemplateStore(Protocol):
    """Port : les modèles de CV Premium en vente, et leur remise."""

    async def premium_offers(self) -> list[PremiumOffer]: ...

    async def premium_offer(self, template_id: UUID) -> PremiumOffer | None: ...

    async def owns(self, user_id: UUID, template_id: UUID) -> bool: ...

    async def grant(self, user_id: UUID, template_id: UUID) -> None:
        """Remet le modèle à la personne. Idempotent."""
        ...
