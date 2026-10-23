"""`GetCriteria`, `ClearCriteria`, `SetAlertsEnabled`, `ForgetUser` : la recherche d'une personne, et son oubli."""

from __future__ import annotations

from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    ClearCriteria,
    ForgetUser,
    GetCriteria,
    SetAlertsEnabled,
)
from karria.modules.offers.domain import KarriaMemory, OfferState
from offers.fakes import (
    NOW,
    FixedClock,
    InMemoryConversationRepository,
    InMemoryCriteriaIndex,
    InMemoryCriteriaRepository,
    InMemoryFeedRepository,
    InMemoryMemoryRepository,
    InMemoryStateRepository,
    InMemoryTargetRepository,
    RecordingUnitOfWork,
    make_criteria,
    make_entry,
    make_stored,
)

pytestmark = pytest.mark.unit

USER = uuid4()


@pytest.fixture
def criteria() -> InMemoryCriteriaRepository:
    return InMemoryCriteriaRepository(make_stored(USER))


@pytest.fixture
def index() -> InMemoryCriteriaIndex:
    index = InMemoryCriteriaIndex()
    index.points[USER] = ((0.0, 1.0), make_criteria(), True)
    return index


class TestLecture:
    async def test_les_criteres_d_une_personne_se_lisent(
        self, criteria: InMemoryCriteriaRepository
    ) -> None:
        get = GetCriteria(criteria=criteria)

        assert await get.execute(USER) == criteria.stored[USER]
        assert await get.execute(uuid4()) is None


class TestOubliDeLaRecherche:
    async def test_criteres_fil_et_index_partent_les_etats_restent(
        self, criteria: InMemoryCriteriaRepository, index: InMemoryCriteriaIndex
    ) -> None:
        feed = InMemoryFeedRepository()
        feed.add(make_entry(USER, uuid4()))
        clear = ClearCriteria(
            criteria=criteria, criteria_index=index, feed=feed, uow=RecordingUnitOfWork()
        )

        await clear.execute(USER)

        assert criteria.stored == {}
        assert feed.entries == {}
        assert USER not in index.points


class TestAlertes:
    async def test_couper_les_alertes_garde_les_criteres(
        self, criteria: InMemoryCriteriaRepository, index: InMemoryCriteriaIndex
    ) -> None:
        toggle = SetAlertsEnabled(criteria=criteria, criteria_index=index, clock=FixedClock())

        updated = await toggle.execute(USER, enabled=False)

        assert updated is not None
        assert updated.alerts_enabled is False
        assert updated.updated_at == NOW
        assert updated.criteria == criteria.stored[USER].criteria
        assert index.points[USER][2] is False

    async def test_sans_criteres_il_n_y_a_rien_a_basculer(
        self, index: InMemoryCriteriaIndex
    ) -> None:
        toggle = SetAlertsEnabled(
            criteria=InMemoryCriteriaRepository(), criteria_index=index, clock=FixedClock()
        )

        assert await toggle.execute(USER, enabled=True) is None


class TestOubliDuCompte:
    async def test_tout_ce_qui_concerne_la_personne_est_efface(
        self, criteria: InMemoryCriteriaRepository, index: InMemoryCriteriaIndex
    ) -> None:
        feed = InMemoryFeedRepository()
        feed.add(make_entry(USER, uuid4()))
        states = InMemoryStateRepository()
        states.add(OfferState(user_id=USER, offer_id=uuid4(), saved=True))
        conversations = InMemoryConversationRepository()
        conversation = await conversations.create(USER, at=NOW)
        await conversations.add_message(conversation, role="user", content="bonjour", at=NOW)
        targets = InMemoryTargetRepository()
        await targets.replace_demands(USER, ["serveur|75|-"], at=NOW)
        memory = InMemoryMemoryRepository()
        memory.memories[USER] = KarriaMemory()
        forget = ForgetUser(
            criteria=criteria,
            criteria_index=index,
            feed=feed,
            states=states,
            conversations=conversations,
            targets=targets,
            memory=memory,
            clock=FixedClock(),
            uow=RecordingUnitOfWork(),
        )

        await forget.execute(USER)
        await forget.execute(USER)

        assert memory.memories == {}

        assert criteria.stored == {}
        assert targets.demands == {}
        assert feed.entries == {}
        assert states.states == {}
        assert conversations.messages == []
        assert USER not in index.points
