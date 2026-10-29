"""Les règles du module `payments` : catalogue et prix, abonnement et grâce, merci, retour."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from karria.modules.payments.domain import (
    AD_FREE_PLAN,
    COFFEE_TIERS,
    GRACE_PERIOD,
    BillingAccount,
    CheckoutOrder,
    PremiumOffer,
    ProductKind,
    ProviderSubscription,
    Purchase,
    SubscriptionStatus,
    TemplateOfferNotFoundError,
    TemplateRequiredError,
    UnknownCoffeeTierError,
    catalog,
    coffee_price,
    gratitude,
    price,
    template_price,
    validate_return_url,
    with_query,
)
from karria.shared.errors.exceptions import InvalidInputError
from payments.fakes import NOW

pytestmark = pytest.mark.unit


class TestCatalogue:
    def test_le_plan_sans_pub_vaut_4_99_par_mois(self) -> None:
        assert AD_FREE_PLAN.amount_cents == 499
        assert AD_FREE_PLAN.interval == "month"
        assert AD_FREE_PLAN.recurring is True
        assert AD_FREE_PLAN.lookup_key == "karria_ad_free_monthly"

    def test_les_cafes_ont_quatre_paliers_a_prix_fixes(self) -> None:
        assert COFFEE_TIERS == {1: 299, 2: 499, 3: 799, 5: 1299}
        assert coffee_price(1).product_name == "1 café pour l'équipe Karria"
        assert coffee_price(3).product_name == "3 cafés pour l'équipe Karria"
        assert coffee_price(5).lookup_key == "karria_coffee_5"
        assert coffee_price(2).recurring is False

    @pytest.mark.parametrize("count", [0, 4, 6, 11])
    def test_un_palier_inconnu_est_refuse(self, count: int) -> None:
        with pytest.raises(UnknownCoffeeTierError) as caught:
            coffee_price(count)
        assert caught.value.details["reason"] == "coffee_count_out_of_bounds"

    def test_le_prix_d_un_modele_vient_de_l_offre(self) -> None:
        offer = PremiumOffer(uuid4(), "Meridian", 699)
        spec = template_price(offer)
        assert spec.amount_cents == 699
        assert spec.lookup_key == f"karria_template_{offer.template_id}"
        assert spec.product_name == "Modèle de CV Meridian"

    def test_le_catalogue_range_les_cafes_par_taille(self) -> None:
        offer = PremiumOffer(uuid4(), "Meridian", 499)
        cat = catalog([offer])
        assert [count for count, _ in cat.coffees] == [1, 2, 3, 5]
        assert cat.ad_free is AD_FREE_PLAN
        assert cat.templates == (offer,)
        assert cat.currency == "eur"


class TestPrix:
    def test_l_abonnement_part_en_mode_abonnement(self) -> None:
        priced = price(CheckoutOrder(ProductKind.AD_FREE))
        assert priced.mode == "subscription"
        assert priced.amount_cents == 499
        assert priced.metadata == {"kind": "ad_free"}

    def test_les_cafes_portent_leur_nombre(self) -> None:
        priced = price(CheckoutOrder(ProductKind.COFFEE, coffee_count=3))
        assert priced.mode == "payment"
        assert priced.amount_cents == 799
        assert priced.metadata == {"kind": "coffee", "coffee_count": "3"}

    def test_un_modele_exige_son_identifiant(self) -> None:
        with pytest.raises(TemplateRequiredError) as caught:
            price(CheckoutOrder(ProductKind.TEMPLATE))
        assert caught.value.details["field"] == "template_id"

    def test_un_modele_qui_n_est_pas_en_vente_est_introuvable(self) -> None:
        template_id = uuid4()
        with pytest.raises(TemplateOfferNotFoundError):
            price(CheckoutOrder(ProductKind.TEMPLATE, template_id=template_id))
        with pytest.raises(TemplateOfferNotFoundError):
            price(
                CheckoutOrder(ProductKind.TEMPLATE, template_id=template_id),
                offer=PremiumOffer(uuid4(), "Autre", 499),
            )

    def test_un_modele_en_vente_prend_le_prix_de_documents(self) -> None:
        offer = PremiumOffer(uuid4(), "Meridian", 899)
        priced = price(
            CheckoutOrder(ProductKind.TEMPLATE, template_id=offer.template_id), offer=offer
        )
        assert priced.amount_cents == 899
        assert priced.mode == "payment"
        assert priced.metadata == {"kind": "template", "template_id": str(offer.template_id)}


def remote(
    subscription_id: str = "sub_1",
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    cancel_scheduled: bool = False,
) -> ProviderSubscription:
    return ProviderSubscription(
        subscription_id=subscription_id,
        customer_id="cus_1",
        status=status,
        current_period_ends_at=NOW + timedelta(days=30),
        cancel_scheduled=cancel_scheduled,
        ends_at=NOW + timedelta(days=30) if cancel_scheduled else None,
    )


class TestAbonnement:
    def test_une_ligne_neuve_ne_donne_rien(self) -> None:
        account = BillingAccount.opened(user_id=uuid4(), customer_id="cus_1")
        assert account.status is SubscriptionStatus.NONE
        assert account.is_ad_free(NOW) is False
        assert account.ad_free_until(NOW) is None

    def test_un_abonnement_actif_est_sans_pub_jusqu_a_la_fin_de_periode(self) -> None:
        account = BillingAccount.opened(user_id=uuid4(), customer_id="cus_1").synced(
            remote(), at=NOW
        )
        assert account.is_ad_free(NOW) is True
        assert account.ad_free_until(NOW) == NOW + timedelta(days=30)
        assert account.subscription_id == "sub_1"
        assert account.synced_at == NOW

    def test_une_resiliation_programmee_garde_l_acces_jusqu_a_la_fin(self) -> None:
        account = BillingAccount.opened(user_id=uuid4(), customer_id="cus_1").synced(
            remote(cancel_scheduled=True), at=NOW
        )
        assert account.cancel_at_period_end is True
        assert account.is_ad_free(NOW) is True
        assert account.ends_at == NOW + timedelta(days=30)

    def test_un_abonnement_resilie_ne_donne_plus_rien(self) -> None:
        account = BillingAccount.opened(user_id=uuid4(), customer_id="cus_1").synced(
            remote(status=SubscriptionStatus.CANCELED), at=NOW
        )
        assert account.is_ad_free(NOW) is False
        assert account.status.is_alive is False

    def test_un_prelevement_en_echec_ouvre_une_grace_d_une_semaine(self) -> None:
        account = (
            BillingAccount.opened(user_id=uuid4(), customer_id="cus_1")
            .synced(remote(), at=NOW)
            .synced(remote(status=SubscriptionStatus.PAST_DUE), at=NOW)
            .payment_failed(at=NOW)
        )
        assert account.past_due_since == NOW
        assert account.ad_free_until(NOW) == NOW + GRACE_PERIOD
        assert account.is_ad_free(NOW + GRACE_PERIOD - timedelta(seconds=1)) is True
        assert account.is_ad_free(NOW + GRACE_PERIOD) is False

    def test_la_grace_part_du_premier_echec_pas_du_dernier(self) -> None:
        account = (
            BillingAccount.opened(user_id=uuid4(), customer_id="cus_1")
            .synced(remote(status=SubscriptionStatus.PAST_DUE), at=NOW)
            .payment_failed(at=NOW)
        )
        later = account.payment_failed(at=NOW + timedelta(days=3))
        assert later is account

    def test_sans_echec_constate_la_grace_part_de_la_synchronisation(self) -> None:
        account = BillingAccount.opened(user_id=uuid4(), customer_id="cus_1").synced(
            remote(status=SubscriptionStatus.PAST_DUE), at=NOW
        )
        assert account.ad_free_until(NOW + timedelta(days=1)) == NOW + GRACE_PERIOD
        fresh = BillingAccount(
            id=uuid4(), user_id=uuid4(), customer_id="c", status=SubscriptionStatus.PAST_DUE
        )
        assert fresh.ad_free_until(NOW) == NOW + GRACE_PERIOD

    def test_un_paiement_regularise_efface_la_grace(self) -> None:
        account = (
            BillingAccount.opened(user_id=uuid4(), customer_id="cus_1")
            .synced(remote(status=SubscriptionStatus.PAST_DUE), at=NOW)
            .payment_failed(at=NOW)
            .synced(remote(), at=NOW + timedelta(days=2))
        )
        assert account.status is SubscriptionStatus.ACTIVE
        assert account.past_due_since is None

    def test_une_nouvelle_tardive_d_un_autre_abonnement_n_eteint_pas_le_courant(self) -> None:
        account = BillingAccount.opened(user_id=uuid4(), customer_id="cus_1").synced(
            remote("sub_2"), at=NOW
        )
        unchanged = account.synced(remote("sub_1", status=SubscriptionStatus.CANCELED), at=NOW)
        assert unchanged is account

    def test_un_nouvel_abonnement_remplace_un_abonnement_termine(self) -> None:
        account = (
            BillingAccount.opened(user_id=uuid4(), customer_id="cus_1")
            .synced(remote("sub_1", status=SubscriptionStatus.PAST_DUE), at=NOW)
            .payment_failed(at=NOW)
            .synced(remote("sub_1", status=SubscriptionStatus.CANCELED), at=NOW)
            .synced(remote("sub_2"), at=NOW)
        )
        assert account.subscription_id == "sub_2"
        assert account.status is SubscriptionStatus.ACTIVE
        assert account.past_due_since is None

    def test_un_client_vide_dans_la_notification_garde_le_client_connu(self) -> None:
        bare = ProviderSubscription("sub_1", "", SubscriptionStatus.ACTIVE, None, False, None)
        account = BillingAccount.opened(user_id=uuid4(), customer_id="cus_1").synced(bare, at=NOW)
        assert account.customer_id == "cus_1"
        assert account.is_ad_free(NOW) is True
        assert account.ad_free_until(NOW) is None


class TestMerci:
    def test_sans_cafe_rien_a_remercier(self) -> None:
        assert gratitude([]).coffees == 0
        template = Purchase(uuid4(), uuid4(), ProductKind.TEMPLATE, "cs_1", 499, "eur", NOW)
        assert gratitude([template]).coffees == 0

    def test_les_cafes_s_additionnent_et_le_dernier_se_retient(self) -> None:
        user = uuid4()
        purchases = [
            Purchase(uuid4(), user, ProductKind.COFFEE, "cs_1", 299, "eur", NOW, coffee_count=1),
            Purchase(
                uuid4(),
                user,
                ProductKind.COFFEE,
                "cs_2",
                799,
                "eur",
                NOW + timedelta(days=1),
                coffee_count=3,
            ),
        ]
        thanks = gratitude(purchases)
        assert thanks.coffees == 4
        assert thanks.total_cents == 1098
        assert thanks.last_at == NOW + timedelta(days=1)


class TestRetour:
    @pytest.mark.parametrize(
        "url",
        [
            "https://karria.fr/paiement",
            "https://www.karria.fr/merci?x=1",
            "karria://paiement/retour",
        ],
    )
    def test_karria_est_admis(self, url: str) -> None:
        assert validate_return_url(f" {url} ") == url

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/karria.fr",
            "http://karria.fr/paiement",
            "https://karria.fr.evil.example/",
            "https://user@karria.fr/",
            "javascript:alert(1)",
            "",
        ],
    )
    def test_le_reste_est_refuse(self, url: str) -> None:
        with pytest.raises(InvalidInputError) as caught:
            validate_return_url(url)
        assert caught.value.details["reason"] == "return_url_not_allowed"

    def test_les_parametres_s_ajoutent_sans_casser_ceux_qui_existent(self) -> None:
        assert with_query("karria://retour", status="success") == "karria://retour?status=success"
        assert (
            with_query("https://karria.fr/p?x=1", status="cancelled", to="a b")
            == "https://karria.fr/p?x=1&status=cancelled&to=a+b"
        )
