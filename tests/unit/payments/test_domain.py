"""Les règles de prix et de retour du module `payments`."""

from __future__ import annotations

import pytest

from karria.modules.payments.domain import (
    COFFEE_PRICE_CENTS,
    TEMPLATE_PRICE_CENTS,
    CheckoutOrder,
    PaymentType,
    price,
    validate_return_url,
)
from karria.shared.errors.exceptions import InvalidInputError

pytestmark = pytest.mark.unit


class TestPrice:
    def test_un_cafe_vaut_cinq_euros(self) -> None:
        priced = price(CheckoutOrder(PaymentType.COFFEE))

        assert priced.amount_cents == COFFEE_PRICE_CENTS
        assert priced.currency == "eur"
        assert "1 café)" in priced.label

    def test_plusieurs_cafes_se_multiplient_et_s_accordent(self) -> None:
        priced = price(CheckoutOrder(PaymentType.COFFEE, coffee_count=3))

        assert priced.amount_cents == 3 * COFFEE_PRICE_CENTS
        assert "3 cafés" in priced.label

    @pytest.mark.parametrize("count", [0, 11])
    def test_le_nombre_de_cafes_est_borne(self, count: int) -> None:
        with pytest.raises(InvalidInputError) as caught:
            price(CheckoutOrder(PaymentType.COFFEE, coffee_count=count))
        assert caught.value.details["reason"] == "coffee_count_out_of_bounds"

    def test_le_montant_libre_prime_sur_les_cafes(self) -> None:
        priced = price(CheckoutOrder(PaymentType.COFFEE, coffee_count=4, custom_amount_cents=250))

        assert priced.amount_cents == 250
        assert "2.50 €" in priced.label

    @pytest.mark.parametrize("cents", [149, 50_001])
    def test_le_montant_libre_est_borne(self, cents: int) -> None:
        with pytest.raises(InvalidInputError) as caught:
            price(CheckoutOrder(PaymentType.COFFEE, custom_amount_cents=cents))
        assert caught.value.details["reason"] == "amount_out_of_bounds"

    def test_un_modele_a_un_prix_unique_et_nomme_le_code(self) -> None:
        priced = price(CheckoutOrder(PaymentType.TEMPLATE, template_code="meridian"))

        assert priced.amount_cents == TEMPLATE_PRICE_CENTS
        assert priced.label.endswith("meridian")
        assert price(CheckoutOrder(PaymentType.TEMPLATE)).label == "Modèle de CV Premium"


class TestReturnUrl:
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
