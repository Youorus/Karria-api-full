"""Le rejeu d'un merci : clé d'idempotence, adresse masquée, rien ne part sans `--send`."""

from __future__ import annotations

from uuid import UUID

import pytest
from scripts.payments_thanks import LEDGER_EVENT_TYPE, _parse, ledger_event_id, mask_email

pytestmark = pytest.mark.unit

PURCHASE = UUID("0b6c0f3e-58a4-4c7e-9a43-1f6f1c3b7d2a")


def test_la_cle_du_registre_designe_l_achat_et_ne_ressemble_pas_a_stripe():
    assert ledger_event_id(PURCHASE) == f"thanks:{PURCHASE}"
    assert not ledger_event_id(PURCHASE).startswith("evt_")
    assert LEDGER_EVENT_TYPE == "manual.thanks"


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("mtakoumba@gmail.com", "m***@gmail.com"),
        ("a@b.fr", "a***@b.fr"),
        (None, "(aucune adresse)"),
        ("sans-arobase", "(aucune adresse)"),
    ],
)
def test_l_adresse_est_masquee(email: str | None, masked: str):
    assert mask_email(email) == masked


def test_sans_send_le_script_simule():
    args = _parse([str(PURCHASE)])
    assert (args.purchase_id, args.send) == (PURCHASE, False)
    assert _parse([str(PURCHASE), "--send"]).send is True


def test_un_identifiant_qui_n_est_pas_un_uuid_est_refuse():
    with pytest.raises(SystemExit):
        _parse(["cs_live_abc"])
