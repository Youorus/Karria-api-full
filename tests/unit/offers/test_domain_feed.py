"""L'état d'une personne vis-à-vis d'une offre, et ce qu'un recalcul retire du fil."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from karria.modules.offers.domain import (
    FeedEntry,
    FeedOrigin,
    MatchQuality,
    OfferState,
    entries_to_remove,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
USER = uuid4()


def blank() -> OfferState:
    return OfferState(user_id=USER, offer_id=uuid4())


class TestEtat:
    def test_un_etat_neuf_est_vierge_et_non_protege(self) -> None:
        state = blank()
        assert state.is_blank
        assert not state.protected

    def test_la_vue_pose_son_horodatage_une_fois(self) -> None:
        state = blank().with_changes(at=NOW, seen=True).with_changes(at=LATER, seen=True)
        assert state.seen
        assert state.seen_at == NOW
        assert state.updated_at == LATER

    def test_la_vue_ne_redescend_pas(self) -> None:
        state = blank().with_changes(at=NOW, seen=True).with_changes(at=LATER, seen=False)
        assert state.seen

    def test_une_vue_a_faux_ne_pose_rien(self) -> None:
        state = blank().with_changes(at=NOW, seen=False)
        assert state.seen_at is None
        assert state.is_blank

    def test_louverture_vaut_vue_et_protege(self) -> None:
        state = blank().with_changes(at=NOW, opened=True)
        assert state.opened
        assert state.seen
        assert state.opened_at == NOW == state.seen_at
        assert state.protected
        assert not state.is_blank

    def test_le_clic_se_retient(self) -> None:
        state = blank().with_changes(at=NOW, link_clicked=True)
        assert state.link_clicked
        assert not state.is_blank

    def test_la_sauvegarde_se_pose_et_se_retire(self) -> None:
        saved = blank().with_changes(at=NOW, saved=True)
        assert saved.saved
        assert saved.saved_at == NOW
        assert saved.protected
        unsaved = saved.with_changes(at=LATER, saved=False)
        assert not unsaved.saved
        assert unsaved.saved_at is None

    def test_la_candidature_protege(self) -> None:
        state = blank().with_changes(at=NOW, applied=True)
        assert state.applied_at == NOW
        assert state.protected
        assert state.with_changes(at=LATER, applied=False).applied_at is None

    def test_lecart_se_pose_et_se_retire(self) -> None:
        dismissed = blank().with_changes(at=NOW, dismissed=True)
        assert dismissed.dismissed
        assert dismissed.dismissed_at == NOW
        assert not dismissed.with_changes(at=LATER, dismissed=False).dismissed


class TestRecalcul:
    def _entry(self) -> FeedEntry:
        return FeedEntry(
            user_id=USER,
            offer_id=uuid4(),
            score=0.5,
            quality=MatchQuality.MEDIUM,
            reasons=(),
            origin=FeedOrigin.SEARCH,
            created_at=NOW,
        )

    def test_ce_qui_nest_plus_retenu_part_sauf_si_pris_en_main(self) -> None:
        kept, dropped, saved, seen_only = (self._entry() for _ in range(4))
        states = {
            saved.offer_id: OfferState(user_id=USER, offer_id=saved.offer_id, saved=True),
            seen_only.offer_id: OfferState(user_id=USER, offer_id=seen_only.offer_id, seen=True),
        }
        removed = entries_to_remove([kept, dropped, saved, seen_only], {kept.offer_id}, states)
        assert removed == [dropped.offer_id, seen_only.offer_id]
