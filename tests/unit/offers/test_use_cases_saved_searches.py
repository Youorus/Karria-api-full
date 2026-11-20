"""Les recherches enregistrées (ADR-0041) : retenues, listées, activées, coupées — et surveillées."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from karria.modules.offers.application import (
    AlertSummary,
    ClearCriteria,
    CriteriaMatch,
    DeleteSavedSearch,
    DispatchAlerts,
    IndexedOffer,
    ListSavedSearches,
    SavedSearch,
    SavedSearches,
    SetAlertsEnabled,
    SetSavedSearchAlerts,
)
from karria.modules.offers.application.use_cases import AppliedCriteria, with_saved_search_hint
from karria.modules.offers.domain import (
    ALERT_DAILY_MAX,
    ALERT_MIN_INTERVAL,
    ALERT_MIN_SIMILARITY,
    Company,
    Offer,
    OfferState,
)
from karria.modules.offers.domain.errors import SavedSearchNotFoundError
from karria.modules.offers.domain.saved import (
    HINT_UNTIL,
    MAX_SAVED_SEARCHES,
    NOTIFIED_MEMORY,
    SAVED_SEARCH_HINT,
    alert_rhythm,
    demanded_keys,
    remember_notified,
    to_evict,
)
from karria.shared.push import PushKind
from offers.fakes import (
    NOW,
    FixedClock,
    InMemoryCriteriaIndex,
    InMemoryCriteriaRepository,
    InMemoryFeedRepository,
    InMemorySavedSearchRepository,
    InMemoryStateRepository,
    InMemoryTargetRepository,
    RecordingNotifier,
    RecordingUnitOfWork,
    make_criteria,
    make_offer,
    make_saved,
    make_stored,
)

pytestmark = pytest.mark.unit

USER = uuid4()
VECTOR = (1.0, 0.0)
COOK = make_criteria(query="cuisinier à Paris", job_titles=["cuisinier"], keywords=["cuisine"])


class Env:
    """Le registre et tout ce qu'il tient en phase."""

    def __init__(self) -> None:
        self.saved = InMemorySavedSearchRepository()
        self.criteria = InMemoryCriteriaRepository()
        self.index = InMemoryCriteriaIndex()
        self.targets = InMemoryTargetRepository()
        self.feed = InMemoryFeedRepository()
        self.states = InMemoryStateRepository()
        self.notifier = RecordingNotifier()
        self.clock = FixedClock()
        self.registry = SavedSearches(
            saved=self.saved, criteria_index=self.index, targets=self.targets
        )
        self.clear = ClearCriteria(
            criteria=self.criteria,
            criteria_index=self.index,
            feed=self.feed,
            uow=RecordingUnitOfWork(),
            saved=self.registry,
            clock=self.clock,
        )

    async def apply(self, criteria=None, *, keys: tuple[str, ...] = ("serveur|paris",)):  # noqa: ANN001
        """Ce que fait `ApplyCriteria` : la recherche devient la recherche en cours, et est retenue."""
        self.clock.advance(timedelta(minutes=1))
        stored = make_stored(USER, criteria, updated_at=self.clock.now())
        known = await self.registry.alerts_flag(USER, stored.fingerprint)
        stored = dataclasses.replace(stored, alerts_enabled=True if known is None else known)
        await self.criteria.save(stored)
        await self.index.upsert(USER, VECTOR, stored.criteria, alerts_enabled=stored.alerts_enabled)
        return await self.registry.remember(stored, VECTOR, keys, now=self.clock.now())

    def dispatch(self) -> DispatchAlerts:
        return DispatchAlerts(
            criteria=self.criteria,
            criteria_index=self.index,
            feed=self.feed,
            states=self.states,
            notifier=self.notifier,
            clock=self.clock,
            uow=RecordingUnitOfWork(),
            saved=self.saved,
        )

    async def run(self, *offers: Offer) -> AlertSummary:
        return await self.dispatch().execute(
            [
                IndexedOffer(offer=o, dense=VECTOR, title_text=o.title, body_text=o.title)
                for o in offers
            ]
        )

    def demands(self) -> set[str]:
        return {key for user, key in self.targets.demands if user == USER}


