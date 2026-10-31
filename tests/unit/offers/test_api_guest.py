"""Le fil sans compte (ADR-0025) : lecture seule, tout le corpus, compté par origine."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from doubles import FakeClock
from karria.modules.offers.api import dependencies as deps
from karria.modules.offers.api.routers import ACCOUNT_REQUIRED
from karria.modules.offers.application import FeedRequest, GetFeed, GetFeedFilters, GetOfferForUser
from karria.modules.offers.domain import GUEST_RATE_LIMIT, FeedMode, OfferState
from karria.shared.auth import ANONYMOUS_RATE_LIMITED, Principal
from offers.fakes import (
    InMemoryCriteriaRepository,
    InMemoryFeedRepository,
    InMemoryOfferRepository,
    InMemoryStateRepository,
    make_entry,
    make_offer,
    make_stored,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)


class TestCasDUsageAnonymes:
    async def test_le_fil_anonyme_est_le_corpus_sans_etat(self) -> None:
        offers = InMemoryOfferRepository([make_offer(title="A"), make_offer(title="B")])
        criteria = InMemoryCriteriaRepository(make_stored(uuid4()))
        feed = GetFeed(
            criteria=criteria,
            feed=InMemoryFeedRepository(offers),
            offers=offers,
            states=InMemoryStateRepository(),
        )

        page = await feed.execute(
            FeedRequest(
                user_id=None,
                limit=10,
                offset=0,
                mode=FeedMode.SEARCH,
                contract_types=("cdi", "zzz"),
            )
        )

        assert page.mode is FeedMode.ALL_OFFERS
        assert page.criteria_summary is None
        assert page.total == 2
        assert all(item.state is None and item.entry is None for item in page.items)

    async def test_l_offre_anonyme_n_a_ni_place_ni_etat(self) -> None:
        offer = make_offer()
        offers = InMemoryOfferRepository([offer])
        feed = InMemoryFeedRepository(offers)
        user = uuid4()
        feed.entries[(user, offer.id)] = make_entry(user, offer.id)
        states = InMemoryStateRepository()
        states.add(OfferState(user_id=user, offer_id=offer.id, saved=True))

        anonymous = await GetOfferForUser(offers=offers, feed=feed, states=states).execute(
            None, offer.id
        )
        known = await GetOfferForUser(offers=offers, feed=feed, states=states).execute(
            user, offer.id
        )

        assert anonymous.entry is None
        assert anonymous.state is None
        assert known.entry is not None
        assert known.state is not None
        assert known.state.saved

    async def test_les_options_anonymes_couvrent_le_corpus(self) -> None:
        offers = InMemoryOfferRepository([make_offer(), make_offer()])
        options = await GetFeedFilters(
            criteria=InMemoryCriteriaRepository(make_stored(uuid4())),
            feed=InMemoryFeedRepository(offers),
            offers=offers,
        ).execute(None)

        assert options.contract_types == {"cdi": 2}
        assert options.cities == {"Paris": 2}


@pytest.fixture
def guest_app(app: FastAPI) -> Iterator[InMemoryOfferRepository]:
    """Les dépôts d'`offers` en mémoire, avec deux offres."""
    offers = InMemoryOfferRepository([make_offer(title="Serveur"), make_offer(title="Cuisinier")])
    overrides = {
        deps.get_offer_repository: lambda: offers,
        # Des lambdas sans paramètre : passé tel quel, un constructeur à
        # arguments ferait lire ces arguments dans le corps de la requête.
        deps.get_criteria_repository: lambda: InMemoryCriteriaRepository(),  # noqa: PLW0108
        deps.get_feed_repository: lambda: InMemoryFeedRepository(offers),
        deps.get_state_repository: lambda: InMemoryStateRepository(),  # noqa: PLW0108
        deps.get_clock: lambda: FakeClock(NOW),
    }
    app.dependency_overrides.update(overrides)
    yield offers
    for key in overrides:
        app.dependency_overrides.pop(key, None)


