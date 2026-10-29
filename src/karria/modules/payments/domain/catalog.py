"""Ce qui se vend, à quel prix : le catalogue de Karria (ADR-0024).

Contrat
-------
- Les prix sont **ici**, pas dans la configuration (AGENTS § 6.3) : un prix
  est une décision commerciale, testable sans réseau. Chez Stripe, chaque
  prix est retrouvé par sa `lookup_key` et créé s'il manque ; Marc ne saisit
  rien à la main, et un prix changé ici produit un nouveau prix Stripe au
  premier achat (la clé est transférée).
- Trois produits : « Karria sans pub » (4,99 € par mois, sans engagement),
  les cafés (1, 2, 3 ou 5, à prix fixes), et les modèles de CV Premium —
  dont le prix vient de `documents`, par le port `TemplateStore`.
- Les montants sont des **entiers de centimes**. Jamais de flottant : deux
  totaux calculés différemment ne sont jamais égaux, et c'est celui affiché
  que le client réclamerait.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from karria.modules.payments.domain.errors import (
    TemplateOfferNotFoundError,
    TemplateRequiredError,
    UnknownCoffeeTierError,
)

CURRENCY: Final = "eur"

type CheckoutMode = Literal["payment", "subscription"]


class ProductKind(StrEnum):
    """Ce qu'on peut payer."""

    AD_FREE = "ad_free"
    COFFEE = "coffee"
    TEMPLATE = "template"


@dataclass(frozen=True, slots=True)
class PriceSpec:
    """Un prix tel que Karria le décide, et tel que Stripe doit le porter.

    Attributes:
        lookup_key: La clé stable par laquelle le prix est retrouvé chez le
            prestataire. Elle ne change pas quand le montant change : la clé
            est transférée au nouveau prix.
        interval: `month` pour un abonnement, `None` pour un paiement unique.
    """

    lookup_key: str
    product_name: str
    amount_cents: int
    interval: Literal["month"] | None = None
    currency: str = CURRENCY

    @property
    def recurring(self) -> bool:
        """Un abonnement, et non un paiement unique."""
        return self.interval is not None


#: « Karria sans pub » : 4,99 € par mois, renouvelé chaque mois, résiliable à
#: tout moment depuis le portail du prestataire. Aucun essai.
AD_FREE_PLAN: Final = PriceSpec(
    lookup_key="karria_ad_free_monthly",
    product_name="Karria sans pub",
    amount_cents=499,
    interval="month",
)

#: Les paliers de cafés : nombre de tasses → prix. Pas de montant libre : un
#: champ libre invite les fautes de frappe et les montants dérisoires.
COFFEE_TIERS: Final[dict[int, int]] = {1: 299, 2: 499, 3: 799, 5: 1299}


def coffee_price(count: int) -> PriceSpec:
    """Le prix d'un palier de cafés.

    Raises:
        UnknownCoffeeTierError: Un nombre hors des paliers.
    """
    amount = COFFEE_TIERS.get(count)
    if amount is None:
        raise UnknownCoffeeTierError(count)
    plural = "s" if count > 1 else ""
    return PriceSpec(
        lookup_key=f"karria_coffee_{count}",
        product_name=f"{count} café{plural} pour l'équipe Karria",
        amount_cents=amount,
    )


@dataclass(frozen=True, slots=True)
class PremiumOffer:
    """Un modèle de CV Premium en vente, tel que `documents` le dit."""

    template_id: UUID
    label: str
    amount_cents: int


def template_price(offer: PremiumOffer) -> PriceSpec:
    """Le prix Stripe d'un modèle : une clé par modèle, le montant venu de `documents`."""
    return PriceSpec(
        lookup_key=f"karria_template_{offer.template_id}",
        product_name=f"Modèle de CV {offer.label}",
        amount_cents=offer.amount_cents,
    )


@dataclass(frozen=True, slots=True)
class CheckoutOrder:
    """Ce que la personne veut payer, tel que l'app le demande."""

    kind: ProductKind
    coffee_count: int = 1
    template_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PricedOrder:
    """La commande décidée : le prix, le mode de paiement, les métadonnées Stripe."""

    kind: ProductKind
    spec: PriceSpec
    mode: CheckoutMode
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def amount_cents(self) -> int:
        """Le montant total, en centimes."""
        return self.spec.amount_cents


def price(order: CheckoutOrder, *, offer: PremiumOffer | None = None) -> PricedOrder:
    """Décide le prix d'une commande.

    Args:
        offer: Le modèle en vente, quand la commande en désigne un. `None`
            vaut « aucun modèle Premium à cet identifiant ».

    Raises:
        UnknownCoffeeTierError: Un nombre de cafés hors des paliers.
        TemplateRequiredError: Un achat de modèle sans identifiant.
        TemplateOfferNotFoundError: Un modèle qui n'est pas en vente.
    """
    if order.kind is ProductKind.AD_FREE:
        return PricedOrder(order.kind, AD_FREE_PLAN, "subscription", {"kind": order.kind.value})
    if order.kind is ProductKind.COFFEE:
        spec = coffee_price(order.coffee_count)
        return PricedOrder(
            order.kind,
            spec,
            "payment",
            {"kind": order.kind.value, "coffee_count": str(order.coffee_count)},
        )
    if order.template_id is None:
        raise TemplateRequiredError()
    if offer is None or offer.template_id != order.template_id:
        raise TemplateOfferNotFoundError()
    return PricedOrder(
        order.kind,
        template_price(offer),
        "payment",
        {"kind": order.kind.value, "template_id": str(offer.template_id)},
    )


@dataclass(frozen=True, slots=True)
class Catalog:
    """Ce que l'app affiche : le plan sans pub, les cafés, les modèles en vente."""

    ad_free: PriceSpec
    coffees: tuple[tuple[int, PriceSpec], ...]
    templates: tuple[PremiumOffer, ...]
    currency: str = CURRENCY


def catalog(templates: list[PremiumOffer]) -> Catalog:
    """Le catalogue complet, les cafés par taille croissante."""
    return Catalog(
        ad_free=AD_FREE_PLAN,
        coffees=tuple((count, coffee_price(count)) for count in sorted(COFFEE_TIERS)),
        templates=tuple(templates),
    )
