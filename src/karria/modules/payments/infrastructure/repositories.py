"""Les dépôts du module `payments` sur Tortoise. Rien n'en sort qu'un objet du domaine."""

from __future__ import annotations

from uuid import UUID

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from karria.modules.payments.domain import BillingAccount, Purchase
from karria.modules.payments.infrastructure.models import (
    BillingAccountModel,
    PurchaseModel,
    WebhookEventModel,
)


def _account(row: BillingAccountModel) -> BillingAccount:
    return BillingAccount(
        id=row.id,
        user_id=row.user_id,
        customer_id=row.customer_id,
        status=row.status,
        subscription_id=row.subscription_id,
        current_period_ends_at=row.current_period_ends_at,
        cancel_at_period_end=row.cancel_at_period_end,
        ends_at=row.ends_at,
        past_due_since=row.past_due_since,
        synced_at=row.synced_at,
    )


class TortoiseBillingAccountRepository:
    """Implémente `BillingAccountRepository`."""

    async def find_for_user(self, user_id: UUID, *, lock: bool = False) -> BillingAccount | None:
        query = BillingAccountModel.filter(user_id=user_id)
        if lock:
            query = query.select_for_update()
        row = await query.first()
        return _account(row) if row is not None else None

    async def find_by_customer(
        self, customer_id: str, *, lock: bool = False
    ) -> BillingAccount | None:
        query = BillingAccountModel.filter(customer_id=customer_id)
        if lock:
            query = query.select_for_update()
        row = await query.first()
        return _account(row) if row is not None else None

    async def save(self, account: BillingAccount) -> None:
        await BillingAccountModel.update_or_create(
            defaults={
                "id": account.id,
                "customer_id": account.customer_id,
                "status": account.status,
                "subscription_id": account.subscription_id,
                "current_period_ends_at": account.current_period_ends_at,
                "cancel_at_period_end": account.cancel_at_period_end,
                "ends_at": account.ends_at,
                "past_due_since": account.past_due_since,
                "synced_at": account.synced_at,
            },
            user_id=account.user_id,
        )

    async def delete_for_user(self, user_id: UUID) -> None:
        await BillingAccountModel.filter(user_id=user_id).delete()


def _purchase(row: PurchaseModel) -> Purchase:
    return Purchase(
        id=row.id,
        user_id=row.user_id,
        kind=row.kind,
        session_id=row.session_id,
        amount_cents=row.amount_cents,
        currency=row.currency,
        paid_at=row.paid_at,
        coffee_count=row.coffee_count,
        template_id=row.template_id,
    )


class TortoisePurchaseRepository:
    """Implémente `PurchaseRepository`."""

    async def add(self, purchase: Purchase) -> bool:
        # Un point de sauvegarde : l'unicité de `session_id` fait le tri des
        # rejeux sans casser la transaction englobante du webhook.
        try:
            async with in_transaction():
                await PurchaseModel.create(
                    id=purchase.id,
                    user_id=purchase.user_id,
                    kind=purchase.kind,
                    session_id=purchase.session_id,
                    amount_cents=purchase.amount_cents,
                    currency=purchase.currency,
                    coffee_count=purchase.coffee_count,
                    template_id=purchase.template_id,
                    paid_at=purchase.paid_at,
                )
        except IntegrityError:
            return False
        return True

    async def has_session(self, session_id: str) -> bool:
        return await PurchaseModel.filter(session_id=session_id).exists()

    async def of_user(self, user_id: UUID) -> list[Purchase]:
        rows = await PurchaseModel.filter(user_id=user_id).order_by("-paid_at")
        return [_purchase(r) for r in rows]

    async def delete_for_user(self, user_id: UUID) -> None:
        await PurchaseModel.filter(user_id=user_id).delete()


class TortoiseWebhookLedger:
    """Implémente `WebhookLedger`."""

    async def record(self, event_id: str, event_type: str) -> bool:
        try:
            async with in_transaction():
                await WebhookEventModel.create(event_id=event_id, event_type=event_type)
        except IntegrityError:
            return False
        return True
