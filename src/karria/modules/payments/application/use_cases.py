"""Cas d'usage de l'encaissement : le catalogue, ouvrir, relire, gérer, écouter, oublier.

Chaque cas d'usage ne connaît que des ports. Sans prestataire configuré, il
n'y a **pas** de mode démonstration : la route répond 503. Un faux « payé »
rendu à un client qui s'y fierait pour débloquer un modèle serait une faille.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from karria.modules.payments.application.ports import (
    AccountDirectory,
    BillingAccountRepository,
    BillingEvent,
    BillingEventKind,
    BillingGateway,
    CheckoutRecord,
    CheckoutRequest,
    CheckoutSession,
    PurchaseRepository,
    TemplateStore,
    WebhookLedger,
    WebhookSignatureError,
)
from karria.modules.payments.domain import (
    BillingAccount,
    BillingAccountNotFoundError,
    Catalog,
    CheckoutMode,
    CheckoutOrder,
    Gratitude,
    PaymentsUnavailableError,
    ProductKind,
    Purchase,
    SubscriptionAlreadyActiveError,
    SubscriptionStatus,
    TemplateAlreadyOwnedError,
    catalog,
    gratitude,
    price,
    validate_return_url,
    with_query,
)
from karria.shared.clock import Clock
from karria.shared.database import UnitOfWork
from karria.shared.errors.exceptions import EntityNotFoundError, InvalidInputError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Fenêtre pendant laquelle une même demande rend la même session Stripe :
#: assez pour absorber un double appui, assez courte pour qu'un retour le
#: lendemain ouvre une session neuve plutôt qu'une session expirée.
CHECKOUT_REPLAY_WINDOW_SECONDS = 600


@dataclass(frozen=True, slots=True)
class PlatformFlags:
    """L'interrupteur par plateforme, et si un prestataire répond.

    `configured` vaut faux sans clé Stripe : l'app cache alors tout paiement,
    comme si les deux interrupteurs étaient éteints.
    """

    ios: bool
    android: bool
    configured: bool

    @property
    def ios_enabled(self) -> bool:
        return self.ios and self.configured

    @property
    def android_enabled(self) -> bool:
        return self.android and self.configured


class GetCatalog:
    """Le catalogue : le plan sans pub, les cafés, les modèles en vente."""

    def __init__(self, templates: TemplateStore) -> None:
        self._templates = templates

    async def execute(self) -> Catalog:
        return catalog(await self._templates.premium_offers())


@dataclass(frozen=True, slots=True)
class OpenedCheckout:
    """Ce que l'ouverture rend à l'app."""

    session: CheckoutSession
    kind: ProductKind
    mode: CheckoutMode
    amount_cents: int
    currency: str


class OpenCheckout:
    """Décide le prix, contrôle le retour, ouvre la session au nom de la personne.

    Ne vend rien par lui-même : c'est la notification signée du prestataire
    qui dira que c'est payé. Une adresse de retour atteinte ne prouve rien.
    """

    def __init__(
        self,
        *,
        gateway: BillingGateway | None,
        accounts: BillingAccountRepository,
        templates: TemplateStore,
        directory: AccountDirectory,
        clock: Clock,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._templates = templates
        self._directory = directory
        self._clock = clock

    async def execute(
        self, user_id: UUID, order: CheckoutOrder, *, return_url: str, return_page: str
    ) -> OpenedCheckout:
        """Ouvre.

        Args:
            return_url: Où l'app veut revenir (`karria://…` ou `https://karria.fr/…`).
            return_page: La page de l'API où Stripe dépose la personne ; elle
                la renvoie ensuite vers `return_url`, validée ici.

        Raises:
            PaymentsUnavailableError: Sans prestataire, ou prestataire en panne.
            InvalidInputError: Retour hors de Karria, palier de cafés inconnu.
            TemplateOfferNotFoundError: Modèle inconnu ou gratuit.
            TemplateAlreadyOwnedError: Modèle déjà possédé.
            SubscriptionAlreadyActiveError: Un abonnement court déjà.
        """
        if self._gateway is None:
            raise PaymentsUnavailableError("payments_not_configured")
        destination = validate_return_url(return_url)
        offer = (
            await self._templates.premium_offer(order.template_id)
            if order.kind is ProductKind.TEMPLATE and order.template_id is not None
            else None
        )
        priced = price(order, offer=offer)

        account = await self._accounts.find_for_user(user_id)
        if priced.kind is ProductKind.AD_FREE and account is not None and account.status.is_alive:
            raise SubscriptionAlreadyActiveError(
                payment_failed=account.status is SubscriptionStatus.PAST_DUE
            )
        if (
            priced.kind is ProductKind.TEMPLATE
            and order.template_id is not None
            and await self._templates.owns(user_id, order.template_id)
        ):
            raise TemplateAlreadyOwnedError()

        if account is None:
            # Le client est créé **avant** la session et enregistré aussitôt :
            # c'est par lui que les notifications retrouvent le compte.
            customer_id = await self._gateway.ensure_customer(
                user_id, await self._directory.email_of(user_id)
            )
            account = BillingAccount.opened(user_id=user_id, customer_id=customer_id)
            await self._accounts.save(account)

        price_id = await self._gateway.ensure_price(priced.spec)
        metadata = {"user_id": str(user_id), "amount_cents": str(priced.amount_cents)}
        metadata.update(priced.metadata)
        session = await self._gateway.open_checkout(
            CheckoutRequest(
                customer_id=account.customer_id,
                price_id=price_id,
                mode=priced.mode,
                success_url=with_query(
                    return_page,
                    to=destination,
                    status="success",
                    session_id="{CHECKOUT_SESSION_ID}",
                ),
                cancel_url=with_query(return_page, to=destination, status="cancelled"),
                metadata=metadata,
                idempotency_key=self._idempotency_key(user_id, priced.spec.lookup_key),
            )
        )
        logger.info(
            "payments.checkout_opened",
            user_id=str(user_id),
            session_id=session.id,
            kind=priced.kind.value,
            amount_cents=priced.amount_cents,
        )
        return OpenedCheckout(
            session, priced.kind, priced.mode, priced.amount_cents, priced.spec.currency
        )

    def _idempotency_key(self, user_id: UUID, lookup_key: str) -> str:
        window = int(self._clock.now().timestamp()) // CHECKOUT_REPLAY_WINDOW_SECONDS
        return f"karria-checkout:{user_id}:{lookup_key}:{window}"


@dataclass(frozen=True, slots=True)
class PaymentStatus:
    """L'état d'une session, et si Karria a livré ce qu'elle payait."""

    session_id: str
    paid: bool
    kind: str | None
    fulfilled: bool


