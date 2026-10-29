"""Les routes du module `payments` : authentification, contrat, interrupteur, page de retour."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from doubles import FakeClock
from karria.bootstrap.app import create_app
from karria.modules.payments.api.dependencies import (
    get_account_directory,
    get_account_repository,
    get_clock,
    get_payment_gateway,
    get_platform_flags,
    get_purchase_repository,
    get_template_store,
    get_webhook_ledger,
)
from karria.modules.payments.application import BillingEventKind, PlatformFlags
from karria.modules.payments.domain import BillingAccount, SubscriptionStatus
from karria.shared.auth import Principal, resolve_principal
from karria.shared.config import AppSettings, Environment, Settings, get_settings
from karria.shared.config.settings import PaymentsSettings
from karria.shared.database import NullUnitOfWork
from karria.shared.http.dependencies import get_unit_of_work
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

USER = uuid4()


class Harness:
    """Une application montée sur des doublures, un client HTTP dessus."""

    def __init__(
        self,
        gateway: FakeGateway | None,
        *,
        authenticated: bool = True,
        flags: PlatformFlags | None = None,
    ) -> None:
        self.gateway = gateway
        self.accounts = InMemoryAccounts()
        self.purchases = InMemoryPurchases()
        self.ledger = InMemoryLedger()
        self.templates = FakeTemplateStore()
        self.directory = FakeDirectory()
        self.clock = FakeClock(NOW)
        settings = Settings(
            app=AppSettings(environment=Environment.TEST, public_url="https://api.karria.fr"),
            payments=PaymentsSettings(secret_key=SecretStr("sk_test" if gateway else "")),
        )
        app = create_app(settings)
        # La configuration du processus est celle du `.env` : la route lit celle-ci.
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_payment_gateway] = lambda: gateway
        app.dependency_overrides[get_account_repository] = lambda: self.accounts
        app.dependency_overrides[get_purchase_repository] = lambda: self.purchases
        app.dependency_overrides[get_webhook_ledger] = lambda: self.ledger
        app.dependency_overrides[get_template_store] = lambda: self.templates
        app.dependency_overrides[get_account_directory] = lambda: self.directory
        app.dependency_overrides[get_clock] = lambda: self.clock
        app.dependency_overrides[get_unit_of_work] = NullUnitOfWork
        if flags is not None:
            app.dependency_overrides[get_platform_flags] = lambda: flags
        if authenticated:
            app.dependency_overrides[resolve_principal] = lambda: Principal(subject_id=USER)
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )


class TestCatalogue:
    async def test_exige_la_session(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        assert (await h.http.get("/api/v1/payments/catalog")).status_code == 401

    async def test_rend_le_catalogue_et_l_interrupteur(self) -> None:
        h = Harness(FakeGateway())
        offer = h.templates.sell("Meridian", 499)
        response = await h.http.get("/api/v1/payments/catalog")

        assert response.status_code == 200
        body = response.json()
        assert body["currency"] == "eur"
        assert body["ad_free"] == {
            "lookup_key": "karria_ad_free_monthly",
            "label": "Karria sans pub",
            "amount_cents": 499,
            "currency": "eur",
            "interval": "month",
        }
        assert [c["count"] for c in body["coffees"]] == [1, 2, 3, 5]
        assert body["coffees"][3]["amount_cents"] == 1299
        assert body["templates"] == [
            {"template_id": str(offer.template_id), "label": "Meridian", "amount_cents": 499}
        ]
        assert body["enabled"] == {"ios": True, "android": True}

    async def test_sans_prestataire_l_interrupteur_est_eteint(self) -> None:
        h = Harness(None)
        body = (await h.http.get("/api/v1/payments/catalog")).json()
        assert body["enabled"] == {"ios": False, "android": False}

    async def test_l_interrupteur_ios_se_coupe_par_la_configuration(self) -> None:
        h = Harness(FakeGateway(), flags=PlatformFlags(ios=False, android=True, configured=True))
        body = (await h.http.get("/api/v1/payments/catalog")).json()
        assert body["enabled"] == {"ios": False, "android": True}


class TestCheckoutSession:
    async def test_exige_la_session(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        response = await h.http.post(
            "/api/v1/payments/checkout-session", json={"payment_type": "coffee"}
        )
        assert response.status_code == 401

    async def test_ouvre_avec_le_retour_par_defaut_et_la_page_du_serveur(self) -> None:
        h = Harness(FakeGateway())
        assert h.gateway is not None
        response = await h.http.post(
            "/api/v1/payments/checkout-session",
            json={"payment_type": "coffee", "coffee_count": 2},
        )

        assert response.status_code == 201
        body = response.json()
        assert body == {
            "checkout_url": "https://checkout.stripe.test/cs_1",
            "session_id": "cs_1",
            "payment_type": "coffee",
            "mode": "payment",
            "amount_cents": 499,
            "currency": "eur",
        }
        request = h.gateway.opened[0]
        assert request.metadata["user_id"] == str(USER)
        assert request.success_url.startswith(
            "https://api.karria.fr/api/v1/payments/return?to=https%3A%2F%2Fkarria.fr%2Fpaiement"
        )

    async def test_ouvre_un_abonnement_vers_l_app(self) -> None:
        h = Harness(FakeGateway())
        assert h.gateway is not None
        response = await h.http.post(
            "/api/v1/payments/checkout-session",
            json={"payment_type": "ad_free", "return_url": "karria://paiement/retour"},
        )
        assert response.status_code == 201
        assert response.json()["mode"] == "subscription"
        assert "to=karria%3A%2F%2Fpaiement%2Fretour" in h.gateway.opened[0].cancel_url

    async def test_un_palier_de_cafes_inconnu_fait_422(self) -> None:
        h = Harness(FakeGateway())
        response = await h.http.post(
            "/api/v1/payments/checkout-session", json={"payment_type": "coffee", "coffee_count": 4}
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "coffee_count_out_of_bounds"

    async def test_un_modele_inconnu_fait_404_et_un_possede_409(self) -> None:
        h = Harness(FakeGateway())
        unknown = await h.http.post(
            "/api/v1/payments/checkout-session",
            json={"payment_type": "template", "template_id": str(uuid4())},
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["details"]["reason"] == "template_not_found"

        offer = h.templates.sell()
        h.templates.owned.add((USER, offer.template_id))
        owned = await h.http.post(
            "/api/v1/payments/checkout-session",
            json={"payment_type": "template", "template_id": str(offer.template_id)},
        )
        assert owned.status_code == 409
        assert owned.json()["error"]["details"]["reason"] == "template_already_owned"

    async def test_un_abonnement_vivant_fait_409(self) -> None:
        h = Harness(FakeGateway())
        h.accounts.rows[USER] = BillingAccount(
            id=uuid4(), user_id=USER, customer_id="cus_1", status=SubscriptionStatus.ACTIVE
        )
        response = await h.http.post(
            "/api/v1/payments/checkout-session", json={"payment_type": "ad_free"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["details"]["reason"] == "subscription_already_active"

    async def test_une_adresse_de_retour_hors_karria_fait_422(self) -> None:
        h = Harness(FakeGateway())
        response = await h.http.post(
            "/api/v1/payments/checkout-session",
            json={"payment_type": "coffee", "return_url": "https://evil.example/"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "return_url_not_allowed"

    async def test_sans_prestataire_503(self) -> None:
        h = Harness(None)
        response = await h.http.post(
            "/api/v1/payments/checkout-session", json={"payment_type": "coffee"}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "dependency_failure"


class TestStatus:
    async def test_l_etat_de_sa_session(self) -> None:
        h = Harness(FakeGateway())
        opened = await h.http.post(
            "/api/v1/payments/checkout-session", json={"payment_type": "coffee"}
        )
        response = await h.http.get(f"/api/v1/payments/status/{opened.json()['session_id']}")
        assert response.status_code == 200
        assert response.json() == {
            "session_id": "cs_1",
            "status": "unpaid",
            "payment_type": "coffee",
            "fulfilled": False,
        }

    async def test_une_session_inconnue_fait_404(self) -> None:
        h = Harness(FakeGateway())
        assert (await h.http.get("/api/v1/payments/status/cs_x")).status_code == 404


class TestMe:
    async def test_un_compte_sans_rien(self) -> None:
        h = Harness(FakeGateway())
        response = await h.http.get("/api/v1/payments/me")
        assert response.status_code == 200
        assert response.json() == {
            "ad_free": {
                "active": False,
                "status": "none",
                "until": None,
                "cancel_at_period_end": False,
                "manageable": False,
            },
            "thanks": {"coffees": 0, "total_cents": 0, "last_at": None},
            "enabled": {"ios": True, "android": True},
        }

    async def test_un_abonne_sans_pub(self) -> None:
        h = Harness(FakeGateway())
        h.accounts.rows[USER] = BillingAccount(
            id=uuid4(),
            user_id=USER,
            customer_id="cus_1",
            status=SubscriptionStatus.ACTIVE,
            current_period_ends_at=NOW + timedelta(days=30),
            cancel_at_period_end=True,
        )
        body = (await h.http.get("/api/v1/payments/me")).json()
        assert body["ad_free"]["active"] is True
        assert body["ad_free"]["status"] == "active"
        assert body["ad_free"]["cancel_at_period_end"] is True
        assert body["ad_free"]["manageable"] is True
        assert body["ad_free"]["until"].startswith("2026-11-28T12:00:00")

    async def test_sans_prestataire_repond_sans_lire(self) -> None:
        h = Harness(None)
        body = (await h.http.get("/api/v1/payments/me")).json()
        assert body["ad_free"]["active"] is False
        assert body["enabled"] == {"ios": False, "android": False}


class TestPortal:
    async def test_ouvre_le_portail(self) -> None:
        h = Harness(FakeGateway())
        await h.http.post("/api/v1/payments/checkout-session", json={"payment_type": "coffee"})
        response = await h.http.post(
            "/api/v1/payments/portal-session", json={"return_url": "karria://paiement/retour"}
        )
        assert response.status_code == 201
        assert response.json() == {"url": "https://billing.stripe.test/cus_1"}

    async def test_sans_corps_le_retour_par_defaut(self) -> None:
        h = Harness(FakeGateway())
        assert h.gateway is not None
        await h.http.post("/api/v1/payments/checkout-session", json={"payment_type": "coffee"})
        response = await h.http.post("/api/v1/payments/portal-session")
        assert response.status_code == 201
        assert "to=https%3A%2F%2Fkarria.fr%2Fpaiement" in h.gateway.portals[0][1]

    async def test_sans_client_404(self) -> None:
        h = Harness(FakeGateway())
        response = await h.http.post("/api/v1/payments/portal-session")
        assert response.status_code == 404
        assert response.json()["error"]["details"]["reason"] == "billing_account_not_found"


class TestReturnPage:
    async def test_renvoie_vers_l_app_avec_l_issue(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        response = await h.http.get(
            "/api/v1/payments/return",
            params={"to": "karria://paiement/retour", "status": "success", "session_id": "cs_1"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        page = response.text
        assert "Merci" in page
        assert 'url=karria://paiement/retour?status=success&amp;session_id=cs_1"' in page
        # Dans un <script>, pas d'entité HTML : le `&` est un `\\u0026` JSON.
        assert (
            'location.replace("karria://paiement/retour?status=success\u0026session_id=cs_1")'
            in page
        )
        assert "Revenir dans Karria" in page

    async def test_une_destination_hors_karria_n_est_pas_suivie(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        response = await h.http.get(
            "/api/v1/payments/return", params={"to": "https://evil.example/", "status": "cancelled"}
        )
        assert response.status_code == 200
        assert "evil.example" not in response.text
        assert "http-equiv" not in response.text
        assert "Rien n'a été payé" in response.text

    async def test_sans_destination_ni_issue_connue(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        response = await h.http.get("/api/v1/payments/return", params={"status": "autre"})
        assert response.status_code == 200
        assert "Merci" in response.text
        portal = await h.http.get(
            "/api/v1/payments/return", params={"to": "https://karria.fr/compte", "status": "portal"}
        )
        assert "C'est noté" in portal.text
        assert "https://karria.fr/compte?status=portal" in portal.text

    async def test_les_caracteres_dangereux_sont_echappes(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        response = await h.http.get(
            "/api/v1/payments/return",
            params={"to": 'karria://retour?x="></script><script>alert(1)</script>'},
        )
        assert "<script>alert" not in response.text
        assert "</script><script>" not in response.text.replace("</script>\n</body>", "").replace(
            "<script>location", ""
        )


class TestWebhook:
    async def test_un_evenement_signe_est_accuse(self) -> None:
        gateway = FakeGateway()
        gateway.event("ok", BillingEventKind.IGNORED)
        h = Harness(gateway, authenticated=False)
        response = await h.http.post(
            "/api/v1/payments/webhook", content=b"{}", headers={"stripe-signature": "ok"}
        )
        assert response.status_code == 200
        assert response.json() == {"received": True, "type": "ignored", "applied": False}

    async def test_sans_signature_422(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        response = await h.http.post("/api/v1/payments/webhook", content=b"{}")
        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "signature_missing"

    async def test_un_corps_trop_gros_est_refuse(self) -> None:
        h = Harness(FakeGateway(), authenticated=False)
        response = await h.http.post(
            "/api/v1/payments/webhook",
            content=b"x" * (256 * 1024 + 1),
            headers={"stripe-signature": "ok"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "signature_invalid"
