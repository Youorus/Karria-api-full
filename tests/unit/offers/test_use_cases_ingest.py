"""`IngestPostings` : normaliser, dédoublonner, enregistrer par lot — sans base."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from karria.modules.offers.application import IngestPostings, IngestStats
from karria.modules.offers.domain import Company, Offer, Source
from offers.fakes import (
    LONG_DESCRIPTION,
    NOW,
    PARIS,
    FakeGeocoder,
    FixedClock,
    InMemoryOfferRepository,
    InMemoryOutbox,
    RecordingUnitOfWork,
    make_offer,
    make_posting,
)

pytestmark = pytest.mark.unit


class Env:
    """Le cas d'usage et ses doublures."""

    def __init__(self, offers: InMemoryOfferRepository | None = None) -> None:
        self.offers = offers or InMemoryOfferRepository()
        self.outbox = InMemoryOutbox()
        self.geocoder = FakeGeocoder({"Paris": PARIS})
        self.clock = FixedClock()
        self.uow = RecordingUnitOfWork()
        self.ingest = IngestPostings(
            offers=self.offers,
            outbox=self.outbox,
            geocoder=self.geocoder,
            clock=self.clock,
            uow=self.uow,
        )

    def only(self) -> Offer:
        """La seule offre du corpus."""
        (offer,) = self.offers.offers.values()
        return offer


@pytest.fixture
def env() -> Env:
    return Env()


class TestNouvelles:
    async def test_une_annonce_nouvelle_est_inseree_geocodee_et_inscrite_a_l_index(
        self, env: Env
    ) -> None:
        stats = await env.ingest.execute([make_posting()])

        offer = env.only()
        assert stats.inserted == 1
        assert stats.inserted_ids == [offer.id]
        assert offer.location is not None
        assert offer.location.latitude == PARIS.latitude
        assert offer.content_hash
        assert env.outbox.offer_ids == [offer.id]
        assert env.uow.transactions == 1

    async def test_un_titre_vide_compte_une_erreur_et_n_ecrit_rien(self, env: Env) -> None:
        stats = await env.ingest.execute([make_posting(title="   ")])

        assert stats == IngestStats(received=1, errors=1)
        assert env.offers.offers == {}
        assert env.outbox.items == []

    async def test_la_meme_annonce_deux_fois_dans_le_lot_n_est_traitee_qu_une_fois(
        self, env: Env
    ) -> None:
        posting = make_posting()

        stats = await env.ingest.execute([posting, posting])

        assert stats.received == 2
        assert stats.inserted == 1
        assert len(env.offers.offers) == 1

    async def test_une_annonce_sans_lieu_n_interroge_pas_le_geocodeur(self, env: Env) -> None:
        await env.ingest.execute([make_posting(location_label=None)])

        assert env.geocoder.queries == []
        assert env.only().location is None

    async def test_une_annonce_en_echec_n_arrete_pas_les_autres(self, env: Env) -> None:
        env.geocoder.failing.add("Lyon")
        broken = make_posting(location_label="Lyon")
        fine = make_posting(description="Une autre annonce, tout à fait différente.")

        stats = await env.ingest.execute([broken, fine])

        assert stats.errors == 1
        assert stats.inserted == 1
        assert env.only().source_id == fine.source_id


