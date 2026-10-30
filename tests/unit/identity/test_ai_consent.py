"""Le consentement à l'IA (ADR-0025) : le domaine, les cas d'usage, le catalogue, les routes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from doubles import IdentityEnv, StaticAiProviderCatalog
from karria.modules.identity.application import (
    AcceptAiConsent,
    EnsureAiConsent,
    GetAiConsentStatus,
    RevokeAiConsent,
)
from karria.modules.identity.domain import (
    AI_DISCLOSURE_REVISION,
    PURPOSE_TASKS,
    AiConsent,
    AiConsentRequiredError,
    AiConsentStatus,
    AiConsentVersionMismatchError,
    AiDisclosure,
    AiPurpose,
    Consent,
    EmailAddress,
    User,
    provider_name,
    user_data_tasks,
)
from karria.modules.identity.infrastructure import SettingsAiProviderCatalog
from karria.shared.auth import Principal
from karria.shared.config import (
    AppSettings,
    Environment,
    GeminiSettings,
    LlmProviderSettings,
    LlmProvidersSettings,
    LlmSettings,
    LlmTaskRoutes,
    Settings,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


def _user() -> User:
    return User(
        id=uuid4(),
        email=EmailAddress("awa@exemple.fr"),
        email_verified_at=NOW,
        consent=Consent(terms_version="1.0", privacy_policy_version="1.0", accepted_at=NOW),
        created_at=NOW,
        updated_at=NOW,
    )


class TestDisclosure:
    def test_la_version_porte_la_revision_et_une_empreinte_stable(self) -> None:
        a = AiDisclosure.build(["gemini", " Gemini ", ""])
        b = AiDisclosure.build(["gemini"])

        assert a == b
        assert a.providers == ("gemini",)
        assert a.purposes == tuple(AiPurpose)
        assert a.version.startswith(f"{AI_DISCLOSURE_REVISION}.")
        assert len(a.version.split(".")[-1]) == 8

    def test_un_fournisseur_de_plus_change_la_version(self) -> None:
        assert (
            AiDisclosure.build(["gemini"]).version
            != AiDisclosure.build(["gemini", "anthropic"]).version
        )
        # L'ordre ne compte pas : même information, même version.
        assert (
            AiDisclosure.build(["anthropic", "gemini"]).version
            == AiDisclosure.build(["gemini", "anthropic"]).version
        )

    def test_les_noms_lisibles_suivent_les_identifiants(self) -> None:
        disclosure = AiDisclosure.build(["openai", "gemini", "inconnu"])
        assert disclosure.provider_names == ("Google Gemini", "inconnu", "OpenAI")
        assert provider_name("anthropic") == "Anthropic Claude"

    def test_chaque_usage_nomme_au_moins_une_tache(self) -> None:
        assert set(PURPOSE_TASKS) == set(AiPurpose)
        assert all(PURPOSE_TASKS[p] for p in AiPurpose)
        tasks = user_data_tasks()
        assert "search_dialogue" in tasks
        assert "enrich" not in tasks
        assert len(tasks) == len(set(tasks))


class TestConsent:
    def test_couvre_l_information_qu_il_a_acceptee_et_pas_une_autre(self) -> None:
        disclosure = AiDisclosure.build(["gemini"])
        consent = AiConsent.given(uuid4(), disclosure, at=NOW)

        assert consent.active
        assert consent.covers(disclosure)
        assert not consent.covers(AiDisclosure.build(["gemini", "openai"]))
        assert AiConsentStatus(disclosure=disclosure, consent=consent).granted
        assert AiConsentStatus(disclosure=disclosure, consent=None).required

    def test_le_retrait_garde_sa_premiere_date(self) -> None:
        consent = AiConsent.given(uuid4(), AiDisclosure.build(["gemini"]), at=NOW)
        revoked = consent.revoked(at=NOW + timedelta(days=1))
        again = revoked.revoked(at=NOW + timedelta(days=9))

        assert revoked.revoked_at == NOW + timedelta(days=1)
        assert again is revoked
        assert not revoked.active
        assert not revoked.covers(AiDisclosure.build(["gemini"]))


@pytest.fixture
def env() -> IdentityEnv:
    return IdentityEnv.build()


def _status(env: IdentityEnv) -> GetAiConsentStatus:
    return GetAiConsentStatus(consents=env.ai_consents, catalog=env.ai_catalog)


def _accept(env: IdentityEnv) -> AcceptAiConsent:
    return AcceptAiConsent(consents=env.ai_consents, catalog=env.ai_catalog, clock=env.clock)


def _revoke(env: IdentityEnv) -> RevokeAiConsent:
    return RevokeAiConsent(consents=env.ai_consents, catalog=env.ai_catalog, clock=env.clock)


def _ensure(env: IdentityEnv) -> EnsureAiConsent:
    return EnsureAiConsent(consents=env.ai_consents, catalog=env.ai_catalog)


class TestCasDUsage:
    async def test_sans_accord_le_statut_est_requis_et_la_porte_refuse(
        self, env: IdentityEnv
    ) -> None:
        user = uuid4()
        status = await _status(env).execute(user)

        assert status.required
        assert status.disclosure.providers == ("gemini",)
        assert env.ai_catalog.asked == [user_data_tasks()]
        with pytest.raises(AiConsentRequiredError) as exc:
            await _ensure(env).execute(user)
        assert exc.value.details == {
            "reason": "ai_consent_required",
            "version": status.disclosure.version,
            "providers": ["gemini"],
        }

    async def test_accepter_la_version_courante_ouvre_la_porte(self, env: IdentityEnv) -> None:
        user = uuid4()
        version = (await _status(env).execute(user)).disclosure.version

        status = await _accept(env).execute(user, version=version)

        assert status.granted
        assert status.consent is not None
        assert status.consent.accepted_at == env.clock.now()
        await _ensure(env).execute(user)  # ne lève plus

    async def test_une_acceptation_deja_valable_n_est_pas_reecrite(self, env: IdentityEnv) -> None:
        user = uuid4()
        version = (await _status(env).execute(user)).disclosure.version
        first = await _accept(env).execute(user, version=version)
        env.clock.advance(timedelta(hours=1))

        second = await _accept(env).execute(user, version=version)

        assert second.consent is first.consent

    async def test_une_version_perimee_est_refusee(self, env: IdentityEnv) -> None:
        with pytest.raises(AiConsentVersionMismatchError) as exc:
            await _accept(env).execute(uuid4(), version="2020-01.deadbeef")
        assert exc.value.details["reason"] == "ai_consent_version_mismatch"
        assert exc.value.details["expected_version"].startswith(AI_DISCLOSURE_REVISION)

    async def test_un_nouveau_fournisseur_redemande_l_accord(self, env: IdentityEnv) -> None:
        user = uuid4()
        version = (await _status(env).execute(user)).disclosure.version
        await _accept(env).execute(user, version=version)

        env.ai_catalog.providers = frozenset({"gemini", "anthropic"})

        assert (await _status(env).execute(user)).required
        with pytest.raises(AiConsentRequiredError):
            await _ensure(env).execute(user)

    async def test_retirer_puis_reaccepter(self, env: IdentityEnv) -> None:
        user = uuid4()
        version = (await _status(env).execute(user)).disclosure.version
        await _accept(env).execute(user, version=version)

        revoked = await _revoke(env).execute(user)
        assert revoked.required
        assert revoked.consent is not None
        assert revoked.consent.revoked_at == env.clock.now()

        # Un second retrait ne réécrit rien.
        again = await _revoke(env).execute(user)
        assert again.consent is revoked.consent

        env.clock.advance(timedelta(minutes=5))
        back = await _accept(env).execute(user, version=version)
        assert back.granted
        assert back.consent is not None
        assert back.consent.revoked_at is None
        assert back.consent.accepted_at == env.clock.now()

    async def test_retirer_sans_accord_ne_fait_rien(self, env: IdentityEnv) -> None:
        status = await _revoke(env).execute(uuid4())
        assert status.consent is None
        assert status.required


def _settings(
    tasks: LlmTaskRoutes, fallbacks: LlmTaskRoutes | None = None, **providers: str
) -> Settings:
    return Settings(
        app=AppSettings(environment=Environment.TEST),
        gemini=GeminiSettings(api_key="g" if "gemini" in providers else ""),
        llm=LlmSettings(
            providers=LlmProvidersSettings(
                anthropic=LlmProviderSettings(
                    api_key=providers.get("anthropic", ""),
                    workspace_id=providers.get("anthropic_workspace", ""),
                ),
                openai=LlmProviderSettings(api_key=providers.get("openai", "")),
            ),
            tasks=tasks,
            fallbacks=fallbacks or LlmTaskRoutes(),
        ),
    )


class TestCatalogueDepuisLaConfiguration:
    def test_lit_les_routes_et_les_replis_des_fournisseurs_appelables(self) -> None:
        settings = _settings(
            LlmTaskRoutes(search_dialogue="openai:gpt", transcribe="gemini:g"),
            LlmTaskRoutes(transcribe="anthropic:claude"),
            gemini="g",
            openai="o",
            anthropic="a",
            anthropic_workspace="w",
        )
        catalog = SettingsAiProviderCatalog(settings)

        assert catalog.providers_for(["search_dialogue", "transcribe"]) == {
            "openai",
            "gemini",
            "anthropic",
        }
        assert catalog.providers_for(["transcribe"]) == {"gemini", "anthropic"}
        assert catalog.providers_for(["inconnue"]) == frozenset()

    def test_ignore_un_fournisseur_route_mais_sans_cle(self) -> None:
        settings = _settings(
            LlmTaskRoutes(search_dialogue="anthropic:claude", cv_interpret="gemini:g"),
            gemini="g",
            anthropic="a",  # sans espace de travail : hors routage
        )
        catalog = SettingsAiProviderCatalog(settings)

        assert catalog.providers_for(["search_dialogue", "cv_interpret"]) == {"gemini"}

    def test_sans_aucune_cle_rien_ne_part(self) -> None:
        catalog = SettingsAiProviderCatalog(_settings(LlmTaskRoutes()))
        assert catalog.providers_for(user_data_tasks()) == frozenset()


class TestRoutes:
    async def test_le_cycle_complet_par_http(
        self,
        app: FastAPI,
        client: AsyncClient,
        identity_env: IdentityEnv,
        authenticate: Callable[..., Principal],
    ) -> None:
        user = _user()
        await identity_env.users.add(user)
        authenticate(user.id)

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == user.email.value
        assert me.json()["ai_consent"]["required"] is True
        disclosure = me.json()["ai_consent"]["disclosure"]
        assert disclosure["providers"] == [{"id": "gemini", "name": "Google Gemini"}]
        assert disclosure["purposes"] == [p.value for p in AiPurpose]

        read = await client.get("/api/v1/auth/me/ai-consent")
        assert read.status_code == 200
        assert read.json()["accepted_at"] is None

        stale = await client.put("/api/v1/auth/me/ai-consent", json={"version": "x"})
        assert stale.status_code == 409
        assert stale.json()["error"]["details"]["reason"] == "ai_consent_version_mismatch"
        assert stale.json()["error"]["details"]["expected_version"] == disclosure["version"]

        accepted = await client.put(
            "/api/v1/auth/me/ai-consent", json={"version": disclosure["version"]}
        )
        assert accepted.status_code == 200
        assert accepted.json()["required"] is False
        assert accepted.json()["accepted_version"] == disclosure["version"]
        assert accepted.json()["accepted_at"] is not None
        assert accepted.json()["revoked_at"] is None

        revoked = await client.delete("/api/v1/auth/me/ai-consent")
        assert revoked.status_code == 200
        assert revoked.json()["required"] is True
        assert revoked.json()["revoked_at"] is not None

    async def test_sans_session_rien_ne_se_lit(
        self, client: AsyncClient, identity_env: IdentityEnv
    ) -> None:
        assert (await client.get("/api/v1/auth/me/ai-consent")).status_code == 401
        assert (
            await client.put("/api/v1/auth/me/ai-consent", json={"version": "v"})
        ).status_code == 401
        assert (await client.delete("/api/v1/auth/me/ai-consent")).status_code == 401

    def test_le_catalogue_statique_sert_les_tests(self) -> None:
        catalog = StaticAiProviderCatalog("openai")
        assert catalog.providers_for(["a"]) == {"openai"}