def cook_offer(**changes: Any) -> Offer:
    changes.setdefault("title", "Cuisinier H/F")
    changes.setdefault("description", "Cuisine traditionnelle, mise en place.")
    return make_offer(**changes)


class TestRegles:
    def test_au_dela_de_dix_la_moins_recemment_utilisee_sort_jamais_la_courante(self) -> None:
        searches = [
            make_saved(
                USER,
                make_criteria(query=f"métier {n}", job_titles=[f"métier {n}"]),
                last_used_at=NOW - timedelta(days=n),
            )
            for n in range(MAX_SAVED_SEARCHES + 2)
        ]
        oldest, second = searches[-1], searches[-2]
        assert to_evict(searches[:MAX_SAVED_SEARCHES], current=None) == []
        assert to_evict(searches, current=None) == [oldest, second]
        # La recherche en cours ne sort jamais, même si elle est la plus ancienne.
        assert oldest not in to_evict(searches, current=oldest.fingerprint)

    def test_la_collecte_ne_suit_que_les_alertes_actives(self) -> None:
        on = make_saved(USER, target_keys=("a", "b"))
        off = make_saved(USER, COOK, target_keys=("b", "c"), alerts_enabled=False)
        assert demanded_keys([on, off]) == ["a", "b"]
        assert demanded_keys([off]) == []

    def test_le_rythme_est_celui_de_la_personne(self) -> None:
        one = make_saved(
            USER, last_alert_at=NOW - timedelta(hours=5), alerts_sent_on=NOW, alerts_sent_today=2
        )
        two = make_saved(
            USER,
            COOK,
            last_alert_at=NOW - timedelta(hours=1),
            alerts_sent_on=NOW - timedelta(days=1),
            alerts_sent_today=3,
        )
        last, today = alert_rhythm([one, two], now=NOW)
        assert last == NOW - timedelta(hours=1)
        assert today == 2
        assert alert_rhythm([], now=NOW) == (None, 0)

    def test_les_offres_signalees_sont_bornees_et_sans_doublon(self) -> None:
        a, b = uuid4(), uuid4()
        assert remember_notified((a,), [a, b]) == (a, b)
        many = [uuid4() for _ in range(NOTIFIED_MEMORY + 5)]
        assert len(remember_notified((), many)) == NOTIFIED_MEMORY


class TestRetenir:
    async def test_chaque_recherche_appliquee_est_retenue_avec_son_point_dindex(self) -> None:
        env = Env()
        first = await env.apply()
        second = await env.apply(COOK, keys=("cuisinier|paris",))

        listed = await ListSavedSearches(saved=env.saved, criteria=env.criteria).execute(USER)

        assert [item.search.id for item in listed] == [second.search.id, first.search.id]
        assert [item.is_current for item in listed] == [True, False]
        # L'ancienne recherche garde son propre point : elle surveille toujours.
        assert env.index.points[first.search.id][2] is True
        assert env.index.points[second.search.id][2] is True
        assert env.demands() == {"serveur|paris", "cuisinier|paris"}

    async def test_reappliquer_la_meme_recherche_ne_la_double_pas(self) -> None:
        env = Env()
        first = await env.apply()
        again = await env.apply()

        assert again.search.id == first.search.id
        assert (first.first_time, again.first_time) == (True, False)
        assert again.search.last_used_at > first.search.last_used_at
        assert len(env.saved.items) == 1

    async def test_une_recherche_coupee_le_reste_quand_on_la_reapplique(self) -> None:
        env = Env()
        first = await env.apply()
        await env.registry.set_enabled(USER, first.search.fingerprint, enabled=False, now=NOW)

        again = await env.apply()

        assert again.search.alerts_enabled is False
        assert (await env.criteria.find(USER)).alerts_enabled is False

    async def test_au_dela_de_dix_la_plus_ancienne_sort_avec_son_point(self) -> None:
        env = Env()
        first = await env.apply()
        for n in range(MAX_SAVED_SEARCHES):
            await env.apply(make_criteria(query=f"métier {n}", job_titles=[f"métier {n}"]))

        assert len(env.saved.items) == MAX_SAVED_SEARCHES
        assert first.search.id not in env.saved.items
        assert first.search.id not in env.index.points

    async def test_karria_ne_le_dit_que_les_deux_premieres_fois(self) -> None:
        env = Env()
        hints = [(await env.apply()).hint, (await env.apply(COOK)).hint]
        hints.append(
            (await env.apply(make_criteria(query="plongeur", job_titles=["plongeur"]))).hint
        )
        hints.append((await env.apply()).hint)  # déjà enregistrée : rien

        assert hints == [True, True, False, False]
        assert HINT_UNTIL == 2

    def test_la_phrase_de_karria_tient_en_une_ligne(self) -> None:
        stored = make_stored(USER)
        told = with_saved_search_hint("3 offres.", AppliedCriteria(stored, 3, hint=True))
        quiet = with_saved_search_hint("3 offres.", AppliedCriteria(stored, 3))
        assert told == f"3 offres. {SAVED_SEARCH_HINT}"
        assert quiet == "3 offres."
        assert "paramètres" in SAVED_SEARCH_HINT
        assert len(SAVED_SEARCH_HINT) < 140


