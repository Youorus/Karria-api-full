"""L'adaptateur Stripe, exercé contre un faux `StripeClient` : paramètres envoyés, traductions, pannes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import stripe

from karria.modules.payments.application import (
    BillingEventKind,
    CheckoutRequest,
    WebhookSignatureError,
)
from karria.modules.payments.domain import (
    AD_FREE_PLAN,
    PaymentsUnavailableError,
    SubscriptionStatus,
    coffee_price,
)
from karria.modules.payments.infrastructure.stripe_gateway import (
    StripeGateway,
    translate_checkout,
    translate_event,
    translate_subscription,
)

pytestmark = pytest.mark.unit


def obj(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**fields)


@dataclass
class Recorder:
    """Une méthode du SDK qui retient ses arguments et rend une réponse scriptée."""

    response: Any = None
    error: Exception | None = None
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class FakeStripeClient:
    """Le sous-ensemble de `StripeClient` que l'adaptateur touche."""

    customers_create: Recorder = field(default_factory=lambda: Recorder(obj(id="cus_1")))
    customers_delete: Recorder = field(default_factory=Recorder)
    prices_list: Recorder = field(default_factory=lambda: Recorder(obj(data=[])))
    prices_create: Recorder = field(default_factory=lambda: Recorder(obj(id="price_new")))
    sessions_create: Recorder = field(
        default_factory=lambda: Recorder(obj(id="cs_1", url="https://checkout.stripe.test/cs_1"))
    )
    sessions_retrieve: Recorder = field(default_factory=Recorder)
    portal_create: Recorder = field(
        default_factory=lambda: Recorder(obj(url="https://billing.stripe.test/p"))
    )
    subscriptions_retrieve: Recorder = field(default_factory=Recorder)
    events: dict[str, Any] = field(default_factory=dict)

    @property
    def v1(self) -> Any:
        return obj(
            customers=obj(create=self.customers_create, delete=self.customers_delete),
            prices=obj(list=self.prices_list, create=self.prices_create),
            checkout=obj(
                sessions=obj(create=self.sessions_create, retrieve=self.sessions_retrieve)
            ),
            billing_portal=obj(sessions=obj(create=self.portal_create)),
            subscriptions=obj(retrieve=self.subscriptions_retrieve),
        )

    def construct_event(self, payload: bytes, signature: str, secret: str) -> Any:
        del payload, secret
        if signature not in self.events:
            raise stripe.SignatureVerificationError("mauvaise signature", signature)
        return self.events[signature]


def gateway(
    client: FakeStripeClient, *, signing: str = "whsec", tax: bool = False
) -> StripeGateway:
    return StripeGateway("sk_test", signing, automatic_tax=tax, client=client)


class TestClient:
    async def test_le_client_porte_l_adresse_et_une_cle_d_idempotence(self) -> None:
        client = FakeStripeClient()
        user = uuid4()
        assert await gateway(client).ensure_customer(user, "toi@exemple.fr") == "cus_1"
        (params,), options = client.customers_create.calls[0]
        assert params == {"metadata": {"user_id": str(user)}, "email": "toi@exemple.fr"}
        assert options == {"options": {"idempotency_key": f"karria-customer:{user}"}}

    async def test_sans_adresse_le_client_nait_quand_meme(self) -> None:
        client = FakeStripeClient()
        await gateway(client).ensure_customer(uuid4(), None)
        (params,), _ = client.customers_create.calls[0]
        assert "email" not in params

    async def test_une_panne_du_prestataire_devient_une_indisponibilite(self) -> None:
        client = FakeStripeClient()
        client.customers_create.error = stripe.APIConnectionError("réseau")
        with pytest.raises(PaymentsUnavailableError):
            await gateway(client).ensure_customer(uuid4(), None)

    async def test_la_suppression_tolere_un_client_deja_parti(self) -> None:
        client = FakeStripeClient()
        client.customers_delete.error = stripe.InvalidRequestError(
            "inconnu", "customer", code="resource_missing"
        )
        await gateway(client).delete_customer("cus_x")

        client.customers_delete.error = stripe.InvalidRequestError("autre", "customer", code="x")
        with pytest.raises(PaymentsUnavailableError):
            await gateway(client).delete_customer("cus_x")
        client.customers_delete.error = stripe.APIConnectionError("réseau")
        with pytest.raises(PaymentsUnavailableError):
            await gateway(client).delete_customer("cus_x")


