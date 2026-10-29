"""Persistance du module `payments` : la projection, les achats, le registre des notifications.

Aucune clé étrangère vers `identity_users` : ce module ne connaît pas les
comptes (règle 4). L'effacement suit l'événement `AccountDeleted` (ADR-0015).
"""

from __future__ import annotations

from uuid import UUID

from tortoise import fields

from karria.modules.payments.domain import PROVIDER_ID_MAX_LENGTH, ProductKind, SubscriptionStatus
from karria.shared.database import BaseDBModel


class BillingAccountModel(BaseDBModel):
    """Un client Stripe et l'abonnement « sans pub » d'une personne (une ligne par compte)."""

    user_id: UUID = fields.UUIDField(unique=True)
    #: **Unique** : c'est par lui qu'une notification retrouve le compte.
    customer_id = fields.CharField(max_length=PROVIDER_ID_MAX_LENGTH, unique=True)
    status = fields.CharEnumField(
        SubscriptionStatus, max_length=16, default=SubscriptionStatus.NONE
    )
    subscription_id = fields.CharField(max_length=PROVIDER_ID_MAX_LENGTH, null=True, db_index=True)
    current_period_ends_at = fields.DatetimeField(null=True)
    cancel_at_period_end = fields.BooleanField(default=False)
    ends_at = fields.DatetimeField(null=True)
    past_due_since = fields.DatetimeField(null=True)
    synced_at = fields.DatetimeField(null=True)

    class Meta:
        table = "payments_accounts"


class PurchaseModel(BaseDBModel):
    """Un paiement unique confirmé : un café, un modèle."""

    user_id: UUID = fields.UUIDField(db_index=True)
    kind = fields.CharEnumField(ProductKind, max_length=16)
    #: La session Stripe qui l'a produit : la clé d'idempotence des rejeux.
    session_id = fields.CharField(max_length=PROVIDER_ID_MAX_LENGTH, unique=True)
    amount_cents = fields.IntField()
    currency = fields.CharField(max_length=3)
    coffee_count = fields.IntField(null=True)
    template_id: UUID | None = fields.UUIDField(null=True)
    paid_at = fields.DatetimeField()

    class Meta:
        table = "payments_purchases"


class WebhookEventModel(BaseDBModel):
    """Une notification déjà traitée. Aucune donnée personnelle : la ligne survit au compte."""

    event_id = fields.CharField(max_length=PROVIDER_ID_MAX_LENGTH, unique=True)
    event_type = fields.CharField(max_length=80)

    class Meta:
        table = "payments_webhook_events"
