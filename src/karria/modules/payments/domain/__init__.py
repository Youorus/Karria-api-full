"""Les règles de l'encaissement : ce qui se vend, à quel prix, qui est sans pub, où l'on revient.

Rien d'autre que des décisions pures. Le fournisseur de paiement n'apparaît
nulle part ici.
"""

from karria.modules.payments.domain.catalog import (
    AD_FREE_PLAN,
    COFFEE_TIERS,
    CURRENCY,
    Catalog,
    CheckoutMode,
    CheckoutOrder,
    PremiumOffer,
    PricedOrder,
    PriceSpec,
    ProductKind,
    catalog,
    coffee_price,
    price,
    template_price,
)
from karria.modules.payments.domain.errors import (
    BillingAccountNotFoundError,
    PaymentsUnavailableError,
    SubscriptionAlreadyActiveError,
    TemplateAlreadyOwnedError,
    TemplateOfferNotFoundError,
    TemplateRequiredError,
    UnknownCoffeeTierError,
)
from karria.modules.payments.domain.purchases import Gratitude, Purchase, gratitude
from karria.modules.payments.domain.returns import (
    ALLOWED_RETURN_HOSTS,
    APP_SCHEME,
    validate_return_url,
    with_query,
)
from karria.modules.payments.domain.subscription import (
    GRACE_PERIOD,
    PROVIDER_ID_MAX_LENGTH,
    BillingAccount,
    ProviderSubscription,
    SubscriptionStatus,
)

__all__ = [
    "AD_FREE_PLAN",
    "ALLOWED_RETURN_HOSTS",
    "APP_SCHEME",
    "COFFEE_TIERS",
    "CURRENCY",
    "GRACE_PERIOD",
    "PROVIDER_ID_MAX_LENGTH",
    "BillingAccount",
    "BillingAccountNotFoundError",
    "Catalog",
    "CheckoutMode",
    "CheckoutOrder",
    "Gratitude",
    "PaymentsUnavailableError",
    "PremiumOffer",
    "PriceSpec",
    "PricedOrder",
    "ProductKind",
    "ProviderSubscription",
    "Purchase",
    "SubscriptionAlreadyActiveError",
    "SubscriptionStatus",
    "TemplateAlreadyOwnedError",
    "TemplateOfferNotFoundError",
    "TemplateRequiredError",
    "UnknownCoffeeTierError",
    "catalog",
    "coffee_price",
    "gratitude",
    "price",
    "template_price",
    "validate_return_url",
    "with_query",
]
