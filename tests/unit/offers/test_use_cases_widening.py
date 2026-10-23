"""`SuggestWidenings` et son usage dans `ConverseSearch` : chiffres comptés, acceptation sans modèle."""

from __future__ import annotations

from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    ApplyCriteria,
    ConverseSearch,
    SearchOffers,
    SuggestWidenings,
)
from karria.modules.offers.domain import (
    DialogueProposal,
    DialogueStatus,
    Intent,
    Location,
    WideningKind,
)
from offers.fakes import (
    PARIS,
    FakeDialogue,
    FakeEmbedder,
    FakeGeocoder,
    FixedClock,
    InMemoryConversationRepository,
    InMemoryCriteriaIndex,
    InMemoryCriteriaRepository,
    InMemoryFeedRepository,
    InMemoryMemoryRepository,
    InMemoryOfferIndex,
    InMemoryOfferRepository,
    InMemoryStateRepository,
    InMemoryTargetRepository,
    RecordingQueue,
    RecordingUnitOfWork,
    make_criteria,
    make_offer,
)

pytestmark = pytest.mark.unit

USER = uuid4()
#: Melun : ~45 km de Paris — hors des 30 km, dans les 50.
MELUN = Location(label="Melun", latitude=48.54, longitude=2.66, department_code="77")


class Env:
    def __init__(self) -> None:
        self.offers = InMemoryOfferRepository()
        self.index = InMemoryOfferIndex()
        self.conversations = InMemoryConversationRepository()
        self.criteria = InMemoryCriteriaRepository()
        self.dialogue = FakeDialogue()
        self.states = InMemoryStateRepository()
        search = SearchOffers(embedder=FakeEmbedder(), index=self.index, offers=self.offers)
        self.apply = ApplyCriteria(
            criteria=self.criteria,
            criteria_index=InMemoryCriteriaIndex(),
            search=search,
            feed=InMemoryFeedRepository(),
            states=self.states,
            targets=InMemoryTargetRepository(),
            index=self.index,
            offers=self.offers,
            geocoder=FakeGeocoder({"Paris": PARIS}),
            clock=FixedClock(),
            uow=RecordingUnitOfWork(),
        )
        self.widen = SuggestWidenings(search=search)
        self.converse = ConverseSearch(
            conversations=self.conversations,
            criteria=self.criteria,
            dialogue=self.dialogue,
            apply=self.apply,
            widen=self.widen,
            memory=InMemoryMemoryRepository(),
            queue=RecordingQueue(),
            clock=FixedClock(),
            constitution_version="test",
        )

    def populate(self, *, near: int, melun: int, commis: int = 0) -> None:
        """Des serveurs à Paris, des serveurs à Melun, des commis à Paris — tous candidats."""
        offers = [
            *(make_offer() for _ in range(near)),
            *(make_offer(location=MELUN) for _ in range(melun)),
            *(make_offer(title="Commis de salle H/F") for _ in range(commis)),
        ]
        self.offers.add(*offers)
        self.index.candidates = [o.id for o in offers]


@pytest.fixture
def env() -> Env:
    return Env()


class TestSuggestWidenings:
    async def test_le_rayon_qui_rapporte_est_propose_avec_son_gain_compte(self, env: Env) -> None:
        env.populate(near=3, melun=12)

        chosen = await env.widen.execute(make_criteria(), current=3)

        assert [(w.id, w.gain) for w in chosen] == [("radius-50", 12)]
        assert chosen[0].kind is WideningKind.RADIUS
        assert chosen[0].criteria.location is not None
        assert chosen[0].criteria.location.radius_km == 50
        # radius-100 et toute la France rapportent autant : le lieu est une seule
        # famille, et le plus petit rayon l'emporte à gain égal.

    async def test_les_intitules_voisins_viennent_des_ecartees_sans_requete(self, env: Env) -> None:
        env.populate(near=3, melun=0, commis=12)
        offers = list(env.offers.offers.values())
        rejected = [o for o in offers if o.title.startswith("Commis")]

        chosen = await env.widen.execute(make_criteria(), current=3, rejected=rejected)

        assert [(w.id, w.gain) for w in chosen] == [("title-commis-de-salle", 12)]
        assert chosen[0].message == "Ajouter « commis de salle » : +12"

    async def test_les_offres_ecartees_par_la_personne_ne_comptent_pas(self, env: Env) -> None:
        env.populate(near=3, melun=12)
        dismissed = frozenset(o.id for o in env.offers.offers.values() if o.location == MELUN)

        assert await env.widen.execute(make_criteria(), current=3, exclude=dismissed) == []


