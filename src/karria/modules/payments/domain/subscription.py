"""L'abonnement « sans pub » d'une personne, tel que Karria le projette.

Stripe fait foi, cette ligne en est le reflet
----------------------------------------------
L'état réel — payé, en défaut, résilié — se décide chez Stripe, qui prélève,
relance et résilie. `BillingAccount` est la **projection** locale, mise à
jour à chaque notification signée, pour répondre sans réseau à la seule
question que l'app pose à chaque ouverture : « cette personne voit-elle des
publicités ? ».

Ce que le domaine décide malgré tout
-------------------------------------
- **La grâce** : un prélèvement en échec ne coupe pas le « sans pub » dans
  la minute. L'accès dure `GRACE_PERIOD` après le premier échec constaté,
  le temps pour la personne de corriger sa carte depuis le portail. Stripe
  relance pendant ce temps ; s'il résilie, l'accès tombe avec lui.
- **Une notification ancienne n'écrase pas l'abonnement courant** : une
  nouvelle tardive d'un abonnement remplacé n'éteint pas le nouveau.
- **Un seul abonnement par personne** : tant qu'un abonnement est vivant
  (actif ou en défaut), on n'en ouvre pas un second.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid4

#: Le « sans pub » survit à un prélèvement en échec pendant une semaine.
GRACE_PERIOD: Final = timedelta(days=7)
#: Longueur maximale d'un identifiant chez le prestataire (`cus_…`, `sub_…`).
PROVIDER_ID_MAX_LENGTH: Final = 255


class SubscriptionStatus(StrEnum):
    """L'état d'un abonnement, dans notre vocabulaire — plus étroit que celui de Stripe."""

    #: Jamais souscrit, ou souscription jamais aboutie.
    NONE = "none"
    #: Payé et à jour.
    ACTIVE = "active"
    #: Un prélèvement a échoué ; Stripe relance. L'accès tient le temps de la grâce.
    PAST_DUE = "past_due"
    #: Terminé. La personne peut souscrire de nouveau.
    CANCELED = "canceled"

    @property
    def is_alive(self) -> bool:
        """Un abonnement existe-t-il encore chez le prestataire ?

        `PAST_DUE` en fait partie : en ouvrir un second ferait payer deux
        fois dès que la carte est corrigée.
        """
        return self in {SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE}


@dataclass(frozen=True, slots=True)
class ProviderSubscription:
    """L'état d'un abonnement tel que le prestataire le rapporte, relu à chaque notification.

    Attributes:
        cancel_scheduled: La personne a résilié depuis le portail : l'accès
            s'arrête à `ends_at`, sans nouveau prélèvement.
        user_id: Notre identifiant de compte, relu dans la métadonnée.
    """

    subscription_id: str
    customer_id: str
    status: SubscriptionStatus
    current_period_ends_at: datetime | None
    cancel_scheduled: bool
    ends_at: datetime | None
    user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class BillingAccount:
    """La projection locale : le client Stripe d'une personne et son abonnement.

    Une ligne par compte, créée au premier paiement ouvert et gardée ensuite :
    elle porte le client Stripe, réutilisé d'un achat à l'autre pour que les
    reçus restent groupés.

    Attributes:
        past_due_since: Le premier échec de prélèvement de l'abonnement
            courant ; départ de la grâce. Effacé dès que l'abonnement est à
            nouveau à jour.
    """

    id: UUID
    user_id: UUID
    customer_id: str
    status: SubscriptionStatus = SubscriptionStatus.NONE
    subscription_id: str | None = None
    current_period_ends_at: datetime | None = None
    cancel_at_period_end: bool = False
    ends_at: datetime | None = None
    past_due_since: datetime | None = None
    synced_at: datetime | None = None

    @classmethod
    def opened(cls, *, user_id: UUID, customer_id: str) -> BillingAccount:
        """La ligne d'un compte qui ouvre son premier paiement : rien n'est souscrit."""
        return cls(id=uuid4(), user_id=user_id, customer_id=customer_id)

    def synced(self, remote: ProviderSubscription, *, at: datetime) -> BillingAccount:
        """Recopie l'état rapporté par le prestataire.

        Rend l'abonnement inchangé quand la notification porte sur un
        **autre** abonnement alors que celui en cours est vivant : c'est une
        nouvelle tardive de l'ancien, qui ne doit pas éteindre le nouveau.
        """
        other = self.subscription_id is not None and remote.subscription_id != self.subscription_id
        if other and self.status.is_alive:
            return self
        return replace(
            self,
            subscription_id=remote.subscription_id,
            customer_id=remote.customer_id or self.customer_id,
            status=remote.status,
            current_period_ends_at=remote.current_period_ends_at,
            cancel_at_period_end=remote.cancel_scheduled,
            ends_at=remote.ends_at,
            # À jour de nouveau, ou terminé : la grâce n'a plus d'objet. Un
            # autre abonnement repart aussi de zéro.
            past_due_since=(
                self.past_due_since
                if remote.status is SubscriptionStatus.PAST_DUE and not other
                else None
            ),
            synced_at=at,
        )

    def payment_failed(self, *, at: datetime) -> BillingAccount:
        """Constate un prélèvement en échec ; la grâce part du **premier**."""
        if self.past_due_since is not None:
            return self
        return replace(self, status=SubscriptionStatus.PAST_DUE, past_due_since=at)

    def ad_free_until(self, now: datetime) -> datetime | None:
        """Jusqu'à quand la personne ne voit pas de publicité, si c'est connu.

        - à jour : la fin de la période payée (`None` si le prestataire ne
          l'a pas dite — l'accès vaut quand même) ;
        - en défaut : la fin de la grâce ;
        - sinon : rien.
        """
        if self.status is SubscriptionStatus.ACTIVE:
            return self.current_period_ends_at
        if self.status is SubscriptionStatus.PAST_DUE:
            return (self.past_due_since or self.synced_at or now) + GRACE_PERIOD
        return None

    def is_ad_free(self, now: datetime) -> bool:
        """La personne est-elle sans publicité à cet instant ?"""
        if self.status is SubscriptionStatus.ACTIVE:
            return True
        if self.status is SubscriptionStatus.PAST_DUE:
            until = self.ad_free_until(now)
            return until is not None and now < until
        return False
