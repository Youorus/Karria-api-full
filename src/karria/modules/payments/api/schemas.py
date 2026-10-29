"""Contrat HTTP du module `payments` (`karria_app/docs/contrat-api-paiements.md`)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from karria.modules.payments.application import BillingOverview, PlatformFlags
from karria.modules.payments.domain import (
    Catalog,
    CheckoutOrder,
    PremiumOffer,
    PriceSpec,
    ProductKind,
)


class EnabledRead(BaseModel):
    """L'interrupteur par plateforme : faux → l'app ne montre aucun paiement."""

    ios: bool
    android: bool

    @classmethod
    def from_flags(cls, flags: PlatformFlags) -> EnabledRead:
        return cls(ios=flags.ios_enabled, android=flags.android_enabled)


class PriceRead(BaseModel):
    """Un prix du catalogue, en centimes d'euro, TTC."""

    lookup_key: str
    label: str
    amount_cents: int
    currency: str
    interval: Literal["month"] | None = Field(
        default=None, description="`month` pour l'abonnement ; absent pour un paiement unique."
    )

    @classmethod
    def from_spec(cls, spec: PriceSpec) -> PriceRead:
        return cls(
            lookup_key=spec.lookup_key,
            label=spec.product_name,
            amount_cents=spec.amount_cents,
            currency=spec.currency,
            interval=spec.interval,
        )


class CoffeeRead(PriceRead):
    """Un palier de cafés."""

    count: int


class TemplateOfferRead(BaseModel):
    """Un modèle de CV Premium en vente (le prix est celui de `documents`)."""

    template_id: UUID
    label: str
    amount_cents: int


class CatalogResponse(BaseModel):
    """Ce que l'app affiche sur la carte de soutien et le détail d'un modèle."""

    currency: str
    ad_free: PriceRead = Field(description="« Karria sans pub », par mois, sans engagement.")
    coffees: list[CoffeeRead]
    templates: list[TemplateOfferRead]
    enabled: EnabledRead

    @classmethod
    def present(cls, cat: Catalog, flags: PlatformFlags) -> CatalogResponse:
        return cls(
            currency=cat.currency,
            ad_free=PriceRead.from_spec(cat.ad_free),
            coffees=[
                CoffeeRead(count=count, **PriceRead.from_spec(spec).model_dump())
                for count, spec in cat.coffees
            ],
            templates=[_offer(o) for o in cat.templates],
            enabled=EnabledRead.from_flags(flags),
        )


def _offer(offer: PremiumOffer) -> TemplateOfferRead:
    return TemplateOfferRead(
        template_id=offer.template_id, label=offer.label, amount_cents=offer.amount_cents
    )


class CreateCheckoutSessionRequest(BaseModel):
    """Ouvrir une session Stripe Checkout."""

    model_config = ConfigDict(extra="forbid")

    payment_type: Literal["ad_free", "coffee", "template"] = Field(
        description=(
            "`ad_free` : l'abonnement « Karria sans pub » ; `coffee` : des cafés pour "
            "l'équipe ; `template` : un modèle de CV Premium."
        )
    )
    coffee_count: int = Field(
        default=1, ge=1, le=10, description="Nombre de cafés : 1, 2, 3 ou 5 (`coffee`)."
    )
    template_id: UUID | None = Field(default=None, description="Le modèle à acheter (`template`).")
    return_url: str | None = Field(
        default=None,
        max_length=2_048,
        description=(
            "Où revenir après le paiement : `karria://…` ou `https://karria.fr/…`. "
            "Absent : l'adresse par défaut du serveur. `422 return_url_not_allowed` sinon."
        ),
    )

    def to_order(self) -> CheckoutOrder:
        """La commande du domaine."""
        return CheckoutOrder(
            kind=ProductKind(self.payment_type),
            coffee_count=self.coffee_count,
            template_id=self.template_id,
        )


class CheckoutSessionResponse(BaseModel):
    """La session ouverte."""

    checkout_url: str = Field(description="Adresse de la page de paiement Stripe.")
    session_id: str
    payment_type: str
    mode: Literal["payment", "subscription"]
    amount_cents: int = Field(description="Montant total, en centimes d'euro, TTC.")
    currency: str = Field(default="eur")


class PaymentStatusResponse(BaseModel):
    """L'état d'une session."""

    session_id: str
    status: Literal["paid", "unpaid"]
    payment_type: str | None = None
    fulfilled: bool = Field(
        description=(
            "Vrai quand Karria a reçu la notification et livré : café compté, modèle remis, "
            "sans pub actif. C'est ce champ que l'app attend, pas `status`."
        )
    )


class AdFreeRead(BaseModel):
    """L'abonnement « sans pub » de la personne."""

    active: bool
    status: Literal["none", "active", "past_due", "canceled"]
    until: datetime | None = Field(
        description="Fin connue de l'accès : fin de période payée, ou fin de la grâce."
    )
    cancel_at_period_end: bool
    manageable: bool = Field(description="Un client existe : le portail peut s'ouvrir.")


class ThanksRead(BaseModel):
    """Les cafés offerts."""

    coffees: int
    total_cents: int
    last_at: datetime | None


class BillingMeResponse(BaseModel):
    """`GET /payments/me`."""

    ad_free: AdFreeRead
    thanks: ThanksRead
    enabled: EnabledRead

    @classmethod
    def present(cls, overview: BillingOverview, flags: PlatformFlags) -> BillingMeResponse:
        account = overview.account
        return cls(
            ad_free=AdFreeRead(
                active=overview.ad_free,
                status=account.status.value if account is not None else "none",
                until=overview.ad_free_until,
                cancel_at_period_end=account.cancel_at_period_end if account is not None else False,
                manageable=account is not None,
            ),
            thanks=ThanksRead(
                coffees=overview.thanks.coffees,
                total_cents=overview.thanks.total_cents,
                last_at=overview.thanks.last_at,
            ),
            enabled=EnabledRead.from_flags(flags),
        )


class PortalRequest(BaseModel):
    """Ouvrir le portail."""

    model_config = ConfigDict(extra="forbid")

    return_url: str | None = Field(default=None, max_length=2_048)


class PortalResponse(BaseModel):
    """L'adresse du portail Stripe (une session qui expire : à ouvrir tout de suite)."""

    url: str


class WebhookAcknowledged(BaseModel):
    """Stripe n'attend qu'un 2xx ; on dit ce qu'on a reçu."""

    received: bool = True
    type: str
    applied: bool