class TestParametres:
    def toggle(self, env: Env) -> SetSavedSearchAlerts:
        return SetSavedSearchAlerts(
            saved=env.saved,
            registry=env.registry,
            criteria=env.criteria,
            criteria_index=env.index,
            clock=env.clock,
        )

    def delete(self, env: Env) -> DeleteSavedSearch:
        return DeleteSavedSearch(
            saved=env.saved,
            registry=env.registry,
            criteria=env.criteria,
            criteria_index=env.index,
            clear=env.clear,
            clock=env.clock,
        )

    async def test_couper_une_recherche_arrete_son_alerte_et_sa_collecte(self) -> None:
        env = Env()
        old = await env.apply()
        await env.apply(COOK, keys=("cuisinier|paris",))

        listed = await self.toggle(env).execute(USER, old.search.id, enabled=False)

        assert listed.search.alerts_enabled is False
        assert listed.is_current is False
        assert env.index.points[old.search.id][2] is False
        assert env.demands() == {"cuisinier|paris"}
        # La recherche en cours n'a pas bougé.
        assert (await env.criteria.find(USER)).alerts_enabled is True
        assert env.index.points[USER][2] is True

    async def test_la_reactiver_la_remet_en_surveillance(self) -> None:
        env = Env()
        old = await env.apply()
        await env.apply(COOK, keys=("cuisinier|paris",))
        await self.toggle(env).execute(USER, old.search.id, enabled=False)

        listed = await self.toggle(env).execute(USER, old.search.id, enabled=True)

        assert listed.search.alerts_enabled is True
        assert env.index.points[old.search.id][2] is True
        assert env.demands() == {"serveur|paris", "cuisinier|paris"}

    async def test_couper_la_recherche_en_cours_coupe_aussi_ses_alertes_du_fil(self) -> None:
        env = Env()
        current = await env.apply()

        listed = await self.toggle(env).execute(USER, current.search.id, enabled=False)

        assert listed.is_current is True
        assert (await env.criteria.find(USER)).alerts_enabled is False
        assert env.index.points[USER][2] is False
        assert env.demands() == set()

    async def test_la_bascule_du_fil_et_celle_des_parametres_disent_la_meme_chose(self) -> None:
        env = Env()
        current = await env.apply()
        legacy = SetAlertsEnabled(
            criteria=env.criteria, criteria_index=env.index, clock=env.clock, saved=env.registry
        )

        await legacy.execute(USER, enabled=False)

        assert env.saved.items[current.search.id].alerts_enabled is False

    async def test_la_recherche_dun_autre_est_introuvable(self) -> None:
        env = Env()
        mine = await env.apply()
        stranger = uuid4()

        with pytest.raises(SavedSearchNotFoundError):
            await self.toggle(env).execute(stranger, mine.search.id, enabled=False)
        with pytest.raises(SavedSearchNotFoundError):
            await self.delete(env).execute(stranger, mine.search.id)
        with pytest.raises(SavedSearchNotFoundError):
            await self.toggle(env).execute(USER, uuid4(), enabled=False)
        assert env.saved.items[mine.search.id].alerts_enabled is True

    async def test_supprimer_une_ancienne_recherche_ne_touche_pas_au_fil(self) -> None:
        env = Env()
        old = await env.apply()
        await env.apply(COOK, keys=("cuisinier|paris",))

        await self.delete(env).execute(USER, old.search.id)

        assert old.search.id not in env.saved.items
        assert old.search.id not in env.index.points
        assert await env.criteria.find(USER) is not None
        assert env.demands() == {"cuisinier|paris"}

    async def test_supprimer_la_recherche_en_cours_rend_le_fil_general(self) -> None:
        env = Env()
        current = await env.apply()

        await self.delete(env).execute(USER, current.search.id)

        assert await env.criteria.find(USER) is None
        assert USER not in env.index.points
        assert not env.saved.items

    async def test_oublier_la_recherche_en_cours_la_garde_alerte_coupee(self) -> None:
        env = Env()
        current = await env.apply()

        await env.clear.execute(USER)

        kept = env.saved.items[current.search.id]
        assert kept.alerts_enabled is False  # retrouvable et réactivable dans les paramètres
        assert await env.criteria.find(USER) is None

    async def test_une_recherche_sans_point_dindex_se_bascule_quand_meme(self) -> None:
        env = Env()
        legacy = make_saved(USER)  # reprise d'avant ADR-0041 : pas encore de point
        await env.saved.save(legacy)

        listed = await self.toggle(env).execute(USER, legacy.id, enabled=False)

        assert listed.search.alerts_enabled is False

    async def test_a_la_suppression_du_compte_tout_part(self) -> None:
        env = Env()
        first = await env.apply()
        second = await env.apply(COOK)

        await env.registry.forget_user(USER)

        assert not env.saved.items
        assert first.search.id not in env.index.points
        assert second.search.id not in env.index.points


