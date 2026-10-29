"""Les routes du module `payments` : authentification, contrat, erreurs."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from karria.bootstrap.app import create_app
from karria.modules.payments.api.dependencies import get_payment_gateway
from karria.shared.auth import Principal, resolve_principal
from karria.shared.config import AppSettings, Environment, Settings
from payments.fakes import FakeGateway

pytestmark = pytest.mark.unit

USER = uuid4()


def client(gateway: FakeGateway | None, *, authenticated: bool = True) -> httpx.AsyncClient:
    app = create_app(Settings(app=AppSettings(environment=Environment.TEST)))
    app.dependency_overrides[get_payment_gateway] = lambda: gateway
    if authenticated:
        app.dependency_overrides[resolve_principal] = lambda: Principal(subject_id=USER)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestCheckoutSession:
    async def test_exige_la_session(self) -> None:
        async with client(FakeGateway(), authenticated=False) as http:
            response = await http.post(
                "/api/v1/payments/checkout-session", json={"payment_type": "coffee"}
            )

        assert response.status_code == 401

    async def test_ouvre_avec_le_retour_par_defaut(self) -> None:
        gateway = FakeGateway()
        async with client(gateway) as http:
            response = await http.post(
                "/api/v1/payments/checkout-session",
                json={"payment_type": "coffee", "coffee_count": 2},
            )

        assert response.status_code == 201
        body = response.json()
        assert body["amount_cents"] == 1_000
        assert body["checkout_url"].startswith("https://checkout.stripe.test/")
        metadata = gateway.opened[0]["metadata"]
        assert isinstance(metadata, dict)
        assert metadata["user_id"] == str(USER)
        assert str(gateway.opened[0]["success_url"]).startswith("https://karria.fr/paiement?")

    async def test_le_montant_libre_est_converti_en_centimes(self) -> None:
        gateway = FakeGateway()
        async with client(gateway) as http:
            response = await http.post(
                "/api/v1/payments/checkout-session",
                json={"payment_type": "coffee", "custom_amount_eur": 2.5},
            )

        assert response.status_code == 201
        assert response.json()["amount_cents"] == 250

    async def test_une_adresse_de_retour_hors_karria_fait_422(self) -> None:
        async with client(FakeGateway()) as http:
            response = await http.post(
                "/api/v1/payments/checkout-session",
                json={"payment_type": "coffee", "return_url": "https://evil.example/"},
            )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "return_url_not_allowed"

    async def test_sans_prestataire_503(self) -> None:
        async with client(None) as http:
            response = await http.post(
                "/api/v1/payments/checkout-session", json={"payment_type": "coffee"}
            )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "dependency_failure"


class TestStatus:
    async def test_l_etat_de_sa_session(self) -> None:
        gateway = FakeGateway()
        async with client(gateway) as http:
            opened = await http.post(
                "/api/v1/payments/checkout-session", json={"payment_type": "template"}
            )
            response = await http.get(f"/api/v1/payments/status/{opened.json()['session_id']}")

        assert response.status_code == 200
        assert response.json() == {
            "session_id": opened.json()["session_id"],
            "status": "unpaid",
            "payment_type": "template",
        }

    async def test_une_session_inconnue_fait_404(self) -> None:
        async with client(FakeGateway()) as http:
            response = await http.get("/api/v1/payments/status/cs_x")

        assert response.status_code == 404


class TestWebhook:
    async def test_un_evenement_signe_est_accuse(self) -> None:
        async with client(FakeGateway()) as http:
            response = await http.post(
                "/api/v1/payments/webhook", content=b"cs_1", headers={"stripe-signature": "ok"}
            )

        assert response.status_code == 200
        assert response.json() == {"received": True, "type": "checkout.session.completed"}

    async def test_sans_signature_422(self) -> None:
        async with client(FakeGateway(), authenticated=False) as http:
            response = await http.post("/api/v1/payments/webhook", content=b"{}")

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "signature_missing"
