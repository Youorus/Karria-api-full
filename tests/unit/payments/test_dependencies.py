"""Le câblage du module `payments` : ses replis qui refusent, et ce que la configuration décide."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from karria.modules.payments.api.dependencies import (
    NoAccountDirectory,
    NoTemplateStore,
    build_forget_user,
    build_gateway,
    build_handle_webhook,
    get_account_repository,
    get_platform_flags,
    get_purchase_repository,
    get_webhook_ledger,
)
from karria.modules.payments.application import ForgetUser, HandleWebhook
from karria.modules.payments.domain import PaymentsUnavailableError
from karria.modules.payments.infrastructure import (
    StripeGateway,
    TortoiseBillingAccountRepository,
    TortoisePurchaseRepository,
    TortoiseWebhookLedger,
)
from karria.shared.config import Settings
from karria.shared.config.settings import PaymentsSettings
from payments.fakes import FakeTemplateStore

pytestmark = pytest.mark.unit


def settings(secret: str = "", *, ios: bool = True, android: bool = True) -> Settings:
    return Settings(
        payments=PaymentsSettings(
            secret_key=SecretStr(secret), ios_enabled=ios, android_enabled=android
        )
    )


class TestReplis:
    async def test_sans_annuaire_aucune_adresse(self) -> None:
        assert await NoAccountDirectory().email_of(uuid4()) is None

    async def test_sans_vitrine_rien_en_vente_et_la_remise_refuse(self) -> None:
        store = NoTemplateStore()
        assert await store.premium_offers() == []
        assert await store.premium_offer(uuid4()) is None
        assert await store.owns(uuid4(), uuid4()) is False
        with pytest.raises(PaymentsUnavailableError) as caught:
            await store.grant(uuid4(), uuid4())
        assert caught.value.details["reason"] == "template_store_unwired"


class TestConfiguration:
    def test_sans_cle_aucun_prestataire_et_tout_est_eteint(self) -> None:
        assert build_gateway(settings()) is None
        flags = get_platform_flags(settings())
        assert (flags.configured, flags.ios_enabled, flags.android_enabled) == (
            False,
            False,
            False,
        )

    def test_avec_une_cle_un_client_stripe_par_processus(self) -> None:
        first = build_gateway(settings("sk_test_x"))
        second = build_gateway(settings("sk_test_x"))
        assert isinstance(first, StripeGateway)
        assert first is second
        flags = get_platform_flags(settings("sk_test_x", ios=False))
        assert (flags.configured, flags.ios_enabled, flags.android_enabled) == (True, False, True)

    def test_les_assemblages_hors_requete(self) -> None:
        assert isinstance(build_forget_user(settings()), ForgetUser)
        assert isinstance(build_handle_webhook(settings(), FakeTemplateStore()), HandleWebhook)

    def test_les_depots_reels_sont_ceux_de_tortoise(self) -> None:
        assert isinstance(get_account_repository(), TortoiseBillingAccountRepository)
        assert isinstance(get_purchase_repository(), TortoisePurchaseRepository)
        assert isinstance(get_webhook_ledger(), TortoiseWebhookLedger)
