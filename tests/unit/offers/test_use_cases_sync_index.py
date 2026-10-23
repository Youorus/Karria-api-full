"""`SyncIndex`, `ExpireOffers`, `ReconcileIndex` : l'index rejoint la base, et seulement elle."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import timedelta
from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    AlertSummary,
    DispatchAlerts,
    ExpireOffers,
    IndexedOffer,
    ReconcileIndex,
    SyncIndex,
    SyncReport,
)
from karria.modules.offers.domain import OFFER_ACTIVE_TTL, index_version
from offers.fakes import (
    NOW,
    FakeEmbedder,
    FixedClock,
    InMemoryOfferIndex,
    InMemoryOfferRepository,
    InMemoryOutbox,
    RecordingUnitOfWork,
    make_offer,
)

pytestmark = pytest.mark.unit

VERSION = index_version("fake-model")


class StubAlerts(DispatchAlerts):
    """Retient les offres reçues ; lève si on le lui demande."""

    def __init__(self, *, fail: bool = False) -> None:
        self.received: list[IndexedOffer] = []
        self.fail = fail

    async def execute(self, indexed: Sequence[IndexedOffer]) -> AlertSummary:
        self.received.extend(indexed)
        if self.fail:
            raise RuntimeError("Qdrant en panne")
        return AlertSummary(offers_examined=len(indexed))


class Env:
    def __init__(self, alerts: DispatchAlerts | None = None) -> None:
        self.offers = InMemoryOfferRepository()
        self.outbox = InMemoryOutbox()
        self.embedder = FakeEmbedder()
        self.index = InMemoryOfferIndex()
        self.uow = RecordingUnitOfWork()
        self.sync = SyncIndex(
            offers=self.offers,
            outbox=self.outbox,
            embedder=self.embedder,
            index=self.index,
            alerts=alerts,
            clock=FixedClock(),
            uow=self.uow,
        )


class TestSynchronisation:
    async def test_une_boite_vide_ne_fait_rien(self) -> None:
        env = Env()

        assert await env.sync.execute() == SyncReport()
        assert env.embedder.documents == []

    async def test_une_offre_nouvelle_est_plongee_ecrite_et_notee(self) -> None:
        env = Env()
        offer = make_offer()
        env.offers.add(offer)
        await env.outbox.enqueue([offer.id, offer.id], at=NOW)

        report = await env.sync.execute()

        assert report.claimed == 2
        assert report.embedded == 1
        assert len(env.embedder.documents) == 1
        assert env.index.points[offer.id].title_text == offer.title
        stored = env.offers.offers[offer.id]
        assert stored.indexed_version == VERSION == env.sync.version
        assert stored.indexed_hash == offer.content_hash
        assert env.outbox.items == []

    async def test_une_offre_deja_plongee_sur_ce_contenu_ne_reecrit_que_ses_attributs(
        self,
    ) -> None:
        env = Env()
        offer = make_offer()
        env.offers.add(
            dataclasses.replace(offer, indexed_version=VERSION, indexed_hash=offer.content_hash)
        )
        await env.outbox.enqueue([offer.id], at=NOW)

        report = await env.sync.execute()

        assert (report.embedded, report.payloads) == (0, 1)
        assert env.embedder.documents == []
        assert env.index.payloads == [offer.id]

    async def test_un_contenu_change_ou_un_autre_modele_se_replonge(self) -> None:
        env = Env()
        changed = make_offer()
        other_model = make_offer()
        env.offers.add(
            dataclasses.replace(changed, indexed_version=VERSION, indexed_hash="ancien"),
            dataclasses.replace(
                other_model, indexed_version="autre@t1", indexed_hash=other_model.content_hash
            ),
        )
        await env.outbox.enqueue([changed.id, other_model.id], at=NOW)

        report = await env.sync.execute()

        assert report.embedded == 2

    async def test_une_offre_expiree_doublon_ou_disparue_est_retiree(self) -> None:
        env = Env()
        expired = dataclasses.replace(make_offer(), is_active=False, indexed_version=VERSION)
        duplicate = dataclasses.replace(make_offer(), duplicate_of=uuid4())
        vanished = uuid4()
        env.offers.add(expired, duplicate)
        await env.outbox.enqueue([expired.id, duplicate.id, vanished], at=NOW)

        report = await env.sync.execute()

        assert report.removed == 3
        assert set(env.index.deleted) == {expired.id, duplicate.id, vanished}
        assert env.offers.offers[expired.id].indexed_version is None
        assert env.outbox.items == []

    async def test_les_offres_nouvellement_plongees_partent_aux_alertes(self) -> None:
        alerts = StubAlerts()
        env = Env(alerts)
        fresh, refreshed = make_offer(), make_offer()
        env.offers.add(
            fresh,
            dataclasses.replace(
                refreshed, indexed_version=VERSION, indexed_hash=refreshed.content_hash
            ),
        )
        await env.outbox.enqueue([fresh.id, refreshed.id], at=NOW)

        report = await env.sync.execute()

        assert [item.offer.id for item in alerts.received] == [fresh.id]
        assert report.alerts == AlertSummary(offers_examined=1)

    async def test_une_panne_des_alertes_ne_bloque_pas_l_index(self) -> None:
        env = Env(StubAlerts(fail=True))
        offer = make_offer()
        env.offers.add(offer)
        await env.outbox.enqueue([offer.id], at=NOW)

        report = await env.sync.execute()

        assert report.embedded == 1
        assert report.alerts is None
        assert env.outbox.items == []

    async def test_sans_rien_de_neuf_les_alertes_ne_sont_pas_appelees(self) -> None:
        alerts = StubAlerts()
        env = Env(alerts)
        vanished = uuid4()
        await env.outbox.enqueue([vanished], at=NOW)

        report = await env.sync.execute()

        assert alerts.received == []
        assert report.alerts is None


class TestExpiration:
    async def test_les_offres_trop_anciennes_sont_desactivees_et_inscrites(self) -> None:
        offers = InMemoryOfferRepository()
        old = make_offer(published_at=NOW - OFFER_ACTIVE_TTL - timedelta(days=1))
        recent = make_offer()
        offers.add(old, recent)
        outbox = InMemoryOutbox()
        expire = ExpireOffers(
            offers=offers, outbox=outbox, clock=FixedClock(), uow=RecordingUnitOfWork()
        )

        assert await expire.execute() == 1
        assert offers.offers[old.id].is_active is False
        assert outbox.offer_ids == [old.id]

    async def test_rien_a_expirer_n_inscrit_rien(self) -> None:
        offers = InMemoryOfferRepository([make_offer()])
        outbox = InMemoryOutbox()
        expire = ExpireOffers(
            offers=offers, outbox=outbox, clock=FixedClock(), uow=RecordingUnitOfWork()
        )

        assert await expire.execute() == 0
        assert outbox.items == []


class TestReconciliation:
    def _reconcile(
        self, offers: InMemoryOfferRepository, outbox: InMemoryOutbox, index: InMemoryOfferIndex
    ) -> ReconcileIndex:
        return ReconcileIndex(
            offers=offers, outbox=outbox, index=index, embedder=FakeEmbedder(), clock=FixedClock()
        )

    async def test_l_ecart_est_reinscrit_et_mesure(self) -> None:
        unindexed = make_offer()
        stale = dataclasses.replace(make_offer(), is_active=False, indexed_version=VERSION)
        current = dataclasses.replace(make_offer(), indexed_version=VERSION)
        offers = InMemoryOfferRepository([unindexed, stale, current])
        outbox = InMemoryOutbox()

        report = await self._reconcile(offers, outbox, InMemoryOfferIndex()).execute()

        assert report.requeued == 2
        assert report.expected == 2
        assert report.indexed == 0
        assert set(outbox.offer_ids) == {unindexed.id, stale.id}

    async def test_un_index_a_jour_ne_reinscrit_rien(self) -> None:
        offers = InMemoryOfferRepository(
            [dataclasses.replace(make_offer(), indexed_version=VERSION)]
        )
        outbox = InMemoryOutbox()

        report = await self._reconcile(offers, outbox, InMemoryOfferIndex()).execute()

        assert report.requeued == 0
        assert outbox.items == []
