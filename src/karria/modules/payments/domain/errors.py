"""Erreurs du module `payments`.

Chaque erreur porte dans `details.reason` la raison fine que l'application
branche, et s'appuie sur un `ErrorCode` existant pour le statut HTTP. Rien
ici ne nomme HTTP ni Stripe.
"""

from __future__ import annotations

from typing import ClassVar

from karria.shared.errors.exceptions import (
    DependencyUnavailableError,
    DomainError,
    EntityAlreadyExistsError,
    ErrorCode,
    InvalidInputError,
)


class PaymentsUnavailableError(DependencyUnavailableError):
    """Le prestataire de paiement n'est pas configuré ou ne répond pas."""

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_FAILURE

    def __init__(self, reason: str = "payments_unavailable") -> None:
        super().__init__("Stripe", reason)


class SubscriptionAlreadyActiveError(EntityAlreadyExistsError):
    """Un abonnement court déjà (actif ou en défaut de paiement) : pas un second."""

    def __init__(self, *, payment_failed: bool) -> None:
        super().__init__("Abonnement", "user_id", "actif")
        self.details["reason"] = "subscription_already_active"
        self.details["payment_failed"] = payment_failed


class TemplateAlreadyOwnedError(EntityAlreadyExistsError):
    """Le modèle est déjà à la personne : rien à payer."""

    def __init__(self) -> None:
        super().__init__("Modèle", "template_id", "possédé")
        self.details["reason"] = "template_already_owned"


class TemplateOfferNotFoundError(DomainError):
    """Le modèle n'existe pas, n'est pas publié, ou n'est pas payant."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__(
            "Ce modèle de CV n'est pas en vente.", details={"reason": "template_not_found"}
        )


class BillingAccountNotFoundError(DomainError):
    """Aucun client n'existe chez le prestataire pour ce compte : rien à gérer."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__(
            "Aucun abonnement ni paiement connu pour ce compte.",
            details={"reason": "billing_account_not_found"},
        )


class UnknownCoffeeTierError(InvalidInputError):
    """Un nombre de cafés hors des paliers proposés."""

    def __init__(self, count: int) -> None:
        super().__init__(
            "Ce nombre de cafés n'est pas proposé.",
            details={
                "reason": "coffee_count_out_of_bounds",
                "field": "coffee_count",
                "count": count,
            },
        )


class TemplateRequiredError(InvalidInputError):
    """Un achat de modèle sans dire lequel."""

    def __init__(self) -> None:
        super().__init__(
            "Le modèle à acheter manque.",
            details={"reason": "template_id_required", "field": "template_id"},
        )