class TestPrices:
    async def test_un_prix_existant_au_bon_montant_est_reutilise_et_mis_en_cache(self) -> None:
        client = FakeStripeClient()
        client.prices_list.response = obj(
            data=[
                obj(id="price_ok", unit_amount=499, currency="eur", recurring=obj(interval="month"))
            ]
        )
        g = gateway(client)
        assert await g.ensure_price(AD_FREE_PLAN) == "price_ok"
        assert await g.ensure_price(AD_FREE_PLAN) == "price_ok"
        assert len(client.prices_list.calls) == 1
        (params,), _ = client.prices_list.calls[0]
        assert params == {"lookup_keys": ["karria_ad_free_monthly"], "active": True, "limit": 1}
        assert client.prices_create.calls == []

    async def test_un_prix_absent_est_cree_avec_sa_cle(self) -> None:
        client = FakeStripeClient()
        assert await gateway(client).ensure_price(coffee_price(2)) == "price_new"
        (params,), options = client.prices_create.calls[0]
        assert params == {
            "currency": "eur",
            "unit_amount": 499,
            "lookup_key": "karria_coffee_2",
            "transfer_lookup_key": True,
            "tax_behavior": "inclusive",
            "product_data": {"name": "2 cafés pour l'équipe Karria"},
            "metadata": {"lookup_key": "karria_coffee_2"},
        }
        assert options == {"options": {"idempotency_key": "karria-price:karria_coffee_2:499"}}

    async def test_un_prix_au_mauvais_montant_est_remplace(self) -> None:
        client = FakeStripeClient()
        client.prices_list.response = obj(
            data=[obj(id="price_old", unit_amount=399, currency="eur", recurring=None)]
        )
        assert await gateway(client).ensure_price(coffee_price(1)) == "price_new"
        (params,), _ = client.prices_create.calls[0]
        assert params["unit_amount"] == 299
        assert "recurring" not in params

    async def test_un_abonnement_porte_sa_periodicite(self) -> None:
        client = FakeStripeClient()
        client.prices_list.response = obj(
            data=[obj(id="price_once", unit_amount=499, currency="eur", recurring=None)]
        )
        await gateway(client).ensure_price(AD_FREE_PLAN)
        (params,), _ = client.prices_create.calls[0]
        assert params["recurring"] == {"interval": "month"}


def request(mode: str = "payment") -> CheckoutRequest:
    return CheckoutRequest(
        customer_id="cus_1",
        price_id="price_1",
        mode="subscription" if mode == "subscription" else "payment",
        success_url="https://api.karria.fr/r?status=success",
        cancel_url="https://api.karria.fr/r?status=cancelled",
        metadata={"user_id": "u", "kind": "coffee"},
        idempotency_key="k",
    )


