"""Le journal d'apprentissage : instantané des traits et gestes révélés par un changement d'état."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from karria.modules.offers.domain import (
    ContractType,
    FeedMode,
    InteractionKind,
    Location,
    OfferState,
    Salary,
    Schedule,
    snapshot,
    transitions,
)
from offers.fakes import NOW, make_criteria, make_entry, make_offer

pytestmark = pytest.mark.unit

USER = uuid4()


class TestInstantane:
    def test_sans_recherche_seuls_les_traits_de_l_offre_sont_connus(self) -> None:
        offer = make_offer(published_at=NOW - timedelta(hours=36))

        shot = snapshot(offer, None, None, now=NOW)

        assert shot.feed_mode is FeedMode.ALL_OFFERS
        assert shot.criteria_fingerprint is None
        assert shot.score is None
        assert shot.quality is None
        assert shot.origin is None
        assert shot.source == "indeed"
        assert shot.age_hours == 36.0
        assert shot.distance_km is None
        assert shot.title_match is None
        assert shot.contract_match is None
        assert shot.schedule_match is None
        assert shot.has_salary is False

    def test_avec_une_recherche_l_accord_se_mesure(self) -> None:
        criteria = make_criteria(
            job_titles=["serveur", "chef de rang"],
            contract_types=[ContractType.CDI],
            schedule=Schedule.FULL_TIME,
        )
        offer = make_offer(
            title="Chef de rang H/F",
            salary=Salary(min_monthly=1_900, max_monthly=None, raw="1 900 €"),
            location=Location(label="Vincennes", latitude=48.847, longitude=2.439),
        )
        entry = make_entry(USER, offer.id, score=0.8)

        shot = snapshot(offer, entry, criteria, now=NOW)

        assert shot.feed_mode is FeedMode.SEARCH
        assert shot.criteria_fingerprint == criteria.fingerprint()
        assert shot.score == 0.8
        assert shot.quality == entry.quality.value
        assert shot.origin == "search"
        assert shot.title_match == 1  # un synonyme, pas l'intitulé dit
        assert shot.contract_match is True
        assert shot.schedule_match is False  # l'offre ne dit pas son temps
        assert shot.has_salary is True
        assert shot.distance_km is not None
        assert 5 < shot.distance_km < 9

    def test_une_offre_sans_coordonnees_n_a_pas_de_distance(self) -> None:
        offer = make_offer(location=Location(label="Quelque part"))
        shot = snapshot(offer, None, make_criteria(), now=NOW)
        assert shot.distance_km is None
        assert shot.title_match == 0


class TestTransitions:
    def test_chaque_bascule_est_un_geste(self) -> None:
        blank = OfferState(user_id=USER, offer_id=uuid4())
        full = blank.with_changes(
            at=NOW, opened=True, link_clicked=True, saved=True, applied=True, dismissed=True
        )

        assert transitions(blank, full) == [
            InteractionKind.OPEN,
            InteractionKind.LINK_CLICK,
            InteractionKind.SAVE,
            InteractionKind.APPLY,
            InteractionKind.DISMISS,
        ]
        assert transitions(
            full, full.with_changes(at=NOW, saved=False, applied=False, dismissed=False)
        ) == [
            InteractionKind.UNSAVE,
            InteractionKind.UNAPPLY,
            InteractionKind.UNDISMISS,
        ]
        assert transitions(full, full) == []
