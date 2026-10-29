"""Les règles de l'encaissement : ce qui se vend, à quel prix, et où l'on revient.

Rien d'autre que des décisions pures : une commande entre, un montant et un
libellé sortent ; une adresse de retour entre, elle est admise ou refusée.
Le fournisseur de paiement n'apparaît nulle part ici.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

from karria.shared.errors.exceptions import (
    DependencyUnavailableError,
    ErrorCode,
    InvalidInputError,
)

CURRENCY: Final = "eur"
#: Un café = 5,00 €. Le nombre de cafés est borné : au-delà, c'est une
#: erreur de saisie, pas un soutien.
COFFEE_PRICE_CENTS: Final = 500
MAX_COFFEES: Final = 10
#: Un montant libre : au moins 1,50 € (sous ce seuil, les frais mangent le
#: don), au plus 500 € (au-delà, une faute de frappe).
MIN_CUSTOM_AMOUNT_CENTS: Final = 150
MAX_CUSTOM_AMOUNT_CENTS: Final = 50_000
#: Un modèle de CV Premium. Un prix unique pour l'instant ; le jour où le
#: catalogue porte un prix par modèle, c'est `documents` qui le dira.
TEMPLATE_PRICE_CENTS: Final = 499
#: Les seules destinations admises après le paiement : le site de Karria, ou
#: l'application elle-même par son schéma. Une URL quelconque ferait de
#: Stripe un relais d'hameçonnage aux couleurs de Karria.
ALLOWED_RETURN_HOSTS: Final[frozenset[str]] = frozenset({"karria.fr", "www.karria.fr"})
APP_SCHEME: Final = "karria"


class PaymentType(StrEnum):
    """Ce qu'on peut payer."""

    COFFEE = "coffee"
    TEMPLATE = "template"


class PaymentsUnavailableError(DependencyUnavailableError):
    """Le prestataire de paiement n'est pas configuré ou ne répond pas."""

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_FAILURE

    def __init__(self, reason: str = "payments_unavailable") -> None:
        super().__init__("Stripe", reason)


@dataclass(frozen=True, slots=True)
class CheckoutOrder:
    """Ce que la personne veut payer, tel que l'app le demande."""

    payment_type: PaymentType
    coffee_count: int = 1
    custom_amount_cents: int | None = None
    template_code: str | None = None


@dataclass(frozen=True, slots=True)
class PricedOrder:
    """Le montant décidé et son libellé, en centimes d'euro."""

    amount_cents: int
    label: str
    currency: str = CURRENCY


def price(order: CheckoutOrder) -> PricedOrder:
    """Le prix d'une commande. `InvalidInputError` hors des bornes."""
    if order.payment_type is PaymentType.TEMPLATE:
        code = (order.template_code or "").strip()
        label = f"Modèle de CV Premium — {code}" if code else "Modèle de CV Premium"
        return PricedOrder(TEMPLATE_PRICE_CENTS, label)
    if order.custom_amount_cents is not None:
        if not MIN_CUSTOM_AMOUNT_CENTS <= order.custom_amount_cents <= MAX_CUSTOM_AMOUNT_CENTS:
            raise InvalidInputError(
                "Le montant libre est hors des bornes.",
                details={"reason": "amount_out_of_bounds", "field": "custom_amount_eur"},
            )
        euros = order.custom_amount_cents / 100
        return PricedOrder(order.custom_amount_cents, f"Soutien à l'équipe Karria — {euros:.2f} €")
    if not 1 <= order.coffee_count <= MAX_COFFEES:
        raise InvalidInputError(
            "Le nombre de cafés est hors des bornes.",
            details={"reason": "coffee_count_out_of_bounds", "field": "coffee_count"},
        )
    plural = "s" if order.coffee_count > 1 else ""
    return PricedOrder(
        order.coffee_count * COFFEE_PRICE_CENTS,
        f"Soutien à l'équipe Karria ({order.coffee_count} café{plural})",
    )


def validate_return_url(url: str) -> str:
    """L'adresse de retour, si elle vise Karria ; `InvalidInputError` sinon."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme == APP_SCHEME and not parts.username and not parts.password:
        return url.strip()
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host in ALLOWED_RETURN_HOSTS and not parts.username:
        return url.strip()
    raise InvalidInputError(
        "L'adresse de retour doit viser Karria.",
        details={"reason": "return_url_not_allowed", "field": "return_url"},
    )


__all__ = [
    "ALLOWED_RETURN_HOSTS",
    "APP_SCHEME",
    "COFFEE_PRICE_CENTS",
    "CURRENCY",
    "MAX_COFFEES",
    "MAX_CUSTOM_AMOUNT_CENTS",
    "MIN_CUSTOM_AMOUNT_CENTS",
    "TEMPLATE_PRICE_CENTS",
    "CheckoutOrder",
    "PaymentType",
    "PaymentsUnavailableError",
    "PricedOrder",
    "price",
    "validate_return_url",
]
