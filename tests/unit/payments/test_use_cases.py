"""Les cas d'usage du module `payments`, avec un prestataire en mémoire."""

from __future__ import annotations

from uuid import uuid4

import pytest

from karria.modules.payments.application import GetPaymentStatus, HandleWebhook, OpenCheckout
from karria.modules.payments.domain import CheckoutOrder, PaymentsUnavailableError, PaymentType
from karria.shared.errors.exceptions import EntityNotFoundError, InvalidInputError
from payments.fakes import FakeGateway

pytestmark = pytest.mark.unit


class TestOpenCheckout:
    async def test_ouvre_au_nom_de_la_personne_avec_le_prix_decide(self) -> None:
        gateway = FakeGateway()
        user = uuid4()

        opened = await OpenCheckout(gateway).execute(
            user, CheckoutOrder(PaymentType.COFFEE, coffee_count=2), "https://karria.fr/merci"
        )

        assert opened.amount_cents == 1_000
        assert opened.session.url.endswith(opened.session.id)
        call = gateway.opened[0]
        assert call["metadata"] == {
            "payment_type": "coffee",
            "user_id": str(user),
            "template_code": "",
            "amount_cents": "1000",
        }
        assert call["success_url"] == (
            "https://karria.fr/merci?session_id={CHECKOUT_SESSION_ID}&status=success"
        )
        assert call["cancel_url"] == "https://karria.fr/merci?status=cancelled"

    async def test_une_adresse_de_retour_avec_parametres_garde_les_siens(self) -> None:
        gateway = FakeGateway()

        await OpenCheckout(gateway).execute(
            uuid4(), CheckoutOrder(PaymentType.TEMPLATE, template_code="m"), "karria://retour?x=1"
        )

        assert gateway.opened[0]["cancel_url"] == "karria://retour?x=1&status=cancelled"

    async def test_une_adresse_hors_karria_est_refusee_avant_tout_appel(self) -> None:
        gateway = FakeGateway()

        with pytest.raises(InvalidInputError):
            await OpenCheckout(gateway).execute(
                uuid4(), CheckoutOrder(PaymentType.COFFEE), "https://evil.example/"
            )
        assert gateway.opened == []

    async def test_sans_prestataire_pas_de_demonstration(self) -> None:
        with pytest.raises(PaymentsUnavailableError):
            await OpenCheckout(None).execute(
                uuid4(), CheckoutOrder(PaymentType.COFFEE), "https://karria.fr/"
            )


class TestGetPaymentStatus:
    async def test_rend_l_etat_a_la_personne_qui_a_ouvert(self) -> None:
        gateway = FakeGateway()
        user = uuid4()
        opened = await OpenCheckout(gateway).execute(
            user, CheckoutOrder(PaymentType.COFFEE), "https://karria.fr/"
        )

        record = await GetPaymentStatus(gateway).execute(user, opened.session.id)

        assert record.paid is False
        assert record.payment_type == "coffee"

    async def test_la_session_d_une_autre_personne_est_introuvable(self) -> None:
        gateway = FakeGateway()
        opened = await OpenCheckout(gateway).execute(
            uuid4(), CheckoutOrder(PaymentType.COFFEE), "https://karria.fr/"
        )

        with pytest.raises(EntityNotFoundError):
            await GetPaymentStatus(gateway).execute(uuid4(), opened.session.id)
        with pytest.raises(EntityNotFoundError):
            await GetPaymentStatus(gateway).execute(uuid4(), "cs_inconnue")

    async def test_sans_prestataire(self) -> None:
        with pytest.raises(PaymentsUnavailableError):
            await GetPaymentStatus(None).execute(uuid4(), "cs_1")


class TestHandleWebhook:
    def test_un_evenement_signe_est_traduit(self) -> None:
        event = HandleWebhook(FakeGateway()).execute(b"cs_9", "ok")

        assert event.kind == "checkout.session.completed"
        assert event.session_id == "cs_9"

    def test_sans_signature_ou_signature_fausse(self) -> None:
        handle = HandleWebhook(FakeGateway())

        with pytest.raises(InvalidInputError) as missing:
            handle.execute(b"", None)
        assert missing.value.details["reason"] == "signature_missing"
        with pytest.raises(InvalidInputError) as wrong:
            handle.execute(b"x", "faux")
        assert wrong.value.details["reason"] == "signature_invalid"

    def test_sans_prestataire(self) -> None:
        with pytest.raises(PaymentsUnavailableError):
            HandleWebhook(None).execute(b"", "ok")
