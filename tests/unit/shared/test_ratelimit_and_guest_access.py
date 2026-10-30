"""Le limiteur du socle, l'accès anonyme compté, et la porte d'IA refusée par défaut."""

from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from karria.shared.auth import (
    AI_CONSENT_REQUIRED,
    ANONYMOUS_RATE_LIMITED,
    AiConsentedPrincipalDep,
    Principal,
    RefusingAiConsentGate,
    allow_anonymous,
    get_ai_consent_gate,
    resolve_principal,
)
from karria.shared.errors import AuthorizationError, register_exception_handlers
from karria.shared.http import SlidingWindowLimiter, client_ip

pytestmark = pytest.mark.unit


class _Horloge:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


class TestSlidingWindowLimiter:
    def test_admet_jusqu_au_plafond_puis_dit_combien_attendre(self) -> None:
        horloge = _Horloge()
        limiter = SlidingWindowLimiter(max_hits=2, window_seconds=60, monotonic=horloge)

        assert limiter.hit("a") is None
        horloge.t += 10
        assert limiter.hit("a") is None
        retry = limiter.hit("a")

        assert retry == 51  # la fenêtre du premier passage se ferme dans 50 s, +1
        # Un refus ne compte pas : le délai annoncé suffit.
        horloge.t += 51
        assert limiter.hit("a") is None

    def test_les_cles_sont_independantes(self) -> None:
        limiter = SlidingWindowLimiter(max_hits=1, window_seconds=60, monotonic=_Horloge())
        assert limiter.hit("a") is None
        assert limiter.hit("b") is None
        assert limiter.hit("a") is not None

    def test_les_origines_oubliees_laissent_la_place(self) -> None:
        horloge = _Horloge()
        limiter = SlidingWindowLimiter(max_hits=5, window_seconds=60, monotonic=horloge, max_keys=3)
        for key in ("a", "b", "c"):
            limiter.hit(key)
        assert limiter.tracked_keys == 3

        horloge.t += 120  # toutes périmées : elles partent avant d'ajouter la quatrième
        limiter.hit("d")
        assert limiter.tracked_keys == 1

        # Plein de fenêtres vivantes : la plus ancienne cède.
        limiter.hit("e")
        limiter.hit("f")
        limiter.hit("g")
        assert limiter.tracked_keys == 3

    def test_refuse_un_quota_absurde(self) -> None:
        with pytest.raises(ValueError, match="strictement positifs"):
            SlidingWindowLimiter(max_hits=0, window_seconds=60)


def _app(limiter: SlidingWindowLimiter) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    guest = allow_anonymous(limiter, scope="test")

    @app.get("/public")
    async def public(principal: Annotated[Principal | None, Depends(guest)]) -> dict[str, bool]:
        return {"anonymous": principal is None}

    @app.get("/ia")
    async def ia(principal: AiConsentedPrincipalDep) -> dict[str, str]:
        return {"subject": str(principal.subject_id)}

    return app


class TestAllowAnonymous:
    async def test_un_anonyme_passe_puis_est_compte_par_origine(self) -> None:
        app = _app(SlidingWindowLimiter(max_hits=1, window_seconds=60, monotonic=_Horloge()))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            first = await client.get(
                "/public", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
            )
            second = await client.get("/public", headers={"X-Forwarded-For": "203.0.113.9"})
            other = await client.get("/public", headers={"X-Forwarded-For": "203.0.113.10"})

        assert first.status_code == 200
        assert first.json() == {"anonymous": True}
        assert second.status_code == 429
        body = second.json()["error"]
        assert body["details"]["reason"] == ANONYMOUS_RATE_LIMITED
        assert body["details"]["retry_after"] >= 1
        assert other.status_code == 200

    async def test_une_personne_connectee_n_est_pas_comptee(self) -> None:
        app = _app(SlidingWindowLimiter(max_hits=1, window_seconds=60, monotonic=_Horloge()))
        principal = Principal(subject_id=uuid4())
        app.dependency_overrides[resolve_principal] = lambda: principal
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            for _ in range(3):
                response = await client.get("/public")
                assert response.status_code == 200
                assert response.json() == {"anonymous": False}

    def test_l_origine_est_le_premier_relais_sinon_le_client(self) -> None:
        class Requete:
            def __init__(self, headers: dict[str, str], client: object) -> None:
                self.headers = headers
                self.client = client

        class Client:
            host = "10.1.1.1"

        assert client_ip(Requete({"x-forwarded-for": " 1.2.3.4 , 5.6.7.8"}, Client())) == "1.2.3.4"  # type: ignore[arg-type]
        assert client_ip(Requete({}, Client())) == "10.1.1.1"  # type: ignore[arg-type]
        assert client_ip(Requete({}, None)) == ""  # type: ignore[arg-type]


class TestPorteIaParDefaut:
    async def test_refuse_bruyamment_tant_que_rien_n_est_branche(self) -> None:
        gate = RefusingAiConsentGate()
        with pytest.raises(AuthorizationError) as exc:
            await gate.ensure(Principal(subject_id=uuid4()))
        assert exc.value.details == {"reason": AI_CONSENT_REQUIRED}
        assert isinstance(get_ai_consent_gate(), RefusingAiConsentGate)

    async def test_une_route_d_ia_repond_403_sans_branchement_et_401_sans_session(self) -> None:
        app = _app(SlidingWindowLimiter(max_hits=5, window_seconds=60))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            anonymous = await client.get("/ia")
            app.dependency_overrides[resolve_principal] = lambda: Principal(subject_id=uuid4())
            connected = await client.get("/ia")

        assert anonymous.status_code == 401
        assert connected.status_code == 403
        assert connected.json()["error"]["details"]["reason"] == AI_CONSENT_REQUIRED
