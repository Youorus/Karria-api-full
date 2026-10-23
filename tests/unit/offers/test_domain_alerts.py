"""Les alertes : rythme, fraîcheur, message groupé."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from karria.modules.offers.domain import (
    ALERT_DAILY_MAX,
    ALERT_MAX_OFFER_AGE,
    ALERT_MIN_INTERVAL,
    Company,
    Offer,
    Salary,
    Source,
    build_notification,
    is_fresh,
    may_notify,
    uuid7,
)
from karria.modules.offers.domain.alerts import ALERT_BODY_OFFERS

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def make_offer(
    title: str = "Serveur H/F", company: str | None = "Le Bistrot", **changes: object
) -> Offer:
    offer = Offer(
        id=uuid7(),
        source=Source.INDEED,
        source_id="in-1",
        title=title,
        company=Company(name=company) if company else None,
        description="",
        location=None,
        contract_type=None,
        remote=None,
        schedule=None,
        experience=None,
        salary=Salary(min_monthly=None, max_monthly=None, raw=None),
        published_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        apply_url=None,
        origin_url=None,
    )
    return dataclasses.replace(offer, **changes)  # type: ignore[arg-type]


class TestRythme:
    def test_premiere_alerte_permise(self) -> None:
        assert may_notify(now=NOW, last_alert_at=None, sent_today=0)

    def test_trop_tot_apres_la_precedente(self) -> None:
        assert not may_notify(now=NOW, last_alert_at=NOW - ALERT_MIN_INTERVAL / 2, sent_today=1)

    def test_apres_lintervalle_cest_permis(self) -> None:
        assert may_notify(now=NOW, last_alert_at=NOW - ALERT_MIN_INTERVAL, sent_today=1)

    def test_le_plafond_journalier_bloque(self) -> None:
        assert not may_notify(now=NOW, last_alert_at=None, sent_today=ALERT_DAILY_MAX)


class TestFraicheur:
    def test_une_offre_recente_est_fraiche(self) -> None:
        assert is_fresh(make_offer(published_at=NOW - ALERT_MAX_OFFER_AGE), now=NOW)

    def test_une_offre_ancienne_ne_lest_plus(self) -> None:
        old = make_offer(published_at=NOW - ALERT_MAX_OFFER_AGE * 2)
        assert not is_fresh(old, now=NOW)


class TestMessage:
    def test_une_seule_offre(self) -> None:
        offer = make_offer()
        note = build_notification("Serveur · Paris", [offer])
        assert note.title == "Une nouvelle offre pour vous"
        assert note.body == "Serveur · Paris — Serveur H/F chez Le Bistrot"
        assert note.data == {"type": "NEW_OFFERS", "offer_id": str(offer.id), "count": "1"}

    def test_plusieurs_offres_sont_groupees_et_le_reste_compte(self) -> None:
        offers = [make_offer(f"Poste {i}", company=None) for i in range(ALERT_BODY_OFFERS + 2)]
        note = build_notification("", offers)
        assert note.title == f"{len(offers)} nouvelles offres pour vous"
        assert note.body.endswith("… et 2 autres")
        assert not note.body.startswith(" —")

    def test_une_seule_offre_en_plus_est_au_singulier(self) -> None:
        offers = [make_offer() for _ in range(ALERT_BODY_OFFERS + 1)]
        assert build_notification("x", offers).body.endswith("… et 1 autre")

    def test_le_corps_est_borne(self) -> None:
        offers = [make_offer("T" * 200) for _ in range(3)]
        assert len(build_notification("résumé", offers).body) <= 240