class GetPaymentStatus:
    """L'état d'une session — seulement pour la personne qui l'a ouverte.

    `paid` vient du prestataire ; `fulfilled` dit si la notification est
    arrivée et a produit son effet (café compté, modèle remis, sans pub
    actif). L'app attend `fulfilled`, pas `paid` : le reste suit.
    """

    def __init__(
        self,
        *,
        gateway: BillingGateway | None,
        accounts: BillingAccountRepository,
        purchases: PurchaseRepository,
        clock: Clock,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._purchases = purchases
        self._clock = clock

    async def execute(self, user_id: UUID, session_id: str) -> PaymentStatus:
        """Relit. `404` si la session n'existe pas ou n'appartient pas à la personne."""
        if self._gateway is None:
            raise PaymentsUnavailableError("payments_not_configured")
        record = await self._gateway.retrieve_checkout(session_id)
        if record is None or record.user_id != user_id:
            # Même réponse dans les deux cas : ne pas dire qu'une session existe.
            raise EntityNotFoundError("Paiement", session_id)
        if record.mode == "subscription":
            account = await self._accounts.find_for_user(user_id)
            fulfilled = account is not None and account.is_ad_free(self._clock.now())
        else:
            fulfilled = await self._purchases.has_session(session_id)
        return PaymentStatus(record.session_id, record.paid, record.kind, fulfilled)


@dataclass(frozen=True, slots=True)
class BillingOverview:
    """Ce que l'app affiche de la personne : sans pub ou non, et ses cafés."""

    account: BillingAccount | None
    thanks: Gratitude
    now: datetime

    @property
    def ad_free(self) -> bool:
        return self.account is not None and self.account.is_ad_free(self.now)

    @property
    def ad_free_until(self) -> datetime | None:
        return self.account.ad_free_until(self.now) if self.account is not None else None


class GetBillingOverview:
    """`GET /payments/me`. Ne lève jamais : un compte sans rien reçoit « rien »."""

    def __init__(
        self,
        *,
        accounts: BillingAccountRepository,
        purchases: PurchaseRepository,
        clock: Clock,
        configured: bool,
    ) -> None:
        self._accounts = accounts
        self._purchases = purchases
        self._clock = clock
        self._configured = configured

    async def execute(self, user_id: UUID) -> BillingOverview:
        now = self._clock.now()
        if not self._configured:
            # Sans prestataire, rien n'a pu être vendu : on ne lit pas la base.
            # C'est aussi ce qui permet de déployer ce code **avant** sa
            # migration, les clés n'étant pas posées.
            return BillingOverview(None, Gratitude(), now)
        return BillingOverview(
            await self._accounts.find_for_user(user_id),
            gratitude(await self._purchases.of_user(user_id)),
            now,
        )


class OpenPortal:
    """Le portail du prestataire : carte, reçus, résiliation.

    Ouvert dès qu'un client existe, abonné ou non : on doit toujours pouvoir
    retrouver ses reçus et résilier ce qu'on paie.
    """

    def __init__(
        self, *, gateway: BillingGateway | None, accounts: BillingAccountRepository
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts

    async def execute(self, user_id: UUID, *, return_url: str, return_page: str) -> str:
        """Rend l'adresse du portail. `404` sans client connu."""
        if self._gateway is None:
            raise PaymentsUnavailableError("payments_not_configured")
        destination = validate_return_url(return_url)
        account = await self._accounts.find_for_user(user_id)
        if account is None:
            raise BillingAccountNotFoundError()
        return await self._gateway.open_portal(
            account.customer_id, with_query(return_page, to=destination, status="portal")
        )


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Ce qu'une notification a produit."""

    event: BillingEvent
    applied: bool


class HandleWebhook:
    """Authentifie une notification, la traite **une fois**, livre ce qui est payé.

    Rejouable par construction
    --------------------------
    L'identifiant d'événement est noté dans un registre, dans la **même
    transaction** que l'effet produit : un rejeu trouve l'identifiant et
    s'acquitte ; un effet qui échoue annule aussi la note, et le prestataire
    réémettra. L'état d'un abonnement n'est jamais lu dans la notification
    mais **relu** chez le prestataire : les notifications arrivent dans le
    désordre.

    Ce qui est livré
    ----------------
    - une session de paiement unique payée → l'achat est enregistré (café
      compté, ou modèle remis par `TemplateStore.grant`) ;
    - un abonnement créé, modifié ou supprimé → la projection est resynchronisée ;
    - un prélèvement en échec → le départ de la grâce est posé.
    """

    def __init__(
        self,
        *,
        gateway: BillingGateway | None,
        accounts: BillingAccountRepository,
        purchases: PurchaseRepository,
        ledger: WebhookLedger,
        templates: TemplateStore,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._purchases = purchases
        self._ledger = ledger
        self._templates = templates
        self._clock = clock
        self._uow = uow

    async def execute(self, payload: bytes, signature: str | None) -> WebhookOutcome:
        """Vérifie, note, applique. `InvalidInputError` si la signature manque ou ment."""
        if self._gateway is None:
            raise PaymentsUnavailableError("payments_not_configured")
        if not signature:
            raise InvalidInputError(
                "En-tête stripe-signature manquant.", details={"reason": "signature_missing"}
            )
        try:
            event = self._gateway.read_event(payload, signature)
        except WebhookSignatureError as exc:
            logger.warning("payments.webhook_rejected")
            raise InvalidInputError(
                "Signature de l'événement invalide.", details={"reason": "signature_invalid"}
            ) from exc

        async with self._uow.atomic():
            if not await self._ledger.record(event.event_id, event.type):
                logger.info("payments.webhook_replayed", event_id=event.event_id, type=event.type)
                return WebhookOutcome(event, applied=False)
            applied = await self._apply(event)

        logger.info(
            "payments.webhook_received",
            event_id=event.event_id,
            type=event.type,
            kind=event.kind.value,
            applied=applied,
        )
        return WebhookOutcome(event, applied=applied)

    async def _apply(self, event: BillingEvent) -> bool:
        if event.kind is BillingEventKind.CHECKOUT_COMPLETED:
            return await self._on_checkout_completed(event.session_id)
        if event.kind is BillingEventKind.SUBSCRIPTION_CHANGED:
            return await self._sync(event.subscription_id)
        if event.kind is BillingEventKind.INVOICE_PAYMENT_FAILED:
            return await self._on_payment_failed(event.subscription_id)
        return False

    async def _on_checkout_completed(self, session_id: str | None) -> bool:
        """Une session close : livrer un paiement unique, ou relire l'abonnement né."""
        assert self._gateway is not None  # noqa: S101 - vérifié par `execute`
        if not session_id:
            return False
        record = await self._gateway.retrieve_checkout(session_id)
        if record is None:
            return False
        if record.mode == "subscription":
            return await self._sync(record.subscription_id)
        return await self._fulfill(record)

    async def _on_payment_failed(self, subscription_id: str | None) -> bool:
        """Un prélèvement en échec : l'état est relu, puis la grâce part du premier échec."""
        synced = await self._sync(subscription_id)
        account = await self._account_of_subscription(subscription_id)
        if account is None:
            return synced
        failed = account.payment_failed(at=self._clock.now())
        if failed == account:
            return synced
        await self._accounts.save(failed)
        logger.info("payments.payment_failed", user_id=str(account.user_id))
        return True

    async def _fulfill(self, record: CheckoutRecord) -> bool:
        """Un paiement unique : compte le café ou remet le modèle, une seule fois."""
        if not record.paid or record.user_id is None or record.kind is None:
            return False
        purchase = Purchase(
            id=uuid4(),
            user_id=record.user_id,
            kind=ProductKind(record.kind),
            session_id=record.session_id,
            amount_cents=record.amount_cents,
            currency=record.currency,
            paid_at=self._clock.now(),
            coffee_count=record.coffee_count,
            template_id=record.template_id,
        )
        if not await self._purchases.add(purchase):
            return False
        if purchase.kind is ProductKind.TEMPLATE and purchase.template_id is not None:
            await self._templates.grant(purchase.user_id, purchase.template_id)
        logger.info(
            "payments.purchase_recorded",
            user_id=str(purchase.user_id),
            kind=purchase.kind.value,
            amount_cents=purchase.amount_cents,
            coffee_count=purchase.coffee_count,
            template_id=str(purchase.template_id) if purchase.template_id else None,
        )
        return True

    async def _sync(self, subscription_id: str | None) -> bool:
        """Relit l'abonnement chez le prestataire et recopie son état."""
        assert self._gateway is not None  # noqa: S101 - vérifié par `execute`
        if not subscription_id:
            return False
        remote = await self._gateway.fetch_subscription(subscription_id)
        account = await self._accounts.find_by_customer(remote.customer_id, lock=True)
        if account is None and remote.user_id is not None:
            account = await self._accounts.find_for_user(remote.user_id, lock=True)
        if account is None:
            # Un client inconnu ne crée rien : la ligne naît toujours à
            # l'ouverture d'une session. Une notification sans ligne vient
            # d'ailleurs (un autre environnement sur le même compte Stripe).
            logger.warning("payments.webhook_unknown_customer", customer_id=remote.customer_id)
            return False
        updated = account.synced(remote, at=self._clock.now())
        if replace(updated, synced_at=account.synced_at) == account:
            # Rien n'a bougé : on n'écrit pas pour une date de relecture.
            return False
        await self._accounts.save(updated)
        logger.info(
            "payments.subscription_synced",
            user_id=str(updated.user_id),
            status=updated.status.value,
            cancel_at_period_end=updated.cancel_at_period_end,
        )
        return True

    async def _account_of_subscription(self, subscription_id: str | None) -> BillingAccount | None:
        assert self._gateway is not None  # noqa: S101 - vérifié par `execute`
        if not subscription_id:
            return None
        remote = await self._gateway.fetch_subscription(subscription_id)
        account = await self._accounts.find_by_customer(remote.customer_id, lock=True)
        if account is None or account.subscription_id != remote.subscription_id:
            return None
        return account


class ForgetUser:
    """La réaction à la suppression d'un compte (ADR-0015).

    Le client est supprimé chez le prestataire — ce qui résilie l'abonnement
    sur-le-champ — puis la projection et les achats locaux sont effacés. Les
    reçus et factures restent chez le prestataire, qui doit les conserver
    (obligation comptable) ; Karria n'en garde aucune copie nominative. Le
    registre des notifications ne porte aucune donnée personnelle et reste.

    Idempotent : rejouer l'événement ne trouve plus rien et ne fait rien.
    """

    def __init__(
        self,
        *,
        gateway: BillingGateway | None,
        accounts: BillingAccountRepository,
        purchases: PurchaseRepository,
    ) -> None:
        self._gateway = gateway
        self._accounts = accounts
        self._purchases = purchases

    async def execute(self, user_id: UUID) -> None:
        account = await self._accounts.find_for_user(user_id)
        if account is not None and self._gateway is not None:
            await self._gateway.delete_customer(account.customer_id)
        await self._accounts.delete_for_user(user_id)
        await self._purchases.delete_for_user(user_id)
        logger.info(
            "payments.user_forgotten", user_id=str(user_id), had_account=account is not None
        )
