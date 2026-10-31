"""Les dépôts du module `offers` sur une vraie base, sous le schéma des migrations."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    FeedQuery,
    IngestionReport,
    OfferFilters,
    StoredCriteria,
)
from karria.modules.offers.domain import (
    Company,
    ContractType,
    FeedEntry,
    FeedOrigin,
    FeedSort,
    GeocodedPlace,
    Location,
    MatchQuality,
    Offer,
    OfferState,
    Salary,
    ScrapeTarget,
    SearchCriteria,
    Source,
    TargetOrigin,
    TargetState,
    TargetStats,
    Widening,
    WideningKind,
    bands,
)
from karria.modules.offers.infrastructure import (
    TortoiseConversationRepository,
    TortoiseCriteriaRepository,
    TortoiseFeedRepository,
    TortoiseGeocodeCache,
    TortoiseIndexOutbox,
    TortoiseIngestionRunRepository,
    TortoiseOfferRepository,
    TortoiseOfferStateRepository,
    TortoiseScrapeTargetRepository,
)
from karria.modules.offers.infrastructure.models import IngestionRunModel
from karria.modules.offers.infrastructure.repositories import to_signed, to_unsigned

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]

NOW = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)
#: Une empreinte dont le bit de poids fort est posé : elle passe par le
#: stockage signé de `BIGINT`.
HIGH_SIMHASH = (1 << 63) | 0x1234_5678_9ABC


def make_offer(**changes: Any) -> Offer:
    values: dict[str, Any] = {
        "id": uuid4(),
        "source": Source.INDEED,
        "source_id": f"in-{uuid4().hex[:8]}",
        "title": "Serveur H/F",
        "company": Company(name="Le Bistrot", logo_url="https://logo", website="https://site"),
        "description": "Service en salle.",
        "location": Location(
            label="Paris (75)",
            city="Paris",
            postal_code="75011",
            department_code="75",
            latitude=48.85,
            longitude=2.35,
        ),
        "contract_type": ContractType.CDI,
        "remote": None,
        "schedule": None,
        "experience": None,
        "salary": Salary(min_monthly=1_900, max_monthly=None, raw="1 900 €"),
        "published_at": NOW,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "apply_url": "https://apply",
        "origin_url": "https://origin",
        "content_hash": uuid4().hex,
        "simhash": HIGH_SIMHASH,
    }
    values.update(changes)
    return Offer(**values)


class TestSignes:
    def test_l_empreinte_fait_l_aller_retour_par_bigint(self) -> None:
        assert to_signed(HIGH_SIMHASH) < 0
        assert to_unsigned(to_signed(HIGH_SIMHASH)) == HIGH_SIMHASH
        assert to_signed(5) == 5


class TestOffres:
    async def test_une_offre_fait_l_aller_retour_a_l_identique(self) -> None:
        repository = TortoiseOfferRepository()
        offer = make_offer()

        await repository.insert_many([offer])
        loaded = await repository.find(offer.id)

        assert loaded is not None
        assert dataclasses.replace(loaded, published_at=offer.published_at) == offer
        assert await repository.find(uuid4()) is None

    async def test_une_offre_sans_employeur_ni_lieu_se_relit(self) -> None:
        repository = TortoiseOfferRepository()
        offer = make_offer(company=None, location=None, simhash=7)

        await repository.insert_many([offer])
        loaded = await repository.find(offer.id)

        assert loaded is not None
        assert (loaded.company, loaded.location, loaded.simhash) == (None, None, 7)

    async def test_rien_a_inserer_ni_a_chercher(self) -> None:
        repository = TortoiseOfferRepository()

        await repository.insert_many([])

        assert await repository.find_many([]) == {}
        assert await repository.find_by_sources([]) == {}
        assert await repository.find_canonical_by_hashes([]) == {}

    async def test_la_recherche_par_source_verifie_le_site(self) -> None:
        repository = TortoiseOfferRepository()
        indeed = make_offer(source_id="same")
        hellowork = make_offer(source=Source.HELLOWORK, source_id="same")
        await repository.insert_many([indeed, hellowork])

        found = await repository.find_by_sources([("indeed", "same")])

        assert list(found) == [("indeed", "same")]
        assert set(await repository.find_many([indeed.id, hellowork.id])) == {
            indeed.id,
            hellowork.id,
        }

    async def test_le_condensat_rend_la_plus_ancienne_canonique_active(self) -> None:
        repository = TortoiseOfferRepository()
        first = make_offer(content_hash="h")
        second = make_offer(content_hash="h")
        duplicate = make_offer(content_hash="h", duplicate_of=first.id)
        inactive = make_offer(content_hash="g", is_active=False)
        await repository.insert_many([first])
        await repository.insert_many([second, duplicate, inactive])

        found = await repository.find_canonical_by_hashes(["h", "g"])

        assert list(found) == ["h"]
        assert found["h"].id == first.id

    async def test_les_quasi_doublons_partagent_employeur_et_bande(self) -> None:
        repository = TortoiseOfferRepository()
        near = make_offer()
        other_company = make_offer(company=Company(name="Ailleurs"))
        no_company = make_offer(company=None)
        old = make_offer(published_at=NOW - timedelta(days=100))
        await repository.insert_many([near, other_company, no_company, old])
        since = NOW - timedelta(days=45)

        with_company = await repository.near_duplicate_candidates(
            bands=bands(HIGH_SIMHASH), company_key="le bistrot", since=since
        )
        without = await repository.near_duplicate_candidates(
            bands=bands(HIGH_SIMHASH), company_key=None, since=since
        )

        assert [o.id for o in with_company] == [near.id]
        assert [o.id for o in without] == [no_company.id]

    async def test_revoir_sauver_desactiver(self) -> None:
        repository = TortoiseOfferRepository()
        offer = make_offer(published_at=NOW - timedelta(days=40))
        await repository.insert_many([offer])
        later = NOW + timedelta(hours=1)

        await repository.mark_seen([], at=later)
        await repository.mark_seen([offer.id], at=later)
        await repository.save(dataclasses.replace(offer, title="Serveuse H/F", last_seen_at=later))
        expired = await repository.deactivate_published_before(NOW - timedelta(days=30), at=later)
        again = await repository.deactivate_published_before(NOW - timedelta(days=30), at=later)

        loaded = await repository.find(offer.id)
        assert loaded is not None
        assert loaded.title == "Serveuse H/F"
        assert loaded.last_seen_at == later
        assert loaded.is_active is False
        assert (expired, again) == ([offer.id], [])

    async def test_la_page_active_filtre_et_trie(self) -> None:
        repository = TortoiseOfferRepository()
        recent = make_offer(published_at=NOW)
        older = make_offer(published_at=NOW - timedelta(days=2), contract_type=ContractType.CDD)
        excluded = make_offer(published_at=NOW - timedelta(days=1))
        lyon = make_offer(location=Location(label="Lyon", city="Lyon"))
        await repository.insert_many([recent, older, excluded, lyon])

        page, total = await repository.page_active(
            filters=OfferFilters(
                contract_types=(ContractType.CDI, ContractType.CDD),
                cities=("Paris",),
                published_after=NOW - timedelta(days=3),
                exclude_offer_ids=(excluded.id,),
            ),
            limit=10,
            offset=0,
        )

        assert [o.id for o in page] == [recent.id, older.id]
        assert total == 2
        _, everything = await repository.page_active(filters=OfferFilters(), limit=1, offset=0)
        assert everything == 4

    async def test_la_page_active_par_distance_en_sql(self) -> None:
        repository = TortoiseOfferRepository()
        paris = make_offer(published_at=NOW - timedelta(days=2))  # géocodée à Paris
        lyon = make_offer(
            location=Location(label="Lyon", city="Lyon", latitude=45.76, longitude=4.83),
            published_at=NOW,
        )
        nowhere = make_offer(location=Location(label="?", city="Paris"), published_at=NOW)
        await repository.insert_many([lyon, nowhere, paris])

        page, total = await repository.page_active(
            filters=OfferFilters(), limit=10, offset=0, near=(48.80, 2.13)
        )

        assert [o.id for o in page] == [paris.id, lyon.id, nowhere.id]
        assert total == 3

    async def test_l_etat_d_indexation_et_la_reconciliation(self) -> None:
        repository = TortoiseOfferRepository()
        indexed = make_offer()
        pending = make_offer()
        stale = make_offer(is_active=False, indexed_version="v1", indexed_hash="x")
        await repository.insert_many([indexed, pending, stale])

        await repository.mark_indexed([(indexed.id, indexed.content_hash)], version="v1", at=NOW)
        await repository.mark_unindexed([])

        loaded = await repository.find(indexed.id)
        assert loaded is not None
        assert loaded.embedding_current("v1")
        assert set(await repository.stale_index_ids(version="v1", limit=10)) == {
            pending.id,
            stale.id,
        }
        assert await repository.count_indexable() == 2

        await repository.mark_unindexed([stale.id])
        assert await repository.stale_index_ids(version="v1", limit=10) == [pending.id]

    async def test_les_options_de_filtre(self) -> None:
        repository = TortoiseOfferRepository()
        a = make_offer()
        b = make_offer(contract_type=None, location=Location(label="Lyon", city="Lyon"))
        await repository.insert_many([a, b])

        contracts, cities = await repository.filter_options(None)
        scoped = await repository.filter_options([a.id])

        assert contracts == {ContractType.CDI: 1}
        assert cities == {"Paris": 1, "Lyon": 1}
        assert scoped == ({ContractType.CDI: 1}, {"Paris": 1})
        assert await repository.filter_options([]) == ({}, {})


class TestBoiteDEnvoi:
    async def test_l_ordre_d_arrivee_et_l_acquittement(self) -> None:
        outbox = TortoiseIndexOutbox()
        first, second = uuid4(), uuid4()

        await outbox.enqueue([], at=NOW)
        await outbox.enqueue([first, second], at=NOW)
        items = await outbox.claim(limit=1)
        await outbox.ack([])
        await outbox.ack([item.id for item in items])

        assert [item.offer_id for item in items] == [first]
        assert await outbox.pending() == 1


class TestCibles:
    async def test_creer_repeser_et_rendre_dues(self) -> None:
        repository = TortoiseScrapeTargetRepository()
        seed = ScrapeTarget(search_term="serveur", location="France", origin=TargetOrigin.SEED)
        demand = ScrapeTarget(
            search_term="Serveur",
            location="france",
            origin=TargetOrigin.DEMAND,
            weight=3,
            distance_km=20,
        )

        await repository.upsert_many([seed], at=NOW)
        await repository.record_scrape(
            seed.key,
            TargetStats(state=TargetState.ACTIVE, scrapes=1, yield_ema=5, last_found=5),
            at=NOW,
            next_at=NOW + timedelta(days=1),
        )
        assert await repository.due(now=NOW, limit=5) == []

        await repository.upsert_many([demand], at=NOW, due_now=True)
        due = await repository.due(now=NOW, limit=5)

        assert len(due) == 1
        assert due[0].target.origin is TargetOrigin.DEMAND
        assert (due[0].target.weight, due[0].target.distance_km) == (1, 20)
        assert due[0].thin_since == NOW
        assert due[0].stats.state is TargetState.ACTIVE
        assert due[0].stats.yield_ema == 5
        assert await repository.count_active() == 1
        assert await repository.count_active(TargetOrigin.SEED) == 0

    async def test_une_cible_du_socle_revue_garde_sa_date(self) -> None:
        repository = TortoiseScrapeTargetRepository()
        seed = ScrapeTarget(search_term="cariste", location="France", origin=TargetOrigin.SEED)
        await repository.upsert_many([seed], at=NOW)
        await repository.record_scrape(
            seed.key, TargetStats(state=TargetState.ACTIVE), at=NOW, next_at=NOW + timedelta(days=1)
        )

        await repository.upsert_many([seed], at=NOW)

        assert await repository.due(now=NOW, limit=5) == []
        assert await repository.count_active(TargetOrigin.SEED) == 1

    async def test_la_demande_se_lie_se_remplace_et_se_pese(self) -> None:
        repository = TortoiseScrapeTargetRepository()
        alice, bob = uuid4(), uuid4()
        serveur = ScrapeTarget(
            search_term="serveur", location="Paris", origin=TargetOrigin.DEMAND, location_key="75"
        )
        barman = ScrapeTarget(
            search_term="barman", location="Paris", origin=TargetOrigin.DEMAND, location_key="75"
        )
        await repository.upsert_many([serveur, barman], at=NOW)
        await repository.replace_demands(alice, [serveur.key, barman.key], at=NOW)
        await repository.replace_demands(bob, [serveur.key], at=NOW - timedelta(days=1))
        await repository.replace_demands(alice, [serveur.key], at=NOW + timedelta(hours=1))

        demands = await repository.all_demands()
        await repository.set_weights({serveur.key: 2})
        catalogue = await repository.catalogue(limit=10)

        assert sorted((str(d.user_id), d.target_key) for d in demands) == sorted(
            [(str(alice), serveur.key), (str(bob), serveur.key)]
        )
        assert max(d.since for d in demands) == NOW + timedelta(hours=1)
        assert [(t.key, t.target.weight) for t in catalogue] == [(serveur.key, 2), (barman.key, 0)]
        await repository.replace_demands(alice, [], at=NOW)
        assert len(await repository.all_demands()) == 1

    async def test_louer_repousse_l_echeance_et_une_cible_se_retrouve_par_sa_cle(
        self,
    ) -> None:
        repository = TortoiseScrapeTargetRepository()
        target = ScrapeTarget(
            search_term="barman", location="Toulouse", origin=TargetOrigin.DEMAND, distance_km=15
        )
        await repository.upsert_many([target], at=NOW)

        await repository.lease([target.key], until=NOW + timedelta(minutes=30))
        await repository.lease([], until=NOW)

        assert await repository.due(now=NOW, limit=5) == []
        found = await repository.find(target.key)
        assert found is not None
        assert found.next_scrape_at == NOW + timedelta(minutes=30)
        assert found.target.distance_km == 15
        assert await repository.find("inconnue|nulle part") is None

    async def test_une_demande_oubliee_s_eteint(self) -> None:
        repository = TortoiseScrapeTargetRepository()
        demand = ScrapeTarget(search_term="plombier", location="Rennes", origin=TargetOrigin.DEMAND)
        await repository.upsert_many([demand], at=NOW - timedelta(days=20))

        assert await repository.deactivate_demand_before(NOW - timedelta(days=15)) == 1
        assert await repository.count_active() == 0


async def test_un_passage_de_collecte_est_trace() -> None:
    run = uuid4()

    await TortoiseIngestionRunRepository().record(
        IngestionReport(
            run_id=run, started_at=NOW, finished_at=NOW, fetched=10, inserted=4, landing_keys=("k",)
        )
    )

    row = await IngestionRunModel.get(run_id=run)
    assert (row.inserted, row.landing_keys) == (4, ["k"])


class TestCacheGeocodage:
    async def test_un_lieu_trouve_et_un_echec_se_retiennent(self) -> None:
        cache = TortoiseGeocodeCache()
        place = GeocodedPlace(
            city="Lyon", postal_code="69003", department_code="69", latitude=45.7, longitude=4.8
        )

        assert await cache.lookup("lyon") == (False, None)
        await cache.remember("lyon", place)
        await cache.remember("nulle part", None)

        assert await cache.lookup("lyon") == (True, place)
        assert await cache.lookup("nulle part") == (True, None)


def stored(user: Any, **changes: Any) -> StoredCriteria:
    values: dict[str, Any] = {
        "user_id": user,
        "criteria": SearchCriteria(query="serveur", job_titles=["serveur"], whole_france=True),
        "fingerprint": "f",
        "alerts_enabled": True,
        "updated_at": NOW,
    }
    values.update(changes)
    return StoredCriteria(**values)


class TestCriteres:
    async def test_sauver_relire_remplacer_oublier(self) -> None:
        repository = TortoiseCriteriaRepository()
        user, other = uuid4(), uuid4()

        assert await repository.find(user) is None
        await repository.save(stored(user))
        await repository.save(stored(user, fingerprint="g"))
        await repository.save(stored(other, alerts_enabled=False))

        loaded = await repository.find(user)
        assert loaded is not None
        assert loaded.fingerprint == "g"
        assert loaded.criteria.job_titles == ["serveur"]
        assert [s.user_id for s in await repository.all_with_alerts()] == [user]
        assert set(await repository.find_many([user, other])) == {user, other}
        assert await repository.find_many([]) == {}
        assert await repository.delete(user) is True
        assert await repository.delete(user) is False

    async def test_le_compteur_d_alertes_repart_chaque_jour(self) -> None:
        repository = TortoiseCriteriaRepository()
        user = uuid4()
        await repository.save(stored(user))

        await repository.record_alert(user, at=NOW)
        await repository.record_alert(user, at=NOW + timedelta(hours=3))
        same_day = await repository.find(user)
        await repository.record_alert(user, at=NOW + timedelta(days=1))
        next_day = await repository.find(user)
        await repository.record_alert(uuid4(), at=NOW)

        assert same_day is not None
        assert next_day is not None
        assert same_day.alerts_sent_today == 2
        assert next_day.alerts_sent_today == 1
        assert next_day.last_alert_at == NOW + timedelta(days=1)


class TestConversations:
    async def test_une_conversation_garde_ses_messages_dans_l_ordre(self) -> None:
        repository = TortoiseConversationRepository()
        user = uuid4()
        conversation = await repository.create(user, at=NOW)

        await repository.add_message(conversation, role="user", content="un", at=NOW)
        await repository.add_message(conversation, role="assistant", content="deux", at=NOW)
        await repository.add_message(conversation, role="user", content="trois", at=NOW)

        recent = await repository.recent_messages(conversation, limit=2)
        assert [m.content for m in recent] == ["deux", "trois"]
        assert await repository.belongs_to(conversation, user)
        assert not await repository.belongs_to(conversation, uuid4())
        assert await repository.user_turns_since(user, NOW - timedelta(days=1)) == 2
        assert await repository.delete_all_of_user(user) == 1
        assert await repository.recent_messages(conversation, limit=5) == []


def entry(user: Any, offer: Offer, score: float, **changes: Any) -> FeedEntry:
    values: dict[str, Any] = {
        "user_id": user,
        "offer_id": offer.id,
        "score": score,
        "quality": MatchQuality.HIGH,
        "reasons": ("CDI",),
        "origin": FeedOrigin.SEARCH,
        "created_at": NOW,
    }
    values.update(changes)
    return FeedEntry(**values)


class TestFil:
    async def test_remplacer_ajouter_paginer_et_oublier(self) -> None:
        offers = TortoiseOfferRepository()
        feed = TortoiseFeedRepository()
        user = uuid4()
        best = make_offer(published_at=NOW - timedelta(days=1))
        newest = make_offer(published_at=NOW, contract_type=ContractType.CDD)
        gone = make_offer()
        lyon = make_offer(location=Location(label="Lyon", city="Lyon"))
        await offers.insert_many([best, newest, gone, lyon])

        await feed.replace(user, [entry(user, gone, 0.1)], remove=[])
        await feed.replace(
            user, [entry(user, best, 0.9), entry(user, newest, 0.5)], remove=[gone.id]
        )
        await feed.add_many([])
        await feed.add_many(
            [entry(user, best, 0.1, origin=FeedOrigin.ALERT), entry(user, lyon, 0.3)]
        )

        by_score, total = await feed.page(FeedQuery(user_id=user, limit=10, offset=0))
        by_date, _ = await feed.page(
            FeedQuery(user_id=user, limit=10, offset=0, sort=FeedSort.RECENT)
        )
        filtered, filtered_total = await feed.page(
            FeedQuery(
                user_id=user,
                limit=10,
                offset=0,
                filters=OfferFilters(
                    contract_types=(ContractType.CDI,),
                    cities=("Paris",),
                    published_after=NOW - timedelta(days=2),
                    exclude_offer_ids=(newest.id,),
                ),
            )
        )

        assert [e.offer_id for e in by_score] == [best.id, newest.id, lyon.id]
        assert by_score[0].score == 0.9  # l'alerte n'a pas écrasé la recherche
        assert total == 3
        assert by_date[0].offer_id == newest.id
        assert ([e.offer_id for e in filtered], filtered_total) == ([best.id], 1)
        # Depuis Lyon : les deux offres de Paris à égalité (la date départage),
        # celle de Lyon sans coordonnées en dernier.
        by_distance, _ = await feed.page(
            FeedQuery(user_id=user, limit=10, offset=0, sort=FeedSort.NEAREST, near=(45.75, 4.85))
        )
        assert [e.offer_id for e in by_distance] == [newest.id, best.id, lyon.id]
        second, _ = await feed.page(
            FeedQuery(user_id=user, limit=1, offset=1, sort=FeedSort.NEAREST, near=(45.75, 4.85))
        )
        assert [e.offer_id for e in second] == [best.id]

        await feed.mark_notified(user, [], at=NOW)
        await feed.mark_notified(user, [best.id], at=NOW)
        notified = await feed.find(user, best.id)
        assert notified is not None
        assert notified.notified_at == NOW
        assert await feed.find(user, gone.id) is None
        assert set(await feed.offer_ids_of(user)) == {best.id, newest.id, lyon.id}
        assert len(await feed.entries_of(user)) == 3
        assert await feed.delete_all_of_user(user) == 3


class TestEtats:
    async def test_sauver_relire_et_filtrer(self) -> None:
        repository = TortoiseOfferStateRepository()
        user = uuid4()
        saved, dismissed, seen = uuid4(), uuid4(), uuid4()
        blank = OfferState(user_id=user, offer_id=saved)

        await repository.save(blank.with_changes(at=NOW, saved=True))
        await repository.save(OfferState(user_id=user, offer_id=dismissed, dismissed=True))
        await repository.save(
            OfferState(user_id=user, offer_id=seen).with_changes(at=NOW, seen=True)
        )

        loaded = await repository.find(user, saved)
        assert loaded is not None
        assert loaded.saved_at == NOW
        assert await repository.find(user, uuid4()) is None
        assert set(await repository.find_many(user, [saved, seen])) == {saved, seen}
        assert await repository.find_many(user, []) == {}
        assert len(await repository.all_of_user(user)) == 3
        assert set(await repository.offer_ids_where(user, saved=True, dismissed=True)) == {
            saved,
            dismissed,
        }
        assert await repository.offer_ids_where(user, applied=True) == []
        assert await repository.offer_ids_where(user, seen=True) == [seen]
        assert await repository.offer_ids_where(user) == []
        assert await repository.delete_all_of_user(user) == 3


class TestElargissementsEnAttente:
    async def test_les_elargissements_se_gardent_et_se_relisent(self) -> None:
        repository = TortoiseConversationRepository()
        conversation = await repository.create(uuid4(), at=NOW)
        widening = Widening(
            id="radius-50",
            kind=WideningKind.RADIUS,
            label="élargir à 50 km autour de Paris",
            gain=42,
            message="Élargir à 50 km : +42",
            criteria=SearchCriteria(query="serveur", job_titles=["serveur"], whole_france=True),
        )

        assert await repository.pending_widenings(conversation) == []
        await repository.set_pending_widenings(conversation, [widening])
        assert await repository.pending_widenings(conversation) == [widening]
        await repository.set_pending_widenings(conversation, [])
        assert await repository.pending_widenings(conversation) == []
        assert await repository.pending_widenings(uuid4()) == []
