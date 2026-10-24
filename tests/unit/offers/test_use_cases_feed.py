"""Le fil : `GetFeed`, `GetOfferForUser`, `UpdateOfferState`, `MarkFeedSeen`, `GetFeedFilters`."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    FeedRequest,
    GetFeed,
    GetFeedFilters,
    GetOfferForUser,
    Impression,
    MarkFeedSeen,
    StateChanges,
    UpdateOfferState,
)
from karria.modules.offers.domain import (
    ContractType,
    FeedMode,
    InteractionKind,
    OfferNotFoundError,
    OfferState,
)
from offers.fakes import (
    NOW,
    FixedClock,
    InMemoryCriteriaRepository,
    InMemoryFeedRepository,
    InMemoryInteractionLog,
    InMemoryOfferRepository,
    InMemoryStateRepository,
    make_entry,
    make_offer,
    make_stored,
)

pytestmark = pytest.mark.unit

USER = uuid4()


class Env:
    def __init__(self) -> None:
        self.criteria = InMemoryCriteriaRepository()
        self.offers = InMemoryOfferRepository()
        self.feed = InMemoryFeedRepository()
        self.states = InMemoryStateRepository()
        self.clock = FixedClock()
        self.get_feed = GetFeed(
            criteria=self.criteria, feed=self.feed, offers=self.offers, states=self.states
        )
        self.get_offer = GetOfferForUser(offers=self.offers, feed=self.feed, states=self.states)
        self.log = InMemoryInteractionLog()
        self.update = UpdateOfferState(
            offers=self.offers,
            feed=self.feed,
            states=self.states,
            clock=self.clock,
            criteria=self.criteria,
            log=self.log,
        )
        self.seen = MarkFeedSeen(
            states=self.states,
            clock=self.clock,
            offers=self.offers,
            feed=self.feed,
            criteria=self.criteria,
            log=self.log,
        )
        self.filters = GetFeedFilters(criteria=self.criteria, feed=self.feed, offers=self.offers)

    def request(self, **changes: object) -> FeedRequest:
        return dataclasses.replace(FeedRequest(user_id=USER, limit=20, offset=0), **changes)


@pytest.fixture
def env() -> Env:
    return Env()


class TestFilSansRecherche:
    async def test_toutes_les_offres_actives_les_plus_recentes_d_abord(self, env: Env) -> None:
        old = make_offer(published_at=NOW - timedelta(days=5))
        new = make_offer()
        env.offers.add(old, new, make_offer(is_active=False))

        page = await env.get_feed.execute(env.request())

        assert page.mode is FeedMode.ALL_OFFERS
        assert page.criteria_summary is None
        assert [item.offer.id for item in page.items] == [new.id, old.id]
        assert page.total == 2
        assert all(item.entry is None for item in page.items)
        assert page.items[0].state == OfferState(user_id=USER, offer_id=new.id)

    async def test_les_ecartees_vues_et_candidatees_se_filtrent(self, env: Env) -> None:
        dismissed, seen, applied, kept = (make_offer() for _ in range(4))
        env.offers.add(dismissed, seen, applied, kept)
        env.states.add(
            OfferState(user_id=USER, offer_id=dismissed.id, dismissed=True),
            OfferState(user_id=USER, offer_id=seen.id, seen=True),
            OfferState(user_id=USER, offer_id=applied.id, applied=True),
        )

        page = await env.get_feed.execute(env.request(unseen_only=True, unapplied_only=True))

        assert [item.offer.id for item in page.items] == [kept.id]

    async def test_un_contrat_inconnu_du_filtre_est_ignore(self, env: Env) -> None:
        cdi = make_offer()
        cdd = make_offer(contract_type=ContractType.CDD)
        env.offers.add(cdi, cdd)

        page = await env.get_feed.execute(env.request(contract_types=("cdd", "inconnu")))

        assert [item.offer.id for item in page.items] == [cdd.id]


class TestFilDeRecherche:
    async def test_les_entrees_du_fil_par_pertinence_avec_leur_etat(self, env: Env) -> None:
        env.criteria.stored[USER] = make_stored(USER)
        best, other = make_offer(), make_offer()
        env.offers.add(best, other)
        env.feed.add(
            make_entry(USER, other.id, score=0.4),
            make_entry(USER, best.id, score=0.9),
            make_entry(USER, uuid4(), score=0.5),  # offre disparue
        )
        env.states.add(OfferState(user_id=USER, offer_id=best.id, saved=True))

        page = await env.get_feed.execute(env.request(sort="recent"))

        assert page.mode is FeedMode.SEARCH
        assert page.criteria_summary == make_stored(USER).criteria.summary()
        assert [item.offer.id for item in page.items] == [best.id, other.id]
        assert page.items[0].entry is not None
        assert page.items[0].state.saved is True
        assert env.feed.queries[0].sort == "recent"
        assert page.total == 3

    async def test_le_mode_toutes_les_offres_montre_le_corpus_sans_toucher_aux_criteres(
        self, env: Env
    ) -> None:
        stored = make_stored(USER)
        env.criteria.stored[USER] = stored
        in_feed, elsewhere = make_offer(), make_offer(published_at=NOW - timedelta(days=1))
        env.offers.add(in_feed, elsewhere)
        env.feed.add(make_entry(USER, in_feed.id))

        page = await env.get_feed.execute(env.request(mode=FeedMode.ALL_OFFERS))

        assert page.mode is FeedMode.ALL_OFFERS
        assert {item.offer.id for item in page.items} == {in_feed.id, elsewhere.id}
        assert page.criteria_summary == stored.criteria.summary()
        assert env.criteria.stored[USER] is stored
        assert env.feed.queries == []

    async def test_le_mode_recherche_sans_criteres_retombe_sur_toutes_les_offres(
        self, env: Env
    ) -> None:
        env.offers.add(make_offer())

        page = await env.get_feed.execute(env.request(mode=FeedMode.SEARCH))

        assert page.mode is FeedMode.ALL_OFFERS
        assert page.total == 1

    async def test_le_mode_recherche_explicite_sert_le_fil(self, env: Env) -> None:
        env.criteria.stored[USER] = make_stored(USER)
        offer = make_offer()
        env.offers.add(offer, make_offer())
        env.feed.add(make_entry(USER, offer.id))

        page = await env.get_feed.execute(env.request(mode=FeedMode.SEARCH))

        assert page.mode is FeedMode.SEARCH
        assert [item.offer.id for item in page.items] == [offer.id]

    async def test_les_sauvegardees_quelle_que_soit_la_recherche_dernieres_d_abord(
        self, env: Env
    ) -> None:
        env.criteria.stored[USER] = make_stored(USER)
        first, second, gone = make_offer(), make_offer(), uuid4()
        env.offers.add(first, second)
        env.feed.add(make_entry(USER, first.id))
        env.states.add(
            OfferState(
                user_id=USER, offer_id=first.id, saved=True, saved_at=NOW - timedelta(days=2)
            ),
            OfferState(user_id=USER, offer_id=second.id, saved=True, updated_at=NOW),
            OfferState(user_id=USER, offer_id=gone, saved=True, saved_at=NOW),
        )

        page = await env.get_feed.execute(env.request(saved_only=True))

        assert [item.offer.id for item in page.items] == [second.id, first.id]
        assert page.total == 2
        assert page.items[1].entry is not None
        assert page.items[0].entry is None

    async def test_une_sauvegarde_sans_date_se_range_a_sa_parution(self, env: Env) -> None:
        offer = make_offer()
        env.offers.add(offer)
        env.states.add(OfferState(user_id=USER, offer_id=offer.id, saved=True))

        page = await env.get_feed.execute(env.request(saved_only=True, limit=1))

        assert [item.offer.id for item in page.items] == [offer.id]


class TestUneOffre:
    async def test_une_offre_inconnue_est_introuvable(self, env: Env) -> None:
        with pytest.raises(OfferNotFoundError):
            await env.get_offer.execute(USER, uuid4())

    async def test_une_offre_expiree_reste_lisible_avec_sa_place_et_son_etat(
        self, env: Env
    ) -> None:
        offer = make_offer(is_active=False)
        env.offers.add(offer)
        env.feed.add(make_entry(USER, offer.id))
        env.states.add(OfferState(user_id=USER, offer_id=offer.id, saved=True))

        item = await env.get_offer.execute(USER, offer.id)

        assert item.offer == offer
        assert item.entry is not None
        assert item.state.saved is True

    async def test_sans_etat_l_offre_rend_un_etat_vierge(self, env: Env) -> None:
        offer = make_offer()
        env.offers.add(offer)

        item = await env.get_offer.execute(USER, offer.id)

        assert item.state.is_blank
        assert item.entry is None


class TestInteractions:
    async def test_une_interaction_sur_une_offre_inconnue_est_refusee(self, env: Env) -> None:
        with pytest.raises(OfferNotFoundError):
            await env.update.execute(USER, uuid4(), StateChanges(saved=True))

    async def test_sauvegarder_puis_ouvrir_cumule_les_etats(self, env: Env) -> None:
        offer = make_offer()
        env.offers.add(offer)

        await env.update.execute(USER, offer.id, StateChanges(saved=True))
        item = await env.update.execute(
            USER, offer.id, StateChanges(opened=True, link_clicked=True)
        )

        assert item.state.saved
        assert item.state.opened
        assert item.state.link_clicked
        assert item.state.saved_at == NOW
        assert env.states.states[(USER, offer.id)] == item.state
        assert [e.kind for e in env.log.events] == [
            InteractionKind.SAVE,
            InteractionKind.OPEN,
            InteractionKind.LINK_CLICK,
        ]

    async def test_le_journal_porte_la_place_le_temps_passe_et_le_contexte(self, env: Env) -> None:
        env.criteria.stored[USER] = make_stored(USER)
        offer = make_offer()
        env.offers.add(offer)
        env.feed.add(make_entry(USER, offer.id, score=0.7))

        await env.update.execute(USER, offer.id, StateChanges(opened=True, position=4))
        await env.update.execute(USER, offer.id, StateChanges(dwell_ms=12_000))
        await env.update.execute(USER, offer.id, StateChanges(opened=True))  # rien ne change

        kinds = [(e.kind, e.position, e.dwell_ms) for e in env.log.events]
        assert kinds == [(InteractionKind.OPEN, 4, None), (InteractionKind.DWELL, None, 12_000)]
        shot = env.log.events[0].snapshot
        assert shot.score == 0.7
        assert shot.feed_mode is FeedMode.SEARCH
        assert shot.title_match == 0

    async def test_sans_journal_l_interaction_reste_muette(self, env: Env) -> None:
        plain = UpdateOfferState(
            offers=env.offers, feed=env.feed, states=env.states, clock=env.clock
        )
        offer = make_offer()
        env.offers.add(offer)

        item = await plain.execute(USER, offer.id, StateChanges(saved=True))

        assert item.state.saved
        assert env.log.events == []


class TestVu:
    async def test_seules_les_offres_jamais_vues_comptent(self, env: Env) -> None:
        seen, fresh = uuid4(), uuid4()
        env.states.add(OfferState(user_id=USER, offer_id=seen, seen=True))

        updated = await env.seen.execute(
            USER, [Impression(seen, 0), Impression(fresh, 1), Impression(fresh, 1)]
        )

        assert updated == 1
        assert env.states.states[(USER, fresh)].seen is True
        assert env.log.events == []  # aucune de ces offres n'existe : rien à expliquer

    async def test_chaque_affichage_est_consigne_avec_sa_place(self, env: Env) -> None:
        first, second = make_offer(), make_offer()
        env.offers.add(first, second)
        env.states.add(OfferState(user_id=USER, offer_id=first.id, seen=True))

        updated = await env.seen.execute(USER, [Impression(first.id, 0), Impression(second.id, 1)])

        assert updated == 1
        assert [(e.kind, e.offer_id, e.position) for e in env.log.events] == [
            (InteractionKind.IMPRESSION, first.id, 0),
            (InteractionKind.IMPRESSION, second.id, 1),
        ]
        assert env.log.events[0].snapshot.feed_mode is FeedMode.ALL_OFFERS

    async def test_sans_journal_le_vu_reste_un_simple_marquage(self, env: Env) -> None:
        plain = MarkFeedSeen(states=env.states, clock=env.clock)
        offer = make_offer()
        env.offers.add(offer)

        assert await plain.execute(USER, [Impression(offer.id)]) == 1
        assert env.log.events == []


class TestOptionsDeFiltre:
    async def test_sans_recherche_les_options_couvrent_tout_le_corpus(self, env: Env) -> None:
        env.offers.add(make_offer(), make_offer(contract_type=ContractType.CDD))

        options = await env.filters.execute(USER)

        assert options.contract_types == {"cdi": 1, "cdd": 1}
        assert options.cities == {"Paris": 2}

    async def test_avec_une_recherche_les_options_couvrent_le_fil(self, env: Env) -> None:
        env.criteria.stored[USER] = make_stored(USER)
        in_feed = make_offer(contract_type=ContractType.STAGE)
        env.offers.add(in_feed, make_offer())
        env.feed.add(make_entry(USER, in_feed.id))

        options = await env.filters.execute(USER)

        assert options.contract_types == {"stage": 1}

    async def test_en_mode_toutes_les_offres_les_options_couvrent_le_corpus_malgre_la_recherche(
        self, env: Env
    ) -> None:
        env.criteria.stored[USER] = make_stored(USER)
        in_feed = make_offer(contract_type=ContractType.STAGE)
        env.offers.add(in_feed, make_offer())
        env.feed.add(make_entry(USER, in_feed.id))

        options = await env.filters.execute(USER, FeedMode.ALL_OFFERS)

        assert options.contract_types == {"stage": 1, "cdi": 1}
