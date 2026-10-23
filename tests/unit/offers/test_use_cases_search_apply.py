"""`SearchOffers`, `ApplyCriteria` : de la demande au fil."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from karria.modules.offers.application import (
    ApplyCriteria,
    IndexedOffer,
    SearchOffers,
)
from karria.modules.offers.domain import (
    RETRIEVAL_LIMIT,
    THIN_RESULTS,
    CriteriaIncompleteError,
    Location,
    LocationCriteria,
    Offer,
    OfferState,
)
from offers.fakes import (
    NOW,
    PARIS,
    FakeEmbedder,
    FakeGeocoder,
    FixedClock,
    InMemoryCriteriaIndex,
    InMemoryCriteriaRepository,
    InMemoryFeedRepository,
    InMemoryOfferIndex,
    InMemoryOfferRepository,
    InMemoryStateRepository,
    InMemoryTargetRepository,
    RecordingUnitOfWork,
    make_criteria,
    make_entry,
    make_offer,
    make_stored,
)

pytestmark = pytest.mark.unit

LYON = Location(label="Lyon", city="Lyon", department_code="69", latitude=45.76, longitude=4.84)


class Env:
    def __init__(self) -> None:
        self.offers = InMemoryOfferRepository()
        self.index = InMemoryOfferIndex()
        self.embedder = FakeEmbedder()
        self.search = SearchOffers(embedder=self.embedder, index=self.index, offers=self.offers)
        self.criteria = InMemoryCriteriaRepository()
        self.criteria_index = InMemoryCriteriaIndex()
        self.feed = InMemoryFeedRepository()
        self.states = InMemoryStateRepository()
        self.targets = InMemoryTargetRepository()
        self.geocoder = FakeGeocoder({"Paris": PARIS})
        self.clock = FixedClock()
        self.apply = ApplyCriteria(
            criteria=self.criteria,
            criteria_index=self.criteria_index,
            search=self.search,
            feed=self.feed,
            states=self.states,
            targets=self.targets,
            index=self.index,
            offers=self.offers,
            geocoder=self.geocoder,
            clock=self.clock,
            uow=RecordingUnitOfWork(),
        )

    def corpus(self, *offers: Offer) -> list[UUID]:
        """Pose des offres et en fait les candidats de l'index, dans l'ordre."""
        self.offers.add(*offers)
        self.index.candidates.extend(o.id for o in offers)
        return [o.id for o in offers]

    def vocabulary(self, *titles: str) -> None:
        """Des titres réels dans l'index, pour valider les termes de collecte."""
        for title in titles:
            offer = make_offer(title=title)
            self.offers.add(offer)
            self.index.points[offer.id] = IndexedOffer(
                offer=offer, dense=(0.0, 1.0), title_text=title, body_text=title
            )


@pytest.fixture
def env() -> Env:
    return Env()


USER = uuid4()


class TestRecherche:
    async def test_sans_candidat_la_recherche_est_vide(self, env: Env) -> None:
        result = await env.search.execute(make_criteria())

        assert result.ranked == []
        assert result.offers == {}
        assert result.query_vector == (0.0, 1.0)

    async def test_la_requete_porte_les_intitules_les_mots_cles_et_la_fenetre(
        self, env: Env
    ) -> None:
        env.corpus(make_offer())

        await env.search.execute(make_criteria())

        query = env.index.queries[0]
        assert query.title_terms == "serveur serveuse"
        assert query.body_terms == "serveur serveuse restauration"
        assert (query.offset, query.limit) == (0, RETRIEVAL_LIMIT)

    async def test_les_candidats_exclus_sont_ignores(self, env: Env) -> None:
        (only,) = env.corpus(make_offer())

        result = await env.search.execute(make_criteria(), exclude=frozenset({only}))

        assert result.ranked == []
        assert result.offers == {}

    async def test_le_filtre_dur_et_la_porte_d_intitule_decident(self, env: Env) -> None:
        missing, kept, other_job, elsewhere = env.corpus(
            make_offer(title="Serveur disparu"),
            make_offer(title="Serveur"),
            make_offer(title="Plongeur"),
            make_offer(title="Serveur à Lyon", location=LYON),
        )
        env.offers.offers.pop(missing)  # retirée de la base entre l'index et la lecture

        result = await env.search.execute(make_criteria())

        assert [r.offer_id for r in result.ranked] == [kept]
        assert {other_job, elsewhere}.isdisjoint(r.offer_id for r in result.ranked)
        assert result.candidates == 4