class TestCheckout:
    async def test_un_paiement_unique(self) -> None:
        client = FakeStripeClient()
        session = await gateway(client).open_checkout(request())
        assert (session.id, session.url) == ("cs_1", "https://checkout.stripe.test/cs_1")
        (params,), options = client.sessions_create.calls[0]
        assert params == {
            "mode": "payment",
            "customer": "cus_1",
            "line_items": [{"price": "price_1", "quantity": 1}],
            "success_url": "https://api.karria.fr/r?status=success",
            "cancel_url": "https://api.karria.fr/r?status=cancelled",
            "metadata": {"user_id": "u", "kind": "coffee"},
            "automatic_tax": {"enabled": False},
            "payment_intent_data": {"metadata": {"user_id": "u", "kind": "coffee"}},
        }
        assert options == {"options": {"idempotency_key": "k"}}

    async def test_un_abonnement_avec_stripe_tax(self) -> None:
        client = FakeStripeClient()
        await gateway(client, tax=True).open_checkout(request("subscription"))
        (params,), _ = client.sessions_create.calls[0]
        assert params["mode"] == "subscription"
        assert params["subscription_data"] == {"metadata": {"user_id": "u", "kind": "coffee"}}
        assert params["automatic_tax"] == {"enabled": True}
        assert params["customer_update"] == {"address": "auto"}
        assert "payment_intent_data" not in params

    async def test_une_session_sans_adresse_est_une_panne(self) -> None:
        client = FakeStripeClient()
        client.sessions_create.response = obj(id="cs_1", url=None)
        with pytest.raises(PaymentsUnavailableError):
            await gateway(client).open_checkout(request())

    async def test_la_relecture_traduit_ou_rend_rien(self) -> None:
        client = FakeStripeClient()
        user = uuid4()
        client.sessions_retrieve.response = obj(
            id="cs_1",
            mode="payment",
            payment_status="paid",
            amount_total=799,
            currency="eur",
            metadata={"user_id": str(user), "kind": "coffee", "coffee_count": "3"},
            subscription=None,
            customer="cus_1",
        )
        record = await gateway(client).retrieve_checkout("cs_1")
        assert record is not None
        assert (record.paid, record.amount_cents, record.coffee_count, record.user_id) == (
            True,
            799,
            3,
            user,
        )
        assert record.customer_id == "cus_1"

        client.sessions_retrieve.error = stripe.InvalidRequestError(
            "inconnue", "id", code="resource_missing"
        )
        assert await gateway(client).retrieve_checkout("cs_x") is None
        client.sessions_retrieve.error = stripe.InvalidRequestError("autre", "id", code="x")
        with pytest.raises(PaymentsUnavailableError):
            await gateway(client).retrieve_checkout("cs_x")
        client.sessions_retrieve.error = stripe.APIConnectionError("réseau")
        with pytest.raises(PaymentsUnavailableError):
            await gateway(client).retrieve_checkout("cs_x")

    async def test_le_portail(self) -> None:
        client = FakeStripeClient()
        url = await gateway(client).open_portal("cus_1", "https://api.karria.fr/r")
        assert url == "https://billing.stripe.test/p"
        (params,), _ = client.portal_create.calls[0]
        assert params == {"customer": "cus_1", "return_url": "https://api.karria.fr/r"}


class TestEvents:
    def test_sans_secret_tout_est_refuse(self) -> None:
        client = FakeStripeClient()
        client.events["sig"] = {"id": "evt_1", "type": "x", "data": {"object": {}}}
        with pytest.raises(WebhookSignatureError):
            gateway(client, signing="").read_event(b"{}", "sig")

    def test_une_signature_fausse_est_refusee(self) -> None:
        with pytest.raises(WebhookSignatureError):
            gateway(FakeStripeClient()).read_event(b"{}", "faux")

    def test_une_session_close(self) -> None:
        client = FakeStripeClient()
        client.events["sig"] = {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_1", "object": "checkout.session"}},
        }
        event = gateway(client).read_event(b"{}", "sig")
        assert event.kind is BillingEventKind.CHECKOUT_COMPLETED
        assert event.session_id == "cs_1"
        assert event.event_id == "evt_1"

    @pytest.mark.parametrize(
        "event_type",
        [
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        ],
    )
    def test_un_abonnement_change(self, event_type: str) -> None:
        event = translate_event(
            {"id": "evt_2", "type": event_type, "data": {"object": obj(id="sub_1")}}
        )
        assert event.kind is BillingEventKind.SUBSCRIPTION_CHANGED
        assert event.subscription_id == "sub_1"

    def test_une_facture_en_echec_nomme_son_abonnement(self) -> None:
        recent = translate_event(
            {
                "id": "evt_3",
                "type": "invoice.payment_failed",
                "data": {
                    "object": {
                        "id": "in_1",
                        "parent": {"subscription_details": {"subscription": "sub_1"}},
                    }
                },
            }
        )
        assert recent.kind is BillingEventKind.INVOICE_PAYMENT_FAILED
        assert (recent.subscription_id, recent.invoice_id) == ("sub_1", "in_1")

        legacy = translate_event(
            {
                "id": "evt_4",
                "type": "invoice.payment_failed",
                "data": {"object": {"id": "in_2", "subscription": obj(id="sub_2")}},
            }
        )
        assert legacy.subscription_id == "sub_2"

        one_off = translate_event(
            {"id": "evt_5", "type": "invoice.payment_failed", "data": {"object": {"id": "in_3"}}}
        )
        assert one_off.kind is BillingEventKind.IGNORED

    def test_le_reste_est_ignore(self) -> None:
        event = translate_event({"id": "evt_6", "type": "charge.refunded", "data": {"object": {}}})
        assert event.kind is BillingEventKind.IGNORED
        assert event.type == "charge.refunded"