class TestRevues:
    async def test_une_annonce_revue_a_l_identique_est_seulement_rafraichie(self, env: Env) -> None:
        posting = make_posting()
        await env.ingest.execute([posting])
        env.outbox.items.clear()
        env.geocoder.queries.clear()
        env.clock.advance(timedelta(hours=3))

        stats = await env.ingest.execute([posting])

        assert stats.refreshed == 1
        assert stats.inserted == 0
        assert env.geocoder.queries == []
        assert env.only().last_seen_at == env.clock.now()
        assert env.outbox.items == []
        assert env.offers.saved == []

    async def test_une_annonce_revue_sous_une_date_plus_recente_est_republiee(
        self, env: Env
    ) -> None:
        posting = make_posting(date_posted=(NOW - timedelta(days=10)).date())
        await env.ingest.execute([posting])
        env.outbox.items.clear()
        offer = env.only()

        stats = await env.ingest.execute([dataclasses.replace(posting, date_posted=NOW.date())])

        republished = env.only()
        assert stats.refreshed == 1
        assert republished.published_at > offer.published_at
        assert republished.last_seen_at == NOW
        assert env.outbox.offer_ids == [offer.id]
        assert env.offers.seen_calls == []

    async def test_un_doublon_revu_plus_recent_ne_republie_rien(self, env: Env) -> None:
        old = (NOW - timedelta(days=10)).date()
        first = make_posting(date_posted=old)
        copy = make_posting(date_posted=old)
        await env.ingest.execute([first, copy])
        env.outbox.items.clear()
        env.offers.saved.clear()

        stats = await env.ingest.execute([dataclasses.replace(copy, date_posted=old)])

        assert stats.refreshed == 1
        assert env.offers.saved == []
        assert env.outbox.items == []

    async def test_un_contenu_modifie_garde_l_identite_et_se_resynchronise(self, env: Env) -> None:
        posting = make_posting()
        await env.ingest.execute([posting])
        before = env.offers.offers[env.only().id] = dataclasses.replace(
            env.only(), indexed_version="v1", indexed_hash="ancien"
        )
        env.outbox.items.clear()
        env.clock.advance(timedelta(days=1))

        stats = await env.ingest.execute(
            [dataclasses.replace(posting, description="Le contenu a changé : brunch le dimanche.")]
        )

        after = env.only()
        assert stats.updated == 1
        assert stats.inserted == 0
        assert after.id == before.id
        assert after.first_seen_at == before.first_seen_at
        assert after.content_hash != before.content_hash
        assert after.indexed_version == "v1"
        assert after.indexed_hash == "ancien"
        assert after.published_at >= before.published_at
        assert env.outbox.offer_ids == [after.id]

    async def test_la_renormalisation_rejoue_une_annonce_inchangee_sans_la_declarer_doublon(
        self, env: Env
    ) -> None:
        posting = make_posting()
        await env.ingest.execute([posting])
        before = env.only()
        env.outbox.items.clear()

        stats = await env.ingest.execute([posting], renormalize=True)

        after = env.only()
        assert stats.updated == 1
        assert stats.refreshed == 0
        assert stats.duplicates == 0
        assert after.id == before.id
        assert after.duplicate_of is None
        assert after.content_hash == before.content_hash
        assert env.outbox.offer_ids == [after.id]


class TestDoublons:
    async def test_un_doublon_exact_d_une_offre_connue_pointe_vers_la_canonique(
        self, env: Env
    ) -> None:
        await env.ingest.execute([make_posting()])
        canonical = env.only()
        env.outbox.items.clear()

        stats = await env.ingest.execute([make_posting()])

        duplicate = next(o for o in env.offers.offers.values() if o.id != canonical.id)
        assert stats.duplicates == 1
        assert stats.inserted == 0
        assert duplicate.duplicate_of == canonical.id
        assert env.outbox.items == []
        assert canonical.id in env.offers.seen_calls[-1]

    async def test_un_doublon_plus_recent_republie_la_canonique(self, env: Env) -> None:
        await env.ingest.execute([make_posting(date_posted=(NOW - timedelta(days=20)).date())])
        canonical = env.only()
        env.outbox.items.clear()

        await env.ingest.execute([make_posting(date_posted=NOW.date())])

        assert env.offers.offers[canonical.id].published_at > canonical.published_at
        assert env.outbox.offer_ids == [canonical.id]

    async def test_deux_doublons_republient_la_canonique_a_la_date_la_plus_recente(
        self, env: Env
    ) -> None:
        await env.ingest.execute([make_posting(date_posted=(NOW - timedelta(days=20)).date())])
        canonical = env.only()

        await env.ingest.execute(
            [
                make_posting(date_posted=(NOW - timedelta(days=5)).date()),
                make_posting(date_posted=(NOW - timedelta(days=2)).date()),
                make_posting(date_posted=(NOW - timedelta(days=9)).date()),
            ]
        )

        published = env.offers.offers[canonical.id].published_at
        assert published.date() == (NOW - timedelta(days=2)).date()

    async def test_deux_copies_exactes_dans_le_meme_lot_n_en_inserent_qu_une_canonique(
        self, env: Env
    ) -> None:
        stats = await env.ingest.execute([make_posting(), make_posting()])

        canonical = [o for o in env.offers.offers.values() if o.is_canonical]
        assert stats.inserted == 1
        assert stats.duplicates == 1
        assert len(canonical) == 1
        assert len(env.offers.offers) == 2

    async def test_un_quasi_doublon_d_une_offre_connue_est_rattache(self, env: Env) -> None:
        await env.ingest.execute([make_posting(description=LONG_DESCRIPTION)])
        canonical = env.only()

        stats = await env.ingest.execute([make_posting(description=f"{LONG_DESCRIPTION} fin")])

        assert stats.near_duplicates == 1
        near = next(o for o in env.offers.offers.values() if o.id != canonical.id)
        assert near.duplicate_of == canonical.id

    async def test_un_quasi_doublon_dans_le_meme_lot_est_rattache_sans_employeur(
        self, env: Env
    ) -> None:
        stats = await env.ingest.execute(
            [
                make_posting(company_name=None, description=LONG_DESCRIPTION),
                make_posting(company_name=None, description=f"{LONG_DESCRIPTION} suite"),
            ]
        )

        assert stats.inserted == 1
        assert stats.near_duplicates == 1

    async def test_un_texte_proche_d_un_autre_employeur_n_est_pas_un_doublon(
        self, env: Env
    ) -> None:
        stats = await env.ingest.execute(
            [
                make_posting(description=LONG_DESCRIPTION),
                make_posting(company_name="Autre maison", description=f"{LONG_DESCRIPTION} fin"),
            ]
        )

        assert stats.inserted == 2
        assert stats.near_duplicates == 0


