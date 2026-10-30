"""Parcours HTTP du module `offers` : le contrat `karria_app/docs/contrat-api-recherche.md`.

La pile HTTP est traversée — authentification, validation, sérialisation,
format d'erreur. Les cas d'usage sont remplacés par des doublures qui
rendent des résultats préparés : leur logique est couverte par
`tests/unit/offers/test_use_cases_*`, et ce fichier vérifie le contrat.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from karria.modules.offers.api import dependencies as deps
from karria.modules.offers.application import (
    AppliedCriteria,
    ConversationTurn,
    FeedFilterOptions,
    FeedItem,
    FeedPage,
    LeasedTarget,
    Message,
    StoredCriteria,
)
from karria.modules.offers.domain import (
    AssistantRateLimitedError,
    BatchRejectedError,
    Company,
    ContractType,
    CriteriaIncompleteError,
    DialogueStatus,
    FeedEntry,
    FeedMode,
    FeedOrigin,
    Location,
    LocationCriteria,
    MatchQuality,
    Offer,
    OfferNotFoundError,
    OfferState,
    Salary,
    SearchCriteria,
    Source,
)
from karria.shared.auth import Principal
from karria.shared.config import CollectorAccessSettings, Settings, get_settings

pytestmark = pytest.mark.e2e

API = "/api/v1"
NOW = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def make_offer(**changes: Any) -> Offer:
    values: dict[str, Any] = {
        "id": uuid4(),
        "source": Source.HELLOWORK,
        "source_id": "hw-1",
        "title": "Serveur H/F",
        "company": Company(name="Le Bistrot", logo_url="https://logo"),
        "description": "Service en salle " * 40,
        "location": Location(
            label="Paris (75)",
            city="Paris",
            postal_code="75011",
            department_code="75",
            latitude=48.85,
            longitude=2.35,
        ),
        "contract_type": ContractType.ALTERNANCE,
        "remote": None,
        "schedule": None,
        "experience": None,
        "salary": Salary(min_monthly=1_200, max_monthly=None, raw="1 200 €"),
        "published_at": NOW - timedelta(hours=2),
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "apply_url": "https://apply",
        "origin_url": "https://origin",
    }
    values.update(changes)
    return Offer(**values)


def stored(user: UUID) -> StoredCriteria:
    return StoredCriteria(
        user_id=user,
        criteria=SearchCriteria(
            query="Serveur à Paris",
            job_titles=["serveur"],
            location=LocationCriteria(label="Paris", latitude=48.85, longitude=2.35),
        ),
        fingerprint="f",
        alerts_enabled=True,
        updated_at=NOW,
    )


def item(user: UUID, offer: Offer, *, entry: bool = True, **state: Any) -> FeedItem:
    return FeedItem(
        offer=offer,
        entry=FeedEntry(
            user_id=user,
            offer_id=offer.id,
            score=0.8,
            quality=MatchQuality.HIGH,
            reasons=("Paris · 2 km",),
            origin=FeedOrigin.SEARCH,
            created_at=NOW,
        )
        if entry
        else None,
        state=OfferState(user_id=user, offer_id=offer.id, **state),
    )


@dataclass
class Stub:
    """Un cas d'usage préparé : rend `result`, ou lève `error`, et garde ses appels."""

    result: Any = None
    error: Exception | None = None
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((*args, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def user(authenticate: Callable[..., Principal]) -> UUID:
    return authenticate().subject_id


@pytest.fixture
def use(app: FastAPI, ai_consented: None) -> Iterator[Callable[[Callable[..., Any], Stub], Stub]]:
    providers: list[Callable[..., Any]] = []

    def _use(provider: Callable[..., Any], stub: Stub) -> Stub:
        app.dependency_overrides[provider] = lambda: stub
        providers.append(provider)
        return stub

    app.dependency_overrides[deps.get_clock] = Clock
    yield _use
    for provider in [*providers, deps.get_clock]:
        app.dependency_overrides.pop(provider, None)


class TestFil:
    async def test_une_page_porte_le_contrat_v1_et_ses_ajouts(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        offer = make_offer()
        feed = use(
            deps.get_get_feed,
            Stub(FeedPage(FeedMode.SEARCH, [item(user, offer)], 3, "Serveur · Paris")),
        )

        response = await client.get(
            f"{API}/job-matches/me",
            params={
                "limit": 1,
                "sort": "fresh",
                "unapplied_only": "true",
                "contract_types": "alternance,cdi",
                "cities": "Paris",
                "published_after": "2026-10-20T00:00:00Z",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert (body["count"], body["has_next"], body["mode"]) == (3, True, "search")
        match = body["items"][0]
        assert match["id"] == match["job_offer_id"] == str(offer.id)
        assert (match["match_quality"], match["is_new"], match["saved"]) == ("high", True, False)
        job = match["job_offer"]
        assert job["summary"] is None
        assert job["description"] is None
        assert job["excerpt"].endswith("…")
        # L'adresse du logo chez la source n'est jamais servie : sans logo en stock, `null`.
        assert job["company"] == {
            "name": "Le Bistrot",
            "logo": None,
            "logo_large": None,
            "website": None,
        }
        assert job["city"] == {"name": "Paris", "postal_code": "75011"}
        assert job["contract_type"] == {"code": "alternance", "label": "Alternance"}
        assert job["is_alternance"] is True
        request = feed.calls[0][0]
        assert request.sort == "recent"
        assert request.unapplied_only is True
        assert request.contract_types == ("alternance", "cdi")

    async def test_hors_recherche_une_offre_n_a_ni_score_ni_qualite(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        offer = make_offer(
            company=None,
            location=None,
            contract_type=None,
            description="Court.",
            published_at=NOW - timedelta(days=5),
        )
        use(
            deps.get_get_feed,
            Stub(
                FeedPage(FeedMode.ALL_OFFERS, [item(user, offer, entry=False, seen=True)], 1, None)
            ),
        )

        body = (await client.get(f"{API}/job-matches/me")).json()

        match = body["items"][0]
        assert (match["score_normalized"], match["match_quality"], match["origin"]) == (
            None,
            None,
            None,
        )
        assert match["reasons"] == []
        assert match["is_new"] is False
        assert match["job_offer"]["excerpt"] == "Court."
        assert match["job_offer"]["city"] is None
        assert body["has_next"] is False

    async def test_les_options_de_filtre_portent_leurs_libelles(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(
            deps.get_get_feed_filters,
            Stub(FeedFilterOptions(contract_types={"cdd": 1, "cdi": 4}, cities={"Paris": 5})),
        )

        body = (await client.get(f"{API}/job-matches/me/filter-options")).json()

        assert body == {
            "contract_types": [
                {"value": "cdi", "label": "CDI", "count": 4},
                {"value": "cdd", "label": "CDD", "count": 1},
            ],
            "cities": [{"value": "Paris", "label": "Paris", "count": 5}],
        }

    async def test_le_mode_demande_passe_au_cas_d_usage_et_un_mode_inconnu_est_refuse(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        feed = use(deps.get_get_feed, Stub(FeedPage(FeedMode.ALL_OFFERS, [], 0, "Serveur")))
        filters = use(
            deps.get_get_feed_filters, Stub(FeedFilterOptions(contract_types={}, cities={}))
        )

        body = (await client.get(f"{API}/job-matches/me", params={"mode": "all_offers"})).json()
        await client.get(f"{API}/job-matches/me/filter-options", params={"mode": "all_offers"})
        await client.get(f"{API}/job-matches/me")
        refused = await client.get(f"{API}/job-matches/me", params={"mode": "everything"})

        assert (body["mode"], body["criteria_summary"]) == ("all_offers", "Serveur")
        assert feed.calls[0][0].mode is FeedMode.ALL_OFFERS
        assert feed.calls[1][0].mode is None
        assert filters.calls[0][1] is FeedMode.ALL_OFFERS
        assert refused.status_code == 422

    async def test_le_vu_par_lots(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_mark_seen, Stub(2))

        response = await client.post(
            f"{API}/job-matches/me/seen", json={"offer_ids": [str(uuid4()), str(uuid4())]}
        )

        assert response.json() == {"updated": 2}

    async def test_le_detail_porte_la_description(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        offer = make_offer()
        use(deps.get_get_offer, Stub(item(user, offer)))

        body = (await client.get(f"{API}/job-matches/{offer.id}")).json()

        assert body["job_offer"]["description"] == offer.description

    async def test_une_offre_inconnue_rend_404(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        missing = uuid4()
        use(deps.get_get_offer, Stub(error=OfferNotFoundError(missing)))

        response = await client.get(f"{API}/job-matches/{missing}")

        assert response.status_code == 404
        assert response.json()["error"]["details"]["reason"] == "offer_not_found"

    async def test_une_interaction_traduit_les_noms_du_contrat(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        offer = make_offer()
        update = use(deps.get_update_state, Stub(item(user, offer, saved=True, opened=True)))

        response = await client.patch(
            f"{API}/job-matches/{offer.id}/interaction",
            json={"saved": True, "detail_opened": True, "dismissed": False},
        )

        assert response.status_code == 200
        assert (response.json()["saved"], response.json()["detail_opened"]) == (True, True)
        changes = update.calls[0][2]
        assert (changes.saved, changes.opened, changes.dismissed, changes.link_clicked) == (
            True,
            True,
            False,
            None,
        )

    async def test_une_interaction_vide_est_refusee(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_update_state, Stub())

        response = await client.patch(f"{API}/job-matches/{uuid4()}/interaction", json={})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "interaction_empty"

    async def test_sans_session_le_fil_se_lit_en_lecture_seule(
        self, client: AsyncClient, use: Callable[..., Stub]
    ) -> None:
        # ADR-0025 : les offres ne sont pas un contenu de compte. Le fil se
        # lit sans session, mais rien ne s'y écrit.
        use(deps.get_get_feed, Stub(FeedPage(FeedMode.ALL_OFFERS, [], 0, None)))

        assert (await client.get(f"{API}/job-matches/me")).status_code == 200
        assert (await client.get(f"{API}/offers/feed")).status_code == 200
        assert (
            await client.post(f"{API}/job-matches/me/seen", json={"offer_ids": []})
        ).status_code == 401


def turn(user: UUID, *, status: DialogueStatus, with_criteria: bool) -> ConversationTurn:
    return ConversationTurn(
        conversation_id=uuid4(),
        message=Message(
            id=uuid4(),
            conversation_id=uuid4(),
            role="assistant",
            content="Voici 12 offres.",
            created_at=NOW,
        ),
        status=status,
        criteria=stored(user) if with_criteria else None,
        results_count=12 if with_criteria else None,
        suggestions=("En CDI seulement",),
    )


class TestAssistant:
    async def test_un_tour_rend_la_reponse_et_ce_qu_elle_a_declenche(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        converse = use(
            deps.get_converse_search,
            Stub(turn(user, status=DialogueStatus.SEARCH, with_criteria=True)),
        )

        response = await client.post(
            f"{API}/assistant/messages", json={"conversation_id": None, "content": "  serveur  "}
        )

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "search"
        assert body["results_count"] == 12
        assert body["message"]["role"] == "assistant"
        assert body["criteria"]["summary"].startswith("serveur")
        assert body["criteria"]["location"]["label"] == "Paris"
        assert body["suggestions"] == ["En CDI seulement"]
        assert converse.calls[0][1]["content"] == "serveur"

    async def test_un_bavardage_ne_rend_pas_de_criteres(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(
            deps.get_converse_search,
            Stub(turn(user, status=DialogueStatus.CHAT, with_criteria=False)),
        )

        body = (await client.post(f"{API}/assistant/messages", json={"content": "salut"})).json()

        assert (body["status"], body["criteria"]) == ("chat", None)

    async def test_un_message_blanc_est_refuse(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_converse_search, Stub())

        response = await client.post(f"{API}/assistant/messages", json={"content": "   "})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "assistant_message_empty"

    async def test_le_quota_journalier_rend_429(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_converse_search, Stub(error=AssistantRateLimitedError(retry_after=3600)))

        response = await client.post(f"{API}/assistant/messages", json={"content": "serveur"})

        assert response.status_code == 429


class TestCriteres:
    async def test_sans_recherche_les_criteres_sont_nuls(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_get_criteria, Stub(None))

        assert (await client.get(f"{API}/search/criteria")).json() == {"criteria": None}

    async def test_les_criteres_courants(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_get_criteria, Stub(stored(user)))

        body = (await client.get(f"{API}/search/criteria")).json()

        assert body["criteria"]["job_titles"] == ["serveur"]
        assert body["criteria"]["alerts_enabled"] is True

    async def test_poser_des_criteres_relance_la_recherche(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        apply = use(
            deps.get_apply_criteria,
            Stub(AppliedCriteria(stored(user), results=30)),
        )

        response = await client.put(
            f"{API}/search/criteria",
            json={"job_titles": ["serveur", "chef de rang", "barman"], "whole_france": True},
        )

        assert response.status_code == 200
        assert response.json()["results_count"] == 30
        criteria = apply.calls[0][1]
        assert criteria.query == "serveur ou chef de rang"
        assert criteria.whole_france is True

    async def test_des_criteres_incomplets_sont_refuses(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_apply_criteria, Stub(error=CriteriaIncompleteError(["location"])))

        response = await client.put(f"{API}/search/criteria", json={"job_titles": ["serveur"]})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "criteria_incomplete"

    async def test_oublier_la_recherche(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        clear = use(deps.get_clear_criteria, Stub())

        assert (await client.delete(f"{API}/search/criteria")).status_code == 204
        assert clear.calls[0][0] == user

    async def test_basculer_les_alertes(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_set_alerts_enabled, Stub(stored(user)))

        response = await client.put(f"{API}/search/criteria/alerts", json={"enabled": True})

        assert response.json() == {"enabled": True}

    async def test_sans_recherche_les_alertes_rendent_404(
        self, client: AsyncClient, user: UUID, use: Callable[..., Stub]
    ) -> None:
        use(deps.get_set_alerts_enabled, Stub(None))

        response = await client.put(f"{API}/search/criteria/alerts", json={"enabled": False})

        assert response.status_code == 404
        assert response.json()["error"]["details"]["reason"] == "criteria_not_found"


TOKEN = "jeton-du-collecteur"  # noqa: S105 - jeton factice de test


@pytest.fixture
def collector(app: FastAPI, settings: Settings) -> Iterator[dict[str, str]]:
    configured = settings.model_copy(update={"collector": CollectorAccessSettings(token=TOKEN)})
    app.dependency_overrides[get_settings] = lambda: configured
    yield {"Authorization": f"Bearer {TOKEN}"}
    app.dependency_overrides.pop(get_settings, None)


class TestCollecteur:
    async def test_sans_jeton_les_routes_internes_sont_fermees(
        self, client: AsyncClient, collector: dict[str, str]
    ) -> None:
        response = await client.get(f"{API}/internal/offers/targets")

        assert response.status_code == 401
        assert response.json()["error"]["details"]["reason"] == "collector_token_invalid"

    async def test_un_jeton_faux_est_refuse(
        self, client: AsyncClient, collector: dict[str, str]
    ) -> None:
        response = await client.get(
            f"{API}/internal/offers/targets", headers={"Authorization": "Bearer faux"}
        )

        assert response.status_code == 401

    async def test_les_cibles_louees_portent_la_version_du_contrat(
        self, client: AsyncClient, collector: dict[str, str], use: Callable[..., Stub]
    ) -> None:
        lease = use(
            deps.get_lease_targets,
            Stub(
                [
                    LeasedTarget(
                        key="serveur|paris", search_term="serveur", location="Paris", distance_km=20
                    )
                ]
            ),
        )

        response = await client.get(
            f"{API}/internal/offers/targets", params={"limit": 5}, headers=collector
        )

        assert response.status_code == 200
        assert response.json() == {
            "handoff_version": 1,
            "targets": [
                {
                    "key": "serveur|paris",
                    "search_term": "serveur",
                    "location": "Paris",
                    "distance_km": 20,
                    "last_found": 0,
                }
            ],
        }
        assert lease.calls == [({"limit": 5},)]

    async def test_un_lot_est_accepte_et_differe(
        self, client: AsyncClient, collector: dict[str, str], use: Callable[..., Stub]
    ) -> None:
        receive = use(deps.get_receive_batch, Stub("collector/a.gz"))

        response = await client.post(
            f"{API}/internal/offers/batches",
            content=b"\x1f\x8bgzip",
            headers={
                **collector,
                "X-Handoff-Version": "1",
                "X-Target-Key": "serveur|paris",
                "Content-Type": "application/gzip",
            },
        )

        assert response.status_code == 202
        assert response.json() == {"key": "collector/a.gz"}
        assert receive.calls == [(b"\x1f\x8bgzip", {"target_key": "serveur|paris"})]

    async def test_une_version_inconnue_du_contrat_est_refusee(
        self, client: AsyncClient, collector: dict[str, str], use: Callable[..., Stub]
    ) -> None:
        receive = use(deps.get_receive_batch, Stub("k"))

        response = await client.post(
            f"{API}/internal/offers/batches",
            content=b"x",
            headers={**collector, "X-Handoff-Version": "2"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "handoff_version_unsupported"
        assert receive.calls == []

    async def test_un_lot_annonce_trop_gros_est_refuse_sans_etre_lu(
        self,
        client: AsyncClient,
        collector: dict[str, str],
        use: Callable[..., Stub],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from karria.modules.offers.api import routers  # noqa: PLC0415

        monkeypatch.setattr(routers, "BATCH_MAX_BYTES", 3)
        use(deps.get_receive_batch, Stub("k"))

        response = await client.post(
            f"{API}/internal/offers/batches",
            content=b"trop long",
            headers={**collector, "X-Handoff-Version": "1"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "batch_too_large"

    async def test_une_cible_inconnue_est_refusee(
        self, client: AsyncClient, collector: dict[str, str], use: Callable[..., Stub]
    ) -> None:
        use(deps.get_receive_batch, Stub(error=BatchRejectedError("target_unknown")))

        response = await client.post(
            f"{API}/internal/offers/batches",
            content=b"x",
            headers={**collector, "X-Handoff-Version": "1", "X-Target-Key": "?"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["reason"] == "target_unknown"
