"""Les dépôts du module `payments` sur une vraie base : unicité, verrous, rejeux."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from karria.modules.payments.domain import BillingAccount, ProductKind, Purchase, SubscriptionStatus
from karria.modules.payments.infrastructure import (
    TortoiseBillingAccountRepository,
    TortoisePurchaseRepository,
    TortoiseWebhookLedger,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class TestComptes:
    async def test_une_ligne_par_compte_se_sauve_et_se_relit_par_personne_ou_par_client(
        self,
    ) -> None:
        repo = TortoiseBillingAccountRepository()
        user = uuid4()
        account = BillingAccount.opened(user_id=user, customer_id="cus_1")
        await repo.save(account)
        updated = BillingAccount(
            id=account.id,
            user_id=user,
            customer_id="cus_1",
            status=SubscriptionStatus.ACTIVE,
            subscription_id="sub_1",
            current_period_ends_at=NOW + timedelta(days=30),
            synced_at=NOW,
        )
        await repo.save(updated)

        assert await repo.find_for_user(user) == updated
        assert await repo.find_by_customer("cus_1", lock=True) == updated
        assert await repo.find_for_user(uuid4()) is None
        assert await repo.find_by_customer("cus_x") is None

        await repo.delete_for_user(user)
        assert await repo.find_for_user(user) is None


class TestAchats:
    async def test_une_session_ne_s_enregistre_qu_une_fois(self) -> None:
        repo = TortoisePurchaseRepository()
        user = uuid4()
        purchase = Purchase(
            uuid4(), user, ProductKind.COFFEE, "cs_1", 499, "eur", NOW, coffee_count=2
        )

        assert await repo.add(purchase) is True
        assert (
            await repo.add(Purchase(uuid4(), user, ProductKind.COFFEE, "cs_1", 499, "eur", NOW))
            is False
        )
        assert await repo.has_session("cs_1") is True
        assert await repo.has_session("cs_2") is False
        assert await repo.of_user(user) == [purchase]

        await repo.delete_for_user(user)
        assert await repo.of_user(user) == []


class TestRegistre:
    async def test_un_evenement_ne_se_note_qu_une_fois(self) -> None:
        ledger = TortoiseWebhookLedger()
        assert await ledger.record("evt_1", "checkout.session.completed") is True
        assert await ledger.record("evt_1", "checkout.session.completed") is False
        assert await ledger.record("evt_2", "x") is True