class TestApplication:
    async def test_des_criteres_incomplets_sont_refuses(self, env: Env) -> None:
        with pytest.raises(CriteriaIncompleteError):
            await env.apply.execute(USER, make_criteria(job_titles=[]))

    async def test_un_lieu_nomme_sans_coordonnees_est_geocode(self, env: Env) -> None:
        criteria = make_criteria(location=LocationCriteria(label="Paris"))

        applied = await env.apply.execute(USER, criteria)

        location = applied.stored.criteria.location
        assert location is not None
        assert location.latitude == PARIS.latitude
        assert env.geocoder.queries == ["Paris"]

    async def test_un_lieu_introuvable_laisse_les_criteres_incomplets(self, env: Env) -> None:
        with pytest.raises(CriteriaIncompleteError):
            await env.apply.execute(
                USER, make_criteria(location=LocationCriteria(label="Atlantide"))
            )

    async def test_un_lieu_vide_n_est_pas_geocode(self, env: Env) -> None:
        criteria = make_criteria(location=LocationCriteria(label=" "), whole_france=True)

        await env.apply.execute(USER, criteria)

        assert env.geocoder.queries == []

    async def test_toute_la_france_sans_lieu_ne_geocode_rien(self, env: Env) -> None:
        await env.apply.execute(USER, make_criteria(location=None, whole_france=True))

        assert env.geocoder.queries == []

    async def test_le_fil_est_reconstruit_et_les_criteres_enregistres_et_indexes(
        self, env: Env
    ) -> None:
        first, second = env.corpus(make_offer(), make_offer())

        applied = await env.apply.execute(USER, make_criteria())

        assert applied.results == 2
        assert {e.offer_id for e in await env.feed.entries_of(USER)} == {first, second}
        assert env.criteria.stored[USER] == applied.stored
        assert applied.stored.alerts_enabled is True
        assert env.criteria_index.points[USER][0] == (0.0, 1.0)

    async def test_les_offres_ecartees_ne_reviennent_pas(self, env: Env) -> None:
        dismissed, kept = env.corpus(make_offer(), make_offer())
        env.states.add(OfferState(user_id=USER, offer_id=dismissed, dismissed=True))

        await env.apply.execute(USER, make_criteria())

        assert {e.offer_id for e in await env.feed.entries_of(USER)} == {kept}

    async def test_une_offre_prise_en_main_reste_et_les_autres_partent(self, env: Env) -> None:
        saved, stale = uuid4(), uuid4()
        env.feed.add(make_entry(USER, saved), make_entry(USER, stale))
        env.states.add(OfferState(user_id=USER, offer_id=saved, saved=True))

        await env.apply.execute(USER, make_criteria())

        assert {e.offer_id for e in await env.feed.entries_of(USER)} == {saved}

    async def test_l_etat_des_alertes_precedent_est_conserve(self, env: Env) -> None:
        previous = make_stored(
            USER, alerts_enabled=False, last_alert_at=NOW, alerts_sent_on=NOW, alerts_sent_today=2
        )
        env.criteria.stored[USER] = previous

        applied = await env.apply.execute(USER, make_criteria(job_titles=["barman"]))

        stored = applied.stored
        assert (stored.alerts_enabled, stored.alerts_sent_today) == (False, 2)
        assert stored.last_alert_at == NOW
        assert env.criteria_index.points[USER][2] is False

    async def test_peu_de_resultats_rendent_les_cibles_dues_tout_de_suite(self, env: Env) -> None:
        env.corpus(make_offer())

        applied = await env.apply.execute(USER, make_criteria())

        assert applied.results < THIN_RESULTS
        targets, due_now = env.targets.upserts[0]
        assert due_now is True
        assert {t.search_term for t in targets} == {"serveur", "serveuse"}
        assert {t.location_key for t in targets} == {"75"}
        assert set(env.targets.demands) == {(USER, t.key) for t in targets}

    async def test_assez_de_resultats_demandent_les_cibles_sans_les_rendre_dues(
        self, env: Env
    ) -> None:
        env.corpus(*(make_offer() for _ in range(THIN_RESULTS)))

        await env.apply.execute(USER, make_criteria())

        targets, due_now = env.targets.upserts[0]
        assert due_now is False
        assert len(targets) == 2

    async def test_les_termes_de_collecte_sont_valides_contre_les_titres_reels(
        self, env: Env
    ) -> None:
        env.vocabulary("Chef de partie H/F", "Commis de cuisine", "Serveur / Serveuse")

        await env.apply.execute(
            USER, make_criteria(search_terms=["serveur", "chef de partie", "chef de rang"])
        )

        targets, _ = env.targets.upserts[0]
        # « chef de rang » : l'index rapproche « Chef de partie », qui ne dit pas ce métier → refusé.
        assert [t.search_term for t in targets] == ["serveur", "chef de partie"]

    async def test_un_terme_inconnu_de_l_index_entre_a_l_essai(self, env: Env) -> None:
        env.vocabulary("Serveur / Serveuse")

        await env.apply.execute(USER, make_criteria(search_terms=["serveur", "sommelier"]))

        targets, _ = env.targets.upserts[0]
        assert [t.search_term for t in targets] == ["serveur", "sommelier"]

    async def test_une_phrase_ne_devient_jamais_une_cible(self, env: Env) -> None:
        await env.apply.execute(
            USER,
            make_criteria(
                job_titles=["serveur dans un restaurant pas trop stressant le soir"],
                search_terms=["je veux un poste de serveur dans un bon restaurant"],
                preferences=["pas trop de stress"],
            ),
        )

        assert env.targets.upserts == []
        assert env.targets.demands == {}

    async def test_une_nouvelle_recherche_remplace_les_demandes_precedentes(self, env: Env) -> None:
        await env.apply.execute(USER, make_criteria(search_terms=["serveur"]))
        await env.apply.execute(USER, make_criteria(search_terms=["barman"]))

        assert [key for (_, key) in env.targets.demands] == ["barman|75|-"]