class TestDansLaConversation:
    async def test_une_recherche_maigre_propose_et_garde_les_elargissements(self, env: Env) -> None:
        env.populate(near=3, melun=12)
        env.dialogue.proposal = DialogueProposal(
            reply="Je cherche des postes de serveur à Paris.",
            criteria=make_criteria(),
            suggestions=("Uniquement en CDI",),
        )

        turn = await env.converse.execute(USER, conversation_id=None, content="serveur à Paris")

        assert turn.status is DialogueStatus.SEARCH
        assert turn.results_count == 3
        assert [(w.id, w.gain) for w in turn.widenings] == [("radius-50", 12)]
        assert turn.suggestions == ("Élargir à 50 km : +12", "Uniquement en CDI")
        assert "élargir à 50 km autour de Paris (+12). Je le fais ?" in turn.message.content
        assert env.conversations.pending[turn.conversation_id] == list(turn.widenings)

    async def test_la_reponse_rapide_applique_l_elargissement_sans_le_modele(
        self, env: Env
    ) -> None:
        env.populate(near=3, melun=12)
        env.dialogue.proposal = DialogueProposal(reply="Je cherche.", criteria=make_criteria())
        first = await env.converse.execute(USER, conversation_id=None, content="serveur à Paris")
        calls = len(env.dialogue.calls)

        second = await env.converse.execute(
            USER, conversation_id=first.conversation_id, content="Élargir à 50 km : +12"
        )

        assert len(env.dialogue.calls) == calls  # pas d'appel au modèle
        assert second.status is DialogueStatus.SEARCH
        assert second.intent is Intent.REFINE
        assert second.results_count == 15
        assert second.criteria is not None
        assert second.criteria.criteria.location is not None
        assert second.criteria.criteria.location.radius_km == 50
        assert second.message.content.startswith(
            "C'est fait, j'ai élargi : élargir à 50 km autour de Paris. J'ai trouvé 15 offres"
        )
        assert second.widenings == ()  # plus rien à proposer : le rayon 100 n'ajoute rien
        assert env.conversations.pending[first.conversation_id] == []

    async def test_le_modele_recoit_les_propositions_en_donnees(self, env: Env) -> None:
        env.populate(near=3, melun=12)
        env.dialogue.proposal = DialogueProposal(reply="Je cherche.", criteria=make_criteria())
        first = await env.converse.execute(USER, conversation_id=None, content="serveur à Paris")

        await env.converse.execute(
            USER, conversation_id=first.conversation_id, content="oui pour les 50 km"
        )

        facts = env.dialogue.calls[-1]["facts"]
        assert facts is not None
        assert "radius-50 : élargir à 50 km autour de Paris (+12 offres)" in facts
        assert env.dialogue.calls[0]["facts"] is None

    async def test_sans_suggesteur_le_tour_reste_celui_d_avant(self, env: Env) -> None:
        env.populate(near=3, melun=12)
        env.dialogue.proposal = DialogueProposal(reply="Je cherche.", criteria=make_criteria())
        plain = ConverseSearch(
            conversations=env.conversations,
            criteria=env.criteria,
            dialogue=env.dialogue,
            apply=env.apply,
            memory=InMemoryMemoryRepository(),
            queue=RecordingQueue(),
            clock=FixedClock(),
            constitution_version="test",
        )

        turn = await plain.execute(USER, conversation_id=None, content="serveur à Paris")

        assert turn.widenings == ()
        assert turn.message.content == "Je cherche. J'ai trouvé 3 offres, elles sont dans ton fil."
