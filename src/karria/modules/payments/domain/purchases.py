"""Les paiements uniques : un café offert, un modèle acheté — et le merci qu'on leur doit.

Un achat est enregistré **une fois par session de paiement** : Stripe réémet
ses notifications, et le second passage ne doit ni compter un café de plus
ni redonner un modèle. L'identifiant de session est la clé d'idempotence.

Le merci (`ThankYou`) est une décision du domaine : ce qu'on dit, par quel
canal, et ce qui ne s'y dit pas. `shared.push` est importé ici parce que le
contrat push est pur stdlib et sans métier (ADR-0022), comme `offers` le fait.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from karria.modules.payments.domain.catalog import ProductKind
from karria.shared.push import PushKind, PushMessage


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


#: Le merci sur le téléphone, par produit. Court, sans emoji (la ligne du
#: dépôt : `support.PUSH_TITLE`), tutoiement comme le reste des notifications.
#: Le corps ne cite ni montant ni modèle : l'écran verrouillé d'un téléphone
#: prêté n'est pas à nous.
COFFEE_PUSH_TITLE: Final = "Merci pour le café"
COFFEE_PUSH_BODY: Final = "Ton soutien est bien arrivé. C'est lui qui fait tourner Karria."
TEMPLATE_PUSH_TITLE: Final = "Ton modèle de CV est à toi"
TEMPLATE_PUSH_BODY: Final = "Il est dans ta galerie. Ouvre l'atelier pour l'appliquer à ton CV."
AD_FREE_PUSH_TITLE: Final = "Karria sans pub est actif"
AD_FREE_PUSH_BODY: Final = "Plus aucune publicité. Merci de soutenir Karria."


@dataclass(frozen=True, slots=True)
class ThankYou:
    """Ce qu'on doit dire à une personne après un paiement livré, et par quels canaux.

    Né **dans** la transaction qui livre (achat enregistré, abonnement activé)
    et remis **après** elle : l'e-mail et la notification ne doivent ni
    retarder ni faire échouer la réponse au prestataire. Une seule fois par
    livraison : l'achat n'est enregistré qu'une fois par session, et
    l'abonnement ne « s'active » qu'en passant d'un état sans abonnement
    vivant à `ACTIVE`.

    Attributes:
        template_label: Le nom du modèle tel que `documents` le vend ; `None`
            tant qu'il n'a pas été relu, ou si le modèle a disparu du
            catalogue (le courriel parle alors du « modèle »).
    """

    user_id: UUID
    kind: ProductKind
    amount_cents: int
    currency: str
    coffee_count: int | None = None
    template_id: UUID | None = None
    template_label: str | None = None

    @classmethod
    def for_purchase(cls, purchase: Purchase) -> ThankYou:
        """Le merci d'un paiement unique : café ou modèle."""
        return cls(
            user_id=purchase.user_id,
            kind=purchase.kind,
            amount_cents=purchase.amount_cents,
            currency=purchase.currency,
            coffee_count=purchase.coffee_count,
            template_id=purchase.template_id,
        )

    @classmethod
    def for_subscription(cls, user_id: UUID, *, amount_cents: int, currency: str) -> ThankYou:
        """Le merci d'un abonnement « sans pub » qui vient de s'activer."""
        return cls(
            user_id=user_id, kind=ProductKind.AD_FREE, amount_cents=amount_cents, currency=currency
        )

    @property
    def amount(self) -> str:
        """Le montant tel qu'il s'écrit dans un courriel : « 2,99 € »."""
        return format_amount(self.amount_cents, self.currency)

    @property
    def coffee_label(self) -> str:
        """« un café » ou « 3 cafés », pour le courriel."""
        count = self.coffee_count or 1
        return "un café" if count == 1 else f"{count} cafés"

    def push(self) -> PushMessage:
        """La notification, au contrat push : `purchase`, avec l'identifiant du modèle s'il y en a un."""
        if self.kind is ProductKind.TEMPLATE:
            return PushMessage(
                kind=PushKind.PURCHASE,
                title=TEMPLATE_PUSH_TITLE,
                body=TEMPLATE_PUSH_BODY,
                target_id=self.template_id,
            )
        if self.kind is ProductKind.AD_FREE:
            return PushMessage(
                kind=PushKind.PURCHASE, title=AD_FREE_PUSH_TITLE, body=AD_FREE_PUSH_BODY
            )
        return PushMessage(kind=PushKind.PURCHASE, title=COFFEE_PUSH_TITLE, body=COFFEE_PUSH_BODY)


def format_amount(cents: int, currency: str) -> str:
    """« 2,99 € » pour 299 centimes d'euro ; « 12,99 USD » pour une autre devise.

    Écrit à la française (virgule, espace insécable avant le symbole) : les
    courriels sont rendus par langue, mais un montant en euros se lit ainsi
    des deux côtés sans ambiguïté.
    """
    euros, rest = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    symbol = "€" if currency.lower() == "eur" else currency.upper()
    return f"{sign}{euros},{rest:02d} {symbol}"
