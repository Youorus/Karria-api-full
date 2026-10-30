"""La porte d'IA, branchée par `bootstrap` : aucune route d'IA ne sert sans consentement (ADR-0025).

Le contrôle est **serveur** : l'app ne peut pas le contourner. On exerce
ici chaque route qui confie une donnée de la personne à un modèle, avec la
vraie couture (`wiring.py`) et les seuls dépôts d'`identity` substitués.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from doubles import IdentityEnv
from karria.bootstrap.wiring import IdentityAiConsentGate, ai_consent_gate_from_identity
from karria.modules.cv.api import dependencies as cv_deps
from karria.modules.identity.application import EnsureAiConsent
from karria.shared.auth import AI_CONSENT_REQUIRED, Principal, get_ai_consent_gate
from karria.shared.errors import InvalidInputError

pytestmark = pytest.mark.unit

AUDIO = {"audio_file": ("r.m4a", b"\x00" * 64, "audio/m4a")}
IMAGE = {"image": ("p.jpg", b"\xff\xd8\xff" + b"\x00" * 64, "image/jpeg")}
DOC = {"file": ("cv.pdf", b"%PDF-1.4" + b"\x00" * 64, "application/pdf")}


async def _accept(client: AsyncClient) -> None:
    disclosure = (await client.get("/api/v1/auth/me/ai-consent")).json()["disclosure"]
    accepted = await client.put(
        "/api/v1/auth/me/ai-consent", json={"version": disclosure["version"]}
    )
    assert accepted.status_code == 200


@pytest.fixture
def connected(identity_env: IdentityEnv, authenticate: Callable[..., Principal]) -> Principal:
    return authenticate(uuid4())


class _Temoin:
    """Un cas d'usage qui atteste qu'on l'a atteint : la porte a donc laissé passer."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise InvalidInputError("atteint", details={"reason": "temoin_atteint"})


@pytest.fixture
def temoin(app: FastAPI) -> Iterator[None]:
    """Remplace les cas d'usage d'IA du CV par le témoin : aucune base, aucun modèle."""
    overrides = {cv_deps.get_upload_photo: _Temoin, cv_deps.get_transcribe_voice: _Temoin}
    app.dependency_overrides.update(overrides)
    yield
    for key in overrides:
        app.dependency_overrides.pop(key, None)


class TestLesRoutesDIaRefusentSansConsentement:
    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            ("POST", "/api/v1/assistant/messages", {"json": {"content": "serveur à Lyon"}}),
            (
                "POST",
                "/api/v1/cv/draft/interpret",
                {"json": {"chapter": "identity", "answer": "Je suis Awa", "correction_of": None}},
            ),
            ("POST", "/api/v1/cv/import", {"files": DOC}),
            ("POST", "/api/v1/voice/transcribe", {"files": AUDIO}),
            ("POST", "/api/v1/cv/photo", {"files": IMAGE, "data": {"enhance": "true"}}),
            ("POST", "/api/v1/cv/me/generated-cvs", {"json": {"template_id": str(uuid4())}}),
        ],
    )
    async def test_403_ai_consent_required(
        self, client: AsyncClient, connected: Principal, method: str, path: str, kwargs: dict
    ) -> None:
        response = await client.request(method, path, **kwargs)

        assert response.status_code == 403, response.text
        details = response.json()["error"]["details"]
        assert details["reason"] == AI_CONSENT_REQUIRED
        assert details["version"].startswith("2026-10.")
        assert details["providers"] == ["gemini"]

    async def test_la_photo_sans_amelioration_ne_passe_pas_par_la_porte(
        self, client: AsyncClient, connected: Principal, temoin: None
    ) -> None:
        # Sans amélioration, rien ne part chez un tiers : la porte ne joue pas.
        response = await client.post("/api/v1/cv/photo", files=IMAGE, data={"enhance": "false"})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "temoin_atteint"

    async def test_une_fois_accepte_la_porte_laisse_passer(
        self, client: AsyncClient, connected: Principal, temoin: None
    ) -> None:
        await _accept(client)

        response = await client.post("/api/v1/voice/transcribe", files=AUDIO)

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "temoin_atteint"

    async def test_sans_session_c_est_401_avant_la_porte(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/voice/transcribe", files=AUDIO)
        assert response.status_code == 401


class TestBranchement:
    def test_bootstrap_branche_la_porte_sur_identity(self, app: FastAPI) -> None:
        assert app.dependency_overrides[get_ai_consent_gate] is ai_consent_gate_from_identity

    async def test_la_porte_traduit_le_principal_en_identifiant(self) -> None:
        env = IdentityEnv.build()
        gate = ai_consent_gate_from_identity(
            EnsureAiConsent(consents=env.ai_consents, catalog=env.ai_catalog)
        )
        assert isinstance(gate, IdentityAiConsentGate)
        with pytest.raises(Exception, match="fournisseurs d'IA"):
            await gate.ensure(Principal(subject_id=uuid4()))