class _ScriptedNear(InMemoryOfferRepository):
    """Rend des candidats imposés, pour exercer le choix du plus proche."""

    def __init__(self, candidates: Sequence[Offer]) -> None:
        super().__init__()
        self.scripted = list(candidates)

    async def near_duplicate_candidates(
        self, *, bands: tuple[int, int, int, int], company_key: str | None, since: datetime
    ) -> Sequence[Offer]:
        return self.scripted


class TestChoixDuQuasiDoublon:
    async def test_le_candidat_le_plus_proche_l_emporte_et_les_doublons_sont_ignores(
        self,
    ) -> None:
        target = make_offer(
            company=Company(name="Brasserie du Nord"), description=f"{LONG_DESCRIPTION} fin"
        )
        far = dataclasses.replace(target, id=make_offer().id, simhash=target.simhash ^ 0b111)
        close = dataclasses.replace(target, id=make_offer().id, simhash=target.simhash ^ 0b1)
        closest_but_duplicate = dataclasses.replace(target, id=make_offer().id, duplicate_of=far.id)
        unrelated = dataclasses.replace(
            target, id=make_offer().id, simhash=target.simhash ^ ((1 << 64) - 1)
        )
        env = Env(_ScriptedNear([unrelated, far, closest_but_duplicate, close, far]))

        await env.ingest.execute([make_posting(description=f"{LONG_DESCRIPTION} fin")])

        (inserted,) = env.offers.offers.values()
        assert inserted.duplicate_of == close.id


class TestRetraits:
    async def test_une_annonce_retiree_par_la_source_est_desactivee_et_retiree_de_l_index(
        self, env: Env
    ) -> None:
        known = make_offer(source=Source.FRANCE_TRAVAIL, source_id="215NPDX")
        env.offers.add(known)

        stats = await env.ingest.execute(
            [
                make_posting(
                    source=Source.FRANCE_TRAVAIL, source_id="215NPDX", title="", expired=True
                )
            ]
        )

        assert stats.expired == 1
        assert stats.errors == 0
        assert env.offers.offers[known.id].is_active is False
        assert env.outbox.offer_ids == [known.id]

    async def test_un_retrait_inconnu_ou_deja_inactif_ne_fait_rien(self, env: Env) -> None:
        env.offers.add(make_offer(source=Source.FRANCE_TRAVAIL, source_id="OLD", is_active=False))

        stats = await env.ingest.execute(
            [
                make_posting(source=Source.FRANCE_TRAVAIL, source_id="OLD", title="", expired=True),
                make_posting(source=Source.FRANCE_TRAVAIL, source_id="NEW", title="", expired=True),
            ]
        )

        assert stats.expired == 0
        assert env.outbox.items == []
        assert env.uow.transactions == 1

    async def test_une_annonce_remise_et_retiree_dans_le_meme_lot_reste(self, env: Env) -> None:
        known = make_offer(source=Source.FRANCE_TRAVAIL, source_id="X")
        env.offers.add(known)
        posting = make_posting(source=Source.FRANCE_TRAVAIL, source_id="X", title=known.title)

        await env.ingest.execute(
            [
                posting,
                make_posting(source=Source.FRANCE_TRAVAIL, source_id="X", title="", expired=True),
            ]
        )

        assert env.offers.offers[known.id].is_active is True


class TestSourceSituee:
    async def test_une_annonce_situee_par_sa_source_n_interroge_pas_le_geocodeur(
        self, env: Env
    ) -> None:
        await env.ingest.execute(
            [
                make_posting(
                    source=Source.FRANCE_TRAVAIL,
                    location_label="Fleure (86)",
                    latitude=46.47,
                    longitude=0.52,
                    postal_code="86340",
                )
            ]
        )

        offer = env.only()
        assert env.geocoder.queries == []
        assert offer.location is not None
        assert (offer.location.city, offer.location.latitude) == ("Fleure", 46.47)