class TestSubscriptionTranslation:
    async def test_un_abonnement_actif_lu_sur_ses_elements(self) -> None:
        client = FakeStripeClient()
        user = uuid4()
        client.subscriptions_retrieve.response = obj(
            id="sub_1",
            customer="cus_1",
            status="active",
            items=obj(data=[obj(current_period_end=1_800_000_000)]),
            cancel_at_period_end=False,
            cancel_at=None,
            ended_at=None,
            metadata={"user_id": str(user)},
        )
        remote = await gateway(client).fetch_subscription("sub_1")
        assert remote.status is SubscriptionStatus.ACTIVE
        assert remote.current_period_ends_at == datetime.fromtimestamp(1_800_000_000, tz=UTC)
        assert remote.cancel_scheduled is False
        assert remote.ends_at is None
        assert remote.user_id == user

    def test_une_resiliation_programmee_et_l_ancien_emplacement(self) -> None:
        remote = translate_subscription(
            {
                "id": "sub_1",
                "customer": {"id": "cus_1"},
                "status": "active",
                "items": {"data": []},
                "current_period_end": 1_800_000_000,
                "cancel_at_period_end": True,
                "metadata": {},
            }
        )
        assert remote.customer_id == "cus_1"
        assert remote.cancel_scheduled is True
        assert remote.ends_at == datetime.fromtimestamp(1_800_000_000, tz=UTC)
        assert remote.user_id is None

    def test_une_date_de_fin_prime(self) -> None:
        remote = translate_subscription(
            {
                "id": "sub_1",
                "customer": "cus_1",
                "status": "canceled",
                "cancel_at": 1_700_000_000,
                "ended_at": 1_650_000_000,
                "metadata": {"user_id": "pas-un-uuid"},
            }
        )
        assert remote.status is SubscriptionStatus.CANCELED
        assert remote.ends_at == datetime.fromtimestamp(1_650_000_000, tz=UTC)
        assert remote.user_id is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("trialing", SubscriptionStatus.ACTIVE),
            ("past_due", SubscriptionStatus.PAST_DUE),
            ("unpaid", SubscriptionStatus.PAST_DUE),
            ("paused", SubscriptionStatus.PAST_DUE),
            ("incomplete", SubscriptionStatus.NONE),
            ("incomplete_expired", SubscriptionStatus.NONE),
            ("inconnu", SubscriptionStatus.PAST_DUE),
        ],
    )
    def test_le_vocabulaire_de_stripe(self, raw: str, expected: SubscriptionStatus) -> None:
        remote = translate_subscription({"id": "s", "customer": "c", "status": raw})
        assert remote.status is expected


class TestCheckoutTranslation:
    def test_une_souscription_close(self) -> None:
        record = translate_checkout(
            {
                "id": "cs_2",
                "mode": "subscription",
                "payment_status": "paid",
                "amount_total": 499,
                "currency": "eur",
                "metadata": {"user_id": "x", "kind": "ad_free", "coffee_count": "abc"},
                "subscription": "sub_1",
                "customer": "cus_1",
            }
        )
        assert record.mode == "subscription"
        assert record.subscription_id == "sub_1"
        assert record.user_id is None
        assert record.coffee_count is None
        assert record.template_id is None

    def test_un_modele_paye(self) -> None:
        template = uuid4()
        record = translate_checkout(
            {
                "id": "cs_3",
                "mode": "payment",
                "payment_status": "unpaid",
                "amount_total": None,
                "currency": None,
                "metadata": {"kind": "template", "template_id": str(template)},
            }
        )
        assert (record.paid, record.amount_cents, record.currency) == (False, 0, "eur")
        assert record.template_id == template
        assert record.kind == "template"
