"""Câblage du module `payments` : la configuration → le prestataire → les cas d'usage.

Deux ports regardent vers d'autres modules et **refusent** par défaut :
`get_account_directory` (← `identity`) ne connaît aucune adresse,
`get_template_store` (← `documents`) ne vend rien et ne remet rien. C'est
`bootstrap/wiring.py` qui les branche (règle 4).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from karria.modules.payments.application import (
    AccountDirectory,
    BillingAccountRepository,
    BillingGateway,
    ForgetUser,
    GetBillingOverview,
    GetCatalog,
    GetPaymentStatus,
    HandleWebhook,
    OpenCheckout,
    OpenPortal,
    PlatformFlags,
    PurchaseRepository,
    TemplateStore,
    WebhookLedger,
)
from karria.modules.payments.domain import PaymentsUnavailableError, PremiumOffer
from karria.modules.payments.infrastructure import (
    StripeGateway,
    TortoiseBillingAccountRepository,
    TortoisePurchaseRepository,
    TortoiseWebhookLedger,
)
from karria.shared.clock import Clock, SystemClock
from karria.shared.config import Settings
from karria.shared.database import TortoiseUnitOfWork
from karria.shared.http.dependencies import SettingsDep, UnitOfWorkDep


class NoAccountDirectory:
    """Le refus par défaut : aucune adresse connue (le client Stripe naît sans e-mail)."""

    async def email_of(self, user_id: UUID) -> str | None:
        del user_id
        return None


class NoTemplateStore:
    """Le refus par défaut : rien en vente, rien de possédé, rien de remis.

    `grant` lève : un modèle payé qu'on ne pourrait pas remettre doit faire
    échouer le webhook, pour que Stripe le réémette une fois le branchement fait.
    """

    async def premium_offers(self) -> list[PremiumOffer]:
        return []

    async def premium_offer(self, template_id: UUID) -> PremiumOffer | None:
        del template_id
        return None

    async def owns(self, user_id: UUID, template_id: UUID) -> bool:
        del user_id, template_id
        return False

    async def grant(self, user_id: UUID, template_id: UUID) -> None:
        del user_id, template_id
        raise PaymentsUnavailableError("template_store_unwired")


@lru_cache(maxsize=2)
def _gateway(secret_key: str, webhook_secret: str, automatic_tax: bool) -> StripeGateway:
    """Un client Stripe par processus (connexions et prix résolus réutilisés)."""
    return StripeGateway(secret_key, webhook_secret, automatic_tax=automatic_tax)


def build_gateway(settings: Settings) -> BillingGateway | None:
    """Le prestataire, ou `None` sans clé : les cas d'usage répondent alors 503."""
    secret = settings.payments.secret_key.get_secret_value()
    if not secret:
        return None
    return _gateway(
        secret,
        settings.payments.webhook_secret.get_secret_value(),
        settings.payments.automatic_tax,
    )


def get_payment_gateway(settings: SettingsDep) -> BillingGateway | None:
    return build_gateway(settings)


def get_platform_flags(settings: SettingsDep) -> PlatformFlags:
    """L'interrupteur par plateforme, éteint d'office sans prestataire."""
    return PlatformFlags(
        ios=settings.payments.ios_enabled,
        android=settings.payments.android_enabled,
        configured=bool(settings.payments.secret_key.get_secret_value()),
    )


def get_clock() -> Clock:
    return SystemClock()


def get_account_repository() -> BillingAccountRepository:
    return TortoiseBillingAccountRepository()


def get_purchase_repository() -> PurchaseRepository:
    return TortoisePurchaseRepository()


def get_webhook_ledger() -> WebhookLedger:
    return TortoiseWebhookLedger()


def get_account_directory() -> AccountDirectory:
    """Point de substitution : `bootstrap` le branche sur `identity`."""
    return NoAccountDirectory()


def get_template_store() -> TemplateStore:
    """Point de substitution : `bootstrap` le branche sur `documents`."""
    return NoTemplateStore()


GatewayDep = Annotated[BillingGateway | None, Depends(get_payment_gateway)]
FlagsDep = Annotated[PlatformFlags, Depends(get_platform_flags)]
ClockDep = Annotated[Clock, Depends(get_clock)]
AccountsDep = Annotated[BillingAccountRepository, Depends(get_account_repository)]
PurchasesDep = Annotated[PurchaseRepository, Depends(get_purchase_repository)]
LedgerDep = Annotated[WebhookLedger, Depends(get_webhook_ledger)]
DirectoryDep = Annotated[AccountDirectory, Depends(get_account_directory)]
TemplateStoreDep = Annotated[TemplateStore, Depends(get_template_store)]


def get_get_catalog(templates: TemplateStoreDep) -> GetCatalog:
    return GetCatalog(templates)


def get_open_checkout(
    gateway: GatewayDep,
    accounts: AccountsDep,
    templates: TemplateStoreDep,
    directory: DirectoryDep,
    clock: ClockDep,
) -> OpenCheckout:
    return OpenCheckout(
        gateway=gateway, accounts=accounts, templates=templates, directory=directory, clock=clock
    )


def get_payment_status(
    gateway: GatewayDep, accounts: AccountsDep, purchases: PurchasesDep, clock: ClockDep
) -> GetPaymentStatus:
    return GetPaymentStatus(gateway=gateway, accounts=accounts, purchases=purchases, clock=clock)


def get_billing_overview(
    accounts: AccountsDep, purchases: PurchasesDep, clock: ClockDep, flags: FlagsDep
) -> GetBillingOverview:
    return GetBillingOverview(
        accounts=accounts, purchases=purchases, clock=clock, configured=flags.configured
    )


def get_open_portal(gateway: GatewayDep, accounts: AccountsDep) -> OpenPortal:
    return OpenPortal(gateway=gateway, accounts=accounts)


def get_handle_webhook(
    *,
    gateway: GatewayDep,
    accounts: AccountsDep,
    purchases: PurchasesDep,
    ledger: LedgerDep,
    templates: TemplateStoreDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> HandleWebhook:
    return HandleWebhook(
        gateway=gateway,
        accounts=accounts,
        purchases=purchases,
        ledger=ledger,
        templates=templates,
        clock=clock,
        uow=uow,
    )


def build_forget_user(settings: Settings) -> ForgetUser:
    """L'oubli d'un compte supprimé, branché sur `AccountDeleted` par `bootstrap/wiring.py`."""
    return ForgetUser(
        gateway=build_gateway(settings),
        accounts=get_account_repository(),
        purchases=get_purchase_repository(),
    )


def build_handle_webhook(settings: Settings, templates: TemplateStore) -> HandleWebhook:
    """Le traitement d'une notification hors requête (scripts, rejeu)."""
    return HandleWebhook(
        gateway=build_gateway(settings),
        accounts=get_account_repository(),
        purchases=get_purchase_repository(),
        ledger=get_webhook_ledger(),
        templates=templates,
        clock=get_clock(),
        uow=TortoiseUnitOfWork(),
    )


GetCatalogDep = Annotated[GetCatalog, Depends(get_get_catalog)]
OpenCheckoutDep = Annotated[OpenCheckout, Depends(get_open_checkout)]
PaymentStatusDep = Annotated[GetPaymentStatus, Depends(get_payment_status)]
BillingOverviewDep = Annotated[GetBillingOverview, Depends(get_billing_overview)]
OpenPortalDep = Annotated[OpenPortal, Depends(get_open_portal)]
HandleWebhookDep = Annotated[HandleWebhook, Depends(get_handle_webhook)]
