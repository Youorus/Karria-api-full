"""La campagne « Karria est de retour » : qui est éligible, et le registre qui empêche le doublon."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.push_campaign import (
    CAMPAIGN_BODY,
    CAMPAIGN_ID,
    CAMPAIGN_TITLE,
    Ledger,
    eligible,
    notification,
)

from karria.shared.push import MAX_BODY_LENGTH, MAX_TITLE_LENGTH, PushKind

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def test_le_message_tient_dans_les_bornes_du_domaine_et_tutoie():
    message = notification()
    assert message.title == CAMPAIGN_TITLE
    assert message.body == CAMPAIGN_BODY
    assert len(message.title) <= MAX_TITLE_LENGTH
    assert len(message.body) <= MAX_BODY_LENGTH
    assert "Ton " in message.body
    assert message.kind is PushKind.OFFERS_FEED
    assert message.data == {
        "v": "1",
        "type": "offers_feed",
        "url": "karria://offres",
        "campaign": CAMPAIGN_ID,
    }


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"email_verified": True, "notifications_enabled": True}, True),
        ({"email_verified": False, "notifications_enabled": True}, False),
        ({"email_verified": True, "notifications_enabled": False}, False),
        ({}, False),
    ],
)
def test_seuls_les_comptes_verifies_qui_voulaient_des_notifications_sont_eligibles(
    record: dict[str, bool], expected: bool
):
    assert eligible(record) is expected


def test_un_registre_absent_est_vide(tmp_path: Path):
    ledger = Ledger.load(tmp_path / "absent.json")
    assert ledger.notified == {}
    assert not ledger.contains("x")


def test_un_registre_malforme_est_lu_comme_vide(tmp_path: Path):
    path = tmp_path / "ledger.json"
    path.write_text("[1, 2]", "utf-8")
    assert Ledger.load(path).notified == {}


def test_un_envoi_note_est_ecrit_aussitot_et_relu(tmp_path: Path):
    path = tmp_path / "ledger.json"
    ledger = Ledger.load(path)
    ledger.add("user-1", at=NOW)
    assert ledger.contains("user-1")
    written = json.loads(path.read_text("utf-8"))
    assert written == {"campaign": CAMPAIGN_ID, "notified": {"user-1": NOW.isoformat()}}
    reloaded = Ledger.load(path)
    assert reloaded.contains("user-1")
    assert not reloaded.contains("user-2")
