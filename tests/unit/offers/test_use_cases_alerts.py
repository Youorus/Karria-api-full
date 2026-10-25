"""`DispatchAlerts` : l'offre fraîche cherche ses lecteurs, et ne les harcèle pas."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    AlertSummary,
    CriteriaMatch,
    DispatchAlerts,
    IndexedOffer,
)
from karria.modules.offers.domain import (
    ALERT_DAILY_MAX,
    ALERT_MAX_OFFER_AGE,
    ALERT_MIN_INTERVAL,
    ALERT_MIN_SCORE,
    ALERT_MIN_SIMILARITY,
    FeedOrigin,
    Location,
    Offer,
    OfferState,
)
from offers.fakes import (
    NOW,
    FixedClock,
    InMemoryCriteriaIndex,
    InMemoryCriteriaRepository,
    InMemoryFeedRepository,
    InMemoryStateRepository,
    RecordingNotifier,
    RecordingUnitOfWork,
    make_criteria,
    make_entry,
    make_offer,
    make_stored,
)

pytestmark = pytest.mark.unit

USER = uuid4()


class Env:
    def __init__(self) -> None:
        self.criteria = InMemoryCriteriaRepository(make_stored(USER))
        self.index = InMemoryCriteriaIndex()
        self.index.matches = [CriteriaMatch(user_id=USER, similarity=ALERT_MIN_SIMILARITY + 0.1)]
        self.feed = InMemoryFeedRepository()
        self.states = InMemoryStateRepository()
        self.notifier = RecordingNotifier()
        self.alerts = DispatchAlerts(
            criteria=self.criteria,
            criteria_index=self.index,
            feed=self.feed,
            states=self.states,
            notifier=self.notifier,
            clock=FixedClock(),
            uow=RecordingUnitOfWork(),
        )

    async def run(self, *offers: Offer) -> AlertSummary:
        return await self.alerts.execute(
            [
                IndexedOffer(offer=o, dense=(1.0, 0.0), title_text=o.title, body_text=o.title)
                for o in offers
            ]
        )


@pytest.fixture
def env() -> Env:
    return Env()


class TestCeQuiEstExamine:
    async def test_une_offre_trop_ancienne_ne_cherche_personne(self, env: Env) -> None:
        old = make_offer(published_at=NOW - ALERT_MAX_OFFER_AGE - timedelta(hours=1))

        summary = await env.run(old)

        assert summary == AlertSummary()
        assert env.index.similar_calls == []

    async def test_une_offre_doublon_ne_cherche_personne(self, env: Env) -> None:
        summary = await env.run(make_offer(duplicate_of=uuid4()))

        assert summary.offers_examined == 0

    async def test_sans_critere_proche_rien_ne_se_passe(self, env: Env) -> None:
        env.index.matches = []

        summary = await env.run(make_offer())

        assert summary == AlertSummary(offers_examined=1)
        assert env.notifier.sent == []


class TestQuiEstConcerne:
    async def test_des_criteres_disparus_ou_sans_alertes_sont_ignores(self, env: Env) -> None:
        silent = uuid4()
        env.criteria.stored[silent] = make_stored(silent, alerts_enabled=False)
        env.index.matches = [
            CriteriaMatch(user_id=uuid4(), similarity=0.9),
            CriteriaMatch(user_id=silent, similarity=0.9),
        ]

        summary = await env.run(make_offer())

        assert summary.people_matched == 0
        assert env.feed.entries == {}

    async def test_une_offre_hors_des_criteres_n_entre_pas(self, env: Env) -> None:
        lyon = Location(label="Lyon", city="Lyon", latitude=45.76, longitude=4.84)

        summary = await env.run(make_offer(location=lyon))

        assert summary.entries_added == 0

    async def test_une_offre_ecartee_ou_deja_au_fil_n_entre_pas(self, env: Env) -> None:
        dismissed, present = make_offer(), make_offer()
        env.states.add(OfferState(user_id=USER, offer_id=dismissed.id, dismissed=True))
        env.feed.add(make_entry(USER, present.id))

        summary = await env.run(dismissed, present)

        assert summary.entries_added == 0

    async def test_une_offre_d_un_autre_metier_ne_passe_pas_la_porte(self, env: Env) -> None:
        summary = await env.run(make_offer(title="Plongeur H/F"))

        assert summary.entries_added == 0


class TestNotification:
    async def test_une_offre_moyenne_entre_au_fil_sans_etre_citee(self, env: Env) -> None:
        first, second, third = make_offer(), make_offer(), make_offer()

        summary = await env.run(first, second, third)

        entry = await env.feed.find(USER, third.id)
        assert entry is not None
        assert entry.origin is FeedOrigin.ALERT
        assert entry.score < ALERT_MIN_SCORE
        assert summary.entries_added == 3
        assert env.feed.notified == [(USER, [first.id, second.id])]

    async def test_sans_offre_tres_alignee_rien_ne_part(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Un synonyme en tête vaut 0,88 : au-dessus du seuil réel ; on le
        # relève pour exercer le cas « des entrées, aucune très alignée ».
        monkeypatch.setattr("karria.modules.offers.application.use_cases.ALERT_MIN_SCORE", 0.95)
        env.criteria.stored[USER] = make_stored(
            USER, make_criteria(job_titles=["chef de rang", "maître d'hôtel", "serveur"])
        )

        summary = await env.run(make_offer(), make_offer())

        assert summary.entries_added == 2
        assert summary.notifications_sent == 0

    async def test_une_offre_tres_alignee_previent_et_le_note(self, env: Env) -> None:
        offer = make_offer()

        summary = await env.run(offer)

        assert summary == AlertSummary(
            offers_examined=1, people_matched=1, entries_added=1, notifications_sent=1
        )
        (sent,) = env.notifier.sent
        assert sent["user_id"] == USER
        assert sent["data"]["type"] == "offer"
        assert sent["data"]["id"] == str(offer.id)
        assert env.criteria.alerts == [(USER, NOW)]
        assert env.feed.notified == [(USER, [offer.id])]

    async def test_une_alerte_trop_rapprochee_ne_part_pas(self, env: Env) -> None:
        env.criteria.stored[USER] = make_stored(USER, last_alert_at=NOW - ALERT_MIN_INTERVAL / 2)

        summary = await env.run(make_offer())

        assert summary.entries_added == 1
        assert env.notifier.sent == []

    async def test_le_plafond_du_jour_bloque_les_envois(self, env: Env) -> None:
        env.criteria.stored[USER] = make_stored(
            USER, alerts_sent_on=NOW, alerts_sent_today=ALERT_DAILY_MAX
        )

        await env.run(make_offer())

        assert env.notifier.sent == []

    async def test_le_compteur_d_hier_ne_compte_plus(self, env: Env) -> None:
        env.criteria.stored[USER] = make_stored(
            USER, alerts_sent_on=NOW - timedelta(days=1), alerts_sent_today=ALERT_DAILY_MAX
        )

        await env.run(make_offer())

        assert len(env.notifier.sent) == 1

    async def test_aucun_appareil_joignable_ne_note_rien(self, env: Env) -> None:
        env.notifier.accepted = 0

        summary = await env.run(make_offer())

        assert summary.notifications_sent == 0
        assert env.criteria.alerts == []
        assert env.feed.notified == []

    async def test_plusieurs_offres_partent_en_une_seule_notification(self, env: Env) -> None:
        offers = [make_offer(), dataclasses.replace(make_offer(), title="Serveuse"), make_offer()]

        await env.run(*offers)

        (sent,) = env.notifier.sent
        assert sent["data"]["type"] == "offers_feed"
        assert sent["data"]["count"] == "2"