class TestRoutesSansSession:
    @pytest.mark.parametrize("path", ["/api/v1/job-matches/me", "/api/v1/offers/feed"])
    async def test_le_fil_se_lit_sans_compte(
        self, client: AsyncClient, guest_app: InMemoryOfferRepository, path: str
    ) -> None:
        response = await client.get(path, params={"mode": "search", "limit": 1})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "all_offers"
        assert body["criteria_summary"] is None
        assert body["count"] == 2
        assert body["has_next"] is True
        item = body["items"][0]
        assert item["saved"] is False
        assert item["feed_seen"] is False
        assert item["score_normalized"] is None

    @pytest.mark.parametrize("flag", ["saved_only", "unseen_only", "unapplied_only"])
    async def test_un_filtre_personnel_exige_un_compte(
        self, client: AsyncClient, guest_app: InMemoryOfferRepository, flag: str
    ) -> None:
        response = await client.get("/api/v1/offers/feed", params={flag: "true"})

        assert response.status_code == 401
        details = response.json()["error"]["details"]
        assert details == {"reason": ACCOUNT_REQUIRED, "field": flag}

    async def test_le_detail_et_les_options_se_lisent_sans_compte(
        self, client: AsyncClient, guest_app: InMemoryOfferRepository
    ) -> None:
        offer_id = next(iter(guest_app.offers))

        detail = await client.get(f"/api/v1/offers/{offer_id}")
        legacy = await client.get(f"/api/v1/job-matches/{offer_id}")
        options = await client.get("/api/v1/offers/filter-options")
        missing = await client.get(f"/api/v1/offers/{uuid4()}")

        assert detail.status_code == 200
        assert detail.json()["job_offer"]["description"]
        assert detail.json() == legacy.json()
        assert options.status_code == 200
        assert options.json()["contract_types"][0]["value"] == "cdi"
        assert missing.status_code == 404

    async def test_une_personne_connectee_garde_son_fil(
        self,
        client: AsyncClient,
        guest_app: InMemoryOfferRepository,
        authenticate: Callable[..., Principal],
    ) -> None:
        authenticate(uuid4())
        response = await client.get("/api/v1/job-matches/me", params={"saved_only": "true"})
        assert response.status_code == 200
        assert response.json()["count"] == 0

    async def test_plus_proches_sans_compte_est_refuse_proprement(
        self, client: AsyncClient, guest_app: InMemoryOfferRepository
    ) -> None:
        response = await client.get("/api/v1/offers/feed", params={"sort": "nearest"})
        fresh = await client.get("/api/v1/offers/feed", params={"sort": "fresh"})

        assert response.status_code == 422
        assert response.json()["error"]["details"] == {
            "reason": "nearest_unavailable",
            "field": "sort",
        }
        assert fresh.json()["sort"] == "fresh"
        assert fresh.json()["nearest_available"] is False

    @pytest.mark.usefixtures("identity_env")
    async def test_une_session_expiree_repond_401_et_non_le_fil_anonyme(
        self, client: AsyncClient, guest_app: InMemoryOfferRepository
    ) -> None:
        # La vraie couture `resolve_principal` (doublures d'`identity`) : un
        # jeton inconnu ne résout personne.
        response = await client.get(
            "/api/v1/job-matches/me", headers={"Authorization": "Bearer jeton-perime"}
        )
        options = await client.get(
            "/api/v1/job-matches/me/filter-options",
            headers={"Authorization": "Bearer jeton-perime"},
        )

        assert response.status_code == 401
        assert response.json()["error"]["details"]["reason"] == "session_expired"
        assert options.status_code == 401

    async def test_au_dela_du_quota_l_origine_attend(
        self, client: AsyncClient, guest_app: InMemoryOfferRepository
    ) -> None:
        ip = "198.51.100.7"
        for _ in range(GUEST_RATE_LIMIT):
            assert deps._guest_limiter.hit(f"offers|{ip}") is None

        throttled = await client.get("/api/v1/offers/feed", headers={"X-Forwarded-For": ip})
        other = await client.get("/api/v1/offers/feed", headers={"X-Forwarded-For": "198.51.100.8"})

        assert throttled.status_code == 429
        assert throttled.json()["error"]["details"]["reason"] == ANONYMOUS_RATE_LIMITED
        assert other.status_code == 200
