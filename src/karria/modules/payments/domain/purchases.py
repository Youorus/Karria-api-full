"""Les paiements uniques : un café offert, un modèle acheté — et le merci qu'on leur doit.

Un achat est enregistré **une fois par session de paiement** : Stripe réémet
ses notifications, et le second passage ne doit ni compter un café de plus
ni redonner un modèle. L'identifiant de session est la clé d'idempotence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from karria.modules.payments.domain.catalog import ProductKind


@dataclass(frozen=True, slots=True)
class Purchase:
    """Un paiement unique confirmé par le prestataire."""

    id: UUID
    user_id: UUID
    kind: ProductKind
    session_id: str
    amount_cents: int
    currency: str
    paid_at: datetime
    coffee_count: int | None = None
    template_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Gratitude:
    """Ce qu'on doit à une personne : ses cafés, et le dernier."""

    coffees: int = 0
    total_cents: int = 0
    last_at: datetime | None = None


def gratitude(purchases: list[Purchase]) -> Gratitude:
    """Additionne les cafés offerts — pas les modèles, qui sont des achats."""
    coffees = [p for p in purchases if p.kind is ProductKind.COFFEE]
    if not coffees:
        return Gratitude()
    return Gratitude(
        coffees=sum(p.coffee_count or 0 for p in coffees),
        total_cents=sum(p.amount_cents for p in coffees),
        last_at=max(p.paid_at for p in coffees),
    )