class TestAlertes:
    async def env_with_old_search(self) -> tuple[Env, SavedSearch]:
        """« Cuisinier » enregistrée, puis « serveur » devenue la recherche en cours."""
        env = Env()
        old = await env.apply(COOK, keys=("cuisinier|paris",))
        await env.apply()
        return env, env.saved.items[old.search.id]

    def match(self, env: Env, *points: UUID) -> None:
        env.index.matches = [
            CriteriaMatch(user_id=p, similarity=ALERT_MIN_SIMILARITY + 0.1) for p in points
        ]

    async def test_une_ancienne_recherche_previent_encore(self) -> None:
        env, old = await self.env_with_old_search()
        self.match(env, old.id)
        offer = cook_offer()

        summary = await env.run(offer)

        assert summary.notifications_sent == 1
        sent = env.notifier.sent[0]
        assert sent["user_id"] == USER
        assert sent["message"].kind is PushKind.OFFER
        assert sent["message"].target_id == offer.id
        assert "cuisinier" in sent["message"].body.lower()
        # Ses offres n'entrent pas dans le fil : il est celui de la recherche en cours.
        assert not await env.feed.offer_ids_of(USER)
        assert env.saved.items[old.id].notified_offer_ids == (offer.id,)
        assert env.saved.items[old.id].alerts_sent_today == 1

    async def test_plusieurs_offres_une_seule_notification_qui_ouvre_la_premiere(self) -> None:
        env, old = await self.env_with_old_search()
        self.match(env, old.id)
        offers = [
            cook_offer(company=Company(name=name), title=title)
            for name, title in (
                ("Le Bouillon", "Cuisinier H/F"),
                ("Chez Marcel", "Cuisinier de collectivité"),
                ("La Table Ronde", "Cuisinier traiteur"),
            )
        ]

        summary = await env.run(*offers)

        assert summary.notifications_sent == 1
        message = env.notifier.sent[0]["message"]
        assert message.kind is PushKind.OFFER
        # Le classement décide lesquelles méritent une notification (porte d'intitulé) ;
        # ce qui compte ici : plusieurs offres, un seul envoi, qui ouvre la première.
        assert message.count is not None
        assert message.count >= 2
        assert message.title == f"{message.count} nouvelles offres pour vous"
        assert message.target_id in {o.id for o in offers}

    async def test_une_recherche_coupee_ne_previent_plus(self) -> None:
        env, old = await self.env_with_old_search()
        await env.registry.set_enabled(USER, old.fingerprint, enabled=False, now=NOW)
        self.match(env, old.id)

        summary = await env.run(cook_offer())

        assert summary.notifications_sent == 0
        assert env.notifier.sent == []

    async def test_jamais_deux_fois_la_meme_offre(self) -> None:
        env, old = await self.env_with_old_search()
        self.match(env, old.id)
        offer = cook_offer()
        await env.run(offer)
        env.clock.advance(ALERT_MIN_INTERVAL + timedelta(minutes=1))

        again = await env.run(offer)

        assert again.notifications_sent == 0
        assert len(env.notifier.sent) == 1

    async def test_une_offre_ecartee_ou_hors_sujet_ne_previent_pas(self) -> None:
        env, old = await self.env_with_old_search()
        self.match(env, old.id)
        dismissed = cook_offer()
        await env.states.save(
            OfferState(user_id=USER, offer_id=dismissed.id, dismissed=True, updated_at=NOW)
        )
        elsewhere = make_offer(title="Comptable H/F", description="Tenue des comptes.")

        summary = await env.run(dismissed, elsewhere)

        assert summary.notifications_sent == 0

    async def test_le_rythme_vaut_toutes_recherches_confondues(self) -> None:
        env, old = await self.env_with_old_search()
        self.match(env, old.id)
        await env.run(cook_offer())

        # Moins de deux heures après : rien, même pour une autre offre.
        env.clock.advance(timedelta(minutes=30))
        soon = await env.run(cook_offer())
        assert soon.notifications_sent == 0

        # Le plafond du jour, déjà atteint par la recherche en cours, vaut aussi.
        env.clock.advance(ALERT_MIN_INTERVAL)
        current = await env.criteria.find(USER)
        await env.criteria.save(
            dataclasses.replace(
                current, alerts_sent_on=env.clock.now(), alerts_sent_today=ALERT_DAILY_MAX
            )
        )
        capped = await env.run(cook_offer())
        assert capped.notifications_sent == 0

    async def test_la_recherche_en_cours_garde_son_chemin_sans_doublon(self) -> None:
        env = Env()
        current = await env.apply()
        # Son point de recherche en cours ET son point de recherche enregistrée répondent.
        self.match(env, USER, current.search.id)
        offer = make_offer()

        summary = await env.run(offer)

        assert summary.notifications_sent == 1
        assert len(env.notifier.sent) == 1
        assert env.notifier.sent[0]["message"].kind is PushKind.OFFER
        assert list(await env.feed.offer_ids_of(USER)) == [offer.id]  # elle, entre dans le fil

    async def test_deux_recherches_dune_personne_une_seule_notification(self) -> None:
        env, old = await self.env_with_old_search()
        baker = make_criteria(query="boulanger à Paris", job_titles=["boulanger"])
        other = await env.apply(baker)
        await env.apply()  # « serveur » redevient la recherche en cours
        self.match(env, old.id, other.search.id)
        bread = make_offer(title="Boulanger H/F", description="Pétrissage, cuisson du pain.")

        summary = await env.run(cook_offer(), bread)

        assert summary.notifications_sent == 1
        assert len(env.notifier.sent) == 1
        counted = [env.saved.items[i].alerts_sent_today for i in (old.id, other.search.id)]
        assert sorted(counted) == [0, 1]  # un seul envoi compté pour la personne

    async def test_sans_registre_branche_rien_ne_change(self) -> None:
        env, old = await self.env_with_old_search()
        self.match(env, old.id)
        legacy = DispatchAlerts(
            criteria=env.criteria,
            criteria_index=env.index,
            feed=env.feed,
            states=env.states,
            notifier=env.notifier,
            clock=env.clock,
            uow=RecordingUnitOfWork(),
        )

        summary = await legacy.execute(
            [
                IndexedOffer(offer=o, dense=VECTOR, title_text=o.title, body_text=o.title)
                for o in (cook_offer(),)
            ]
        )

        assert summary.notifications_sent == 0
