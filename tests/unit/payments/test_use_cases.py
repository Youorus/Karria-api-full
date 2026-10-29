"""Les cas d'usage du module `payments`, avec un prestataire et des dépôts en mémoire."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from doubles import FakeClock
from karria.modules.payments.application import (
    CHECKOUT_REPLAY_WINDOW_SECONDS,
    BillingEventKind,
    ForgetUser,
    GetBillingOverview,
    GetCatalog,
    GetPaymentStatus,
    HandleWebhook,
    OpenCheckout,
    OpenPortal,
    PlatformFlags,
)
from karria.modules.payments.domain import (
    GRACE_PERIOD,
    BillingAccount,
    BillingAccountNotFoundError,
    CheckoutOrder,
    PaymentsUnavailableError,
    ProductKind,
    SubscriptionAlreadyActiveError,
    SubscriptionStatus,
    TemplateAlreadyOwnedError,
    TemplateOfferNotFoundError,
)
from karria.shared.database import NullUnitOfWork
from karria.shared.errors.exceptions import EntityNotFoundError, InvalidInputError
from payments.fakes import (
    NOW,
    FakeDirectory,
    FakeGateway,
    FakeTemplateStore,
    InMemoryAccounts,
    InMemoryLedger,
    InMemoryPurchases,
)

pytestmark = pytest.mark.unit

RETURN_PAGE = "https://api.karria.fr/api/v1/payments/return"
APP = "karria://paiement/retour"


@dataclass
class Env:
    gateway: FakeGateway = field(default_factory=FakeGateway)
    accounts: InMemoryAccounts = field(default_factory=InMemoryAccounts)
    purchases: InMemoryPurchases = field(default_factory=InMemoryPurchases)
    ledger: InMemoryLedger = field(default_factory=InMemoryLedger)
    templates: FakeTemplateStore = field(default_factory=FakeTemplateStore)
    directory: FakeDirectory = field(default_factory=FakeDirectory)
    clock: FakeClock = field(default_factory=lambda: FakeClock(NOW))
    user: UUID = field(default_factory=uuid4)

    def open(self, gateway: FakeGateway | None = None, *, configured: bool = True) -> OpenCheckout:
        return OpenCheckout(
            gateway=(gateway or self.gateway) if configured else None,
            accounts=self.accounts,
            templates=self.templates,
            directory=self.directory,
            clock=self.clock,
        )

    def status(self, *, configured: bool = True) -> GetPaymentStatus:
        return GetPaymentStatus(
            gateway=self.gateway if configured else None,
            accounts=self.accounts,
            purchases=self.purchases,
            clock=self.clock,
        )

    def overview(self, *, configured: bool = True) -> GetBillingOverview:
        return GetBillingOverview(
            accounts=self.accounts,
            purchases=self.purchases,
            clock=self.clock,
            configured=configured,
        )

    def portal(self, *, configured: bool = True) -> OpenPortal:
        return OpenPortal(gateway=self.gateway if configured else None, accounts=self.accounts)

    def webhook(self, *, configured: bool = True) -> HandleWebhook:
        return HandleWebhook(
            gateway=self.gateway if configured else None,
            accounts=self.accounts,
            purchases=self.purchases,
            ledger=self.ledger,
            templates=self.templates,
            clock=self.clock,
            uow=NullUnitOfWork(),
        )

    def forget(self, *, configured: bool = True) -> ForgetUser:
        return ForgetUser(
            gateway=self.gateway if configured else None,
            accounts=self.accounts,
            purchases=self.purchases,
        )

    async def subscribe(self) -> str:
        """La personne souscrit et Stripe confirme : rend l'identifiant d'abonnement."""
        opened = await self.open().execute(
            self.user, CheckoutOrder(ProductKind.AD_FREE), return_url=APP, return_page=RETURN_PAGE
        )
        self.gateway.pay(opened.session.id, subscription_id="sub_1")
        self.gateway.subscription(
            "sub_1", "cus_1", period_end=NOW + timedelta(days=30), user_id=self.user
        )
        self.gateway.event(
            "ok",
            BillingEventKind.CHECKOUT_COMPLETED,
            event_id="evt_1",
            session_id=opened.session.id,
        )
        await self.webhook().execute(b"{}", "ok")
        return "sub_1"


@pytest.fixture
def env() -> Env:
    return Env()


class TestCatalogue:
    async def test_le_catalogue_liste_les_modeles_en_vente(self, env: Env) -> None:
        offer = env.templates.sell("Meridian", 499)
        cat = await GetCatalog(env.templates).execute()
        assert cat.templates == (offer,)
        assert len(cat.coffees) == 4

    def test_l_interrupteur_est_eteint_sans_prestataire(self) -> None:
        flags = PlatformFlags(ios=True, android=True, configured=False)
        assert flags.ios_enabled is False
        assert flags.android_enabled is False
        on = PlatformFlags(ios=False, android=True, configured=True)
        assert on.ios_enabled is False
        assert on.android_enabled is True


class TestOpenCheckout:
    async def test_ouvre_un_abonnement_au_nom_de_la_personne(self, env: Env) -> None:
        env.directory.emails[env.user] = "toi@exemple.fr"

        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.AD_FREE), return_url=APP, return_page=RETURN_PAGE
        )

        assert opened.mode == "subscription"
        assert opened.amount_cents == 499
        assert opened.kind is ProductKind.AD_FREE
        assert env.gateway.customers == [(env.user, "toi@exemple.fr")]
        request = env.gateway.opened[0]
        assert request.customer_id == "cus_1"
        assert request.price_id == "price_karria_ad_free_monthly"
        assert request.metadata == {
            "user_id": str(env.user),
            "amount_cents": "499",
            "kind": "ad_free",
        }
        assert request.success_url == (
            f"{RETURN_PAGE}?to=karria%3A%2F%2Fpaiement%2Fretour&status=success"
            "&session_id=%7BCHECKOUT_SESSION_ID%7D"
        )
        assert request.cancel_url == (
            f"{RETURN_PAGE}?to=karria%3A%2F%2Fpaiement%2Fretour&status=cancelled"
        )
        account = env.accounts.rows[env.user]
        assert account.customer_id == "cus_1"
        assert account.status is SubscriptionStatus.NONE

    async def test_le_client_stripe_est_reutilise_d_un_achat_a_l_autre(self, env: Env) -> None:
        await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE, 1), return_url=APP, return_page=RETURN_PAGE
        )
        await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE, 2), return_url=APP, return_page=RETURN_PAGE
        )
        assert len(env.gateway.customers) == 1
        assert [r.price_id for r in env.gateway.opened] == [
            "price_karria_coffee_1",
            "price_karria_coffee_2",
        ]

    async def test_la_cle_d_idempotence_tient_dix_minutes(self, env: Env) -> None:
        order = CheckoutOrder(ProductKind.COFFEE, 1)
        await env.open().execute(env.user, order, return_url=APP, return_page=RETURN_PAGE)
        env.clock.advance(timedelta(seconds=CHECKOUT_REPLAY_WINDOW_SECONDS))
        await env.open().execute(env.user, order, return_url=APP, return_page=RETURN_PAGE)
        keys = [r.idempotency_key for r in env.gateway.opened]
        assert keys[0].startswith(f"karria-checkout:{env.user}:karria_coffee_1:")
        assert keys[0] != keys[1]

    async def test_un_abonnement_vivant_refuse_un_second(self, env: Env) -> None:
        await env.subscribe()
        with pytest.raises(SubscriptionAlreadyActiveError) as caught:
            await env.open().execute(
                env.user,
                CheckoutOrder(ProductKind.AD_FREE),
                return_url=APP,
                return_page=RETURN_PAGE,
            )
        assert caught.value.details["reason"] == "subscription_already_active"
        assert caught.value.details["payment_failed"] is False

    async def test_un_abonnement_en_defaut_le_dit(self, env: Env) -> None:
        env.accounts.rows[env.user] = BillingAccount(
            id=uuid4(), user_id=env.user, customer_id="cus_x", status=SubscriptionStatus.PAST_DUE
        )
        with pytest.raises(SubscriptionAlreadyActiveError) as caught:
            await env.open().execute(
                env.user,
                CheckoutOrder(ProductKind.AD_FREE),
                return_url=APP,
                return_page=RETURN_PAGE,
            )
        assert caught.value.details["payment_failed"] is True

    async def test_un_abonnement_resilie_peut_etre_repris(self, env: Env) -> None:
        env.accounts.rows[env.user] = BillingAccount(
            id=uuid4(), user_id=env.user, customer_id="cus_x", status=SubscriptionStatus.CANCELED
        )
        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.AD_FREE), return_url=APP, return_page=RETURN_PAGE
        )
        assert env.gateway.opened[0].customer_id == "cus_x"
        assert opened.session.id == "cs_1"

    async def test_un_modele_se_paie_au_prix_de_documents(self, env: Env) -> None:
        offer = env.templates.sell("Meridian", 699)
        opened = await env.open().execute(
            env.user,
            CheckoutOrder(ProductKind.TEMPLATE, template_id=offer.template_id),
            return_url=APP,
            return_page=RETURN_PAGE,
        )
        assert opened.amount_cents == 699
        assert opened.mode == "payment"
        assert env.gateway.opened[0].metadata["template_id"] == str(offer.template_id)

    async def test_un_modele_deja_possede_ne_se_rachete_pas(self, env: Env) -> None:
        offer = env.templates.sell()
        env.templates.owned.add((env.user, offer.template_id))
        with pytest.raises(TemplateAlreadyOwnedError):
            await env.open().execute(
                env.user,
                CheckoutOrder(ProductKind.TEMPLATE, template_id=offer.template_id),
                return_url=APP,
                return_page=RETURN_PAGE,
            )
        assert env.gateway.opened == []

    async def test_un_modele_inconnu_n_est_pas_en_vente(self, env: Env) -> None:
        with pytest.raises(TemplateOfferNotFoundError):
            await env.open().execute(
                env.user,
                CheckoutOrder(ProductKind.TEMPLATE, template_id=uuid4()),
                return_url=APP,
                return_page=RETURN_PAGE,
            )

    async def test_une_adresse_hors_karria_est_refusee_avant_tout_appel(self, env: Env) -> None:
        with pytest.raises(InvalidInputError):
            await env.open().execute(
                env.user,
                CheckoutOrder(ProductKind.COFFEE),
                return_url="https://evil.example/",
                return_page=RETURN_PAGE,
            )
        assert env.gateway.opened == []
        assert env.gateway.customers == []

    async def test_sans_prestataire_pas_de_demonstration(self, env: Env) -> None:
        with pytest.raises(PaymentsUnavailableError):
            await env.open(configured=False).execute(
                env.user, CheckoutOrder(ProductKind.COFFEE), return_url=APP, return_page=RETURN_PAGE
            )


class TestGetPaymentStatus:
    async def test_un_cafe_n_est_livre_qu_apres_la_notification(self, env: Env) -> None:
        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE, 2), return_url=APP, return_page=RETURN_PAGE
        )
        before = await env.status().execute(env.user, opened.session.id)
        assert (before.paid, before.fulfilled, before.kind) == (False, False, "coffee")

        env.gateway.pay(opened.session.id)
        paid = await env.status().execute(env.user, opened.session.id)
        assert (paid.paid, paid.fulfilled) == (True, False)

        env.gateway.event("ok", BillingEventKind.CHECKOUT_COMPLETED, session_id=opened.session.id)
        await env.webhook().execute(b"{}", "ok")
        delivered = await env.status().execute(env.user, opened.session.id)
        assert delivered.fulfilled is True

    async def test_un_abonnement_est_livre_quand_le_sans_pub_est_actif(self, env: Env) -> None:
        await env.subscribe()
        status = await env.status().execute(env.user, "cs_1")
        assert status.fulfilled is True

    async def test_la_session_d_une_autre_personne_est_introuvable(self, env: Env) -> None:
        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE), return_url=APP, return_page=RETURN_PAGE
        )
        with pytest.raises(EntityNotFoundError):
            await env.status().execute(uuid4(), opened.session.id)
        with pytest.raises(EntityNotFoundError):
            await env.status().execute(env.user, "cs_inconnue")

    async def test_sans_prestataire(self, env: Env) -> None:
        with pytest.raises(PaymentsUnavailableError):
            await env.status(configured=False).execute(env.user, "cs_1")


class TestOverview:
    async def test_un_compte_sans_rien(self, env: Env) -> None:
        overview = await env.overview().execute(env.user)
        assert overview.account is None
        assert overview.ad_free is False
        assert overview.ad_free_until is None
        assert overview.thanks.coffees == 0

    async def test_sans_prestataire_rien_n_est_lu(self, env: Env) -> None:
        env.accounts.rows[env.user] = BillingAccount(
            id=uuid4(), user_id=env.user, customer_id="cus_x", status=SubscriptionStatus.ACTIVE
        )
        overview = await env.overview(configured=False).execute(env.user)
        assert overview.account is None

    async def test_un_abonne_est_sans_pub_avec_ses_cafes(self, env: Env) -> None:
        await env.subscribe()
        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE, 3), return_url=APP, return_page=RETURN_PAGE
        )
        env.gateway.pay(opened.session.id)
        env.gateway.event("cafe", BillingEventKind.CHECKOUT_COMPLETED, session_id=opened.session.id)
        await env.webhook().execute(b"{}", "cafe")

        overview = await env.overview().execute(env.user)
        assert overview.ad_free is True
        assert overview.ad_free_until == NOW + timedelta(days=30)
        assert overview.thanks.coffees == 3
        assert overview.thanks.total_cents == 799


class TestPortal:
    async def test_ouvre_le_portail_du_client(self, env: Env) -> None:
        await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE), return_url=APP, return_page=RETURN_PAGE
        )
        url = await env.portal().execute(env.user, return_url=APP, return_page=RETURN_PAGE)
        assert url == "https://billing.stripe.test/cus_1"
        assert env.gateway.portals == [
            ("cus_1", f"{RETURN_PAGE}?to=karria%3A%2F%2Fpaiement%2Fretour&status=portal")
        ]

    async def test_sans_client_rien_a_gerer(self, env: Env) -> None:
        with pytest.raises(BillingAccountNotFoundError):
            await env.portal().execute(env.user, return_url=APP, return_page=RETURN_PAGE)

    async def test_sans_prestataire(self, env: Env) -> None:
        with pytest.raises(PaymentsUnavailableError):
            await env.portal(configured=False).execute(
                env.user, return_url=APP, return_page=RETURN_PAGE
            )

    async def test_une_adresse_hors_karria_est_refusee(self, env: Env) -> None:
        with pytest.raises(InvalidInputError):
            await env.portal().execute(
                env.user, return_url="https://evil.example", return_page=RETURN_PAGE
            )


class TestWebhook:
    async def test_un_cafe_paye_est_compte_une_seule_fois(self, env: Env) -> None:
        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE, 2), return_url=APP, return_page=RETURN_PAGE
        )
        env.gateway.pay(opened.session.id)
        env.gateway.event(
            "a", BillingEventKind.CHECKOUT_COMPLETED, event_id="evt_a", session_id=opened.session.id
        )
        # Stripe envoie parfois deux événements distincts pour le même fait.
        env.gateway.event(
            "b", BillingEventKind.CHECKOUT_COMPLETED, event_id="evt_b", session_id=opened.session.id
        )

        first = await env.webhook().execute(b"{}", "a")
        replay = await env.webhook().execute(b"{}", "a")
        twin = await env.webhook().execute(b"{}", "b")

        assert first.applied is True
        assert replay.applied is False
        assert twin.applied is False
        assert len(env.purchases.rows) == 1
        purchase = env.purchases.rows[0]
        assert (purchase.kind, purchase.coffee_count, purchase.amount_cents) == (
            ProductKind.COFFEE,
            2,
            499,
        )
        assert purchase.paid_at == NOW

    async def test_un_modele_paye_est_remis_dans_la_meme_transaction(self, env: Env) -> None:
        offer = env.templates.sell()
        opened = await env.open().execute(
            env.user,
            CheckoutOrder(ProductKind.TEMPLATE, template_id=offer.template_id),
            return_url=APP,
            return_page=RETURN_PAGE,
        )
        env.gateway.pay(opened.session.id)
        env.gateway.event("ok", BillingEventKind.CHECKOUT_COMPLETED, session_id=opened.session.id)

        outcome = await env.webhook().execute(b"{}", "ok")

        assert outcome.applied is True
        assert env.templates.granted == [(env.user, offer.template_id)]
        assert await env.templates.owns(env.user, offer.template_id)

    async def test_une_session_non_payee_ne_livre_rien(self, env: Env) -> None:
        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE), return_url=APP, return_page=RETURN_PAGE
        )
        env.gateway.event("ok", BillingEventKind.CHECKOUT_COMPLETED, session_id=opened.session.id)
        outcome = await env.webhook().execute(b"{}", "ok")
        assert outcome.applied is False
        assert env.purchases.rows == []

    async def test_une_session_inconnue_ou_absente_s_acquitte(self, env: Env) -> None:
        env.gateway.event("x", BillingEventKind.CHECKOUT_COMPLETED, session_id="cs_inconnue")
        env.gateway.event("y", BillingEventKind.CHECKOUT_COMPLETED)
        assert (await env.webhook().execute(b"{}", "x")).applied is False
        assert (await env.webhook().execute(b"{}", "y")).applied is False

    async def test_une_souscription_close_relit_l_abonnement(self, env: Env) -> None:
        await env.subscribe()
        account = env.accounts.rows[env.user]
        assert account.status is SubscriptionStatus.ACTIVE
        assert account.subscription_id == "sub_1"
        assert account.current_period_ends_at == NOW + timedelta(days=30)
        assert env.accounts.locks >= 1

    async def test_une_resiliation_puis_une_suppression_se_lisent(self, env: Env) -> None:
        await env.subscribe()
        env.gateway.subscription(
            "sub_1",
            "cus_1",
            period_end=NOW + timedelta(days=30),
            cancel_scheduled=True,
            ends_at=NOW + timedelta(days=30),
        )
        env.gateway.event("upd", BillingEventKind.SUBSCRIPTION_CHANGED, subscription_id="sub_1")
        assert (await env.webhook().execute(b"{}", "upd")).applied is True
        assert env.accounts.rows[env.user].cancel_at_period_end is True
        assert env.accounts.rows[env.user].is_ad_free(NOW) is True

        env.gateway.subscription(
            "sub_1", "cus_1", status=SubscriptionStatus.CANCELED, ends_at=NOW + timedelta(days=30)
        )
        env.gateway.event("del", BillingEventKind.SUBSCRIPTION_CHANGED, subscription_id="sub_1")
        assert (await env.webhook().execute(b"{}", "del")).applied is True
        assert env.accounts.rows[env.user].is_ad_free(NOW + timedelta(days=31)) is False

    async def test_un_etat_identique_ne_change_rien(self, env: Env) -> None:
        await env.subscribe()
        env.gateway.event("same", BillingEventKind.SUBSCRIPTION_CHANGED, subscription_id="sub_1")
        assert (await env.webhook().execute(b"{}", "same")).applied is False

    async def test_un_prelevement_en_echec_ouvre_la_grace(self, env: Env) -> None:
        await env.subscribe()
        env.gateway.subscription(
            "sub_1",
            "cus_1",
            status=SubscriptionStatus.PAST_DUE,
            period_end=NOW + timedelta(days=30),
        )
        env.gateway.event(
            "fail",
            BillingEventKind.INVOICE_PAYMENT_FAILED,
            subscription_id="sub_1",
            invoice_id="in_1",
        )
        outcome = await env.webhook().execute(b"{}", "fail")

        assert outcome.applied is True
        account = env.accounts.rows[env.user]
        assert account.status is SubscriptionStatus.PAST_DUE
        assert account.past_due_since == NOW
        assert account.is_ad_free(NOW + GRACE_PERIOD - timedelta(hours=1)) is True
        assert account.is_ad_free(NOW + GRACE_PERIOD) is False

        # Une seconde relance de la même facture ne repousse pas la grâce.
        env.clock.advance(timedelta(days=3))
        env.gateway.event(
            "fail2",
            BillingEventKind.INVOICE_PAYMENT_FAILED,
            event_id="evt_fail2",
            subscription_id="sub_1",
            invoice_id="in_1",
        )
        assert (await env.webhook().execute(b"{}", "fail2")).applied is False
        assert env.accounts.rows[env.user].past_due_since == NOW

    async def test_un_echec_sur_un_abonnement_inconnu_ou_sans_identifiant(self, env: Env) -> None:
        env.gateway.subscription("sub_x", "cus_inconnu")
        env.gateway.event("f", BillingEventKind.INVOICE_PAYMENT_FAILED, subscription_id="sub_x")
        env.gateway.event("g", BillingEventKind.INVOICE_PAYMENT_FAILED)
        assert (await env.webhook().execute(b"{}", "f")).applied is False
        assert (await env.webhook().execute(b"{}", "g")).applied is False

    async def test_un_echec_sur_un_abonnement_remplace_est_ignore(self, env: Env) -> None:
        await env.subscribe()
        env.gateway.subscription("sub_old", "cus_1", status=SubscriptionStatus.CANCELED)
        env.gateway.event("old", BillingEventKind.INVOICE_PAYMENT_FAILED, subscription_id="sub_old")
        assert (await env.webhook().execute(b"{}", "old")).applied is False
        assert env.accounts.rows[env.user].status is SubscriptionStatus.ACTIVE

    async def test_un_client_inconnu_ne_cree_rien(self, env: Env) -> None:
        env.gateway.subscription("sub_9", "cus_ailleurs")
        env.gateway.event("ail", BillingEventKind.SUBSCRIPTION_CHANGED, subscription_id="sub_9")
        assert (await env.webhook().execute(b"{}", "ail")).applied is False
        assert env.accounts.rows == {}

    async def test_un_client_inconnu_mais_une_personne_connue_se_retrouve(self, env: Env) -> None:
        await env.open().execute(
            env.user, CheckoutOrder(ProductKind.AD_FREE), return_url=APP, return_page=RETURN_PAGE
        )
        # Le client a été recréé côté Stripe (autre identifiant) : la métadonnée sauve.
        env.gateway.subscription("sub_2", "cus_autre", user_id=env.user)
        env.gateway.event("m", BillingEventKind.SUBSCRIPTION_CHANGED, subscription_id="sub_2")
        assert (await env.webhook().execute(b"{}", "m")).applied is True
        assert env.accounts.rows[env.user].customer_id == "cus_autre"

    async def test_un_evenement_ignore_s_acquitte_et_se_note(self, env: Env) -> None:
        env.gateway.event("ign", BillingEventKind.IGNORED)
        outcome = await env.webhook().execute(b"{}", "ign")
        assert outcome.applied is False
        assert "evt_ign" in env.ledger.seen

    async def test_sans_signature_ou_signature_fausse(self, env: Env) -> None:
        with pytest.raises(InvalidInputError) as missing:
            await env.webhook().execute(b"", None)
        assert missing.value.details["reason"] == "signature_missing"
        with pytest.raises(InvalidInputError) as wrong:
            await env.webhook().execute(b"x", "faux")
        assert wrong.value.details["reason"] == "signature_invalid"

    async def test_sans_prestataire(self, env: Env) -> None:
        with pytest.raises(PaymentsUnavailableError):
            await env.webhook(configured=False).execute(b"", "ok")


class TestForgetUser:
    async def test_le_client_est_supprime_chez_stripe_et_tout_est_efface(self, env: Env) -> None:
        await env.subscribe()
        opened = await env.open().execute(
            env.user, CheckoutOrder(ProductKind.COFFEE), return_url=APP, return_page=RETURN_PAGE
        )
        env.gateway.pay(opened.session.id)
        env.gateway.event("c", BillingEventKind.CHECKOUT_COMPLETED, session_id=opened.session.id)
        await env.webhook().execute(b"{}", "c")

        await env.forget().execute(env.user)

        assert env.gateway.deleted_customers == ["cus_1"]
        assert env.accounts.rows == {}
        assert env.purchases.rows == []
        # Le registre des notifications n'a rien de personnel : il reste.
        assert len(env.ledger.seen) == 2

    async def test_un_compte_sans_paiement_ou_sans_prestataire_ne_fait_rien(self, env: Env) -> None:
        await env.forget().execute(env.user)
        assert env.gateway.deleted_customers == []

        env.accounts.rows[env.user] = BillingAccount.opened(user_id=env.user, customer_id="cus_z")
        await env.forget(configured=False).execute(env.user)
        assert env.gateway.deleted_customers == []
        assert env.accounts.rows == {}
