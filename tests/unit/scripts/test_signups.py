"""La répartition des inscrits par pays et par mois : ce que `scripts/signups.py` affiche."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.signups import _parse, label, report, since

pytestmark = pytest.mark.unit

ROWS: list[tuple[str, str | None, int]] = [
    ("2026-10", "FR", 6),
    ("2026-10", None, 4),
    ("2026-11", "FR", 30),
    ("2026-11", "BE", 5),
    ("2026-11", "QQ", 1),
    ("2026-11", None, 14),
]


def test_un_pays_se_lit_par_son_nom_et_son_code() -> None:
    assert label("BE") == "Belgique (BE)"
    assert label(None) == "pays inconnu"
    assert label("QQ") == "pays inconnu (QQ)"


def test_le_tableau_donne_la_periode_puis_chaque_mois_du_plus_recent_au_plus_ancien() -> None:
    lines = report(ROWS).splitlines()

    assert lines[0] == "Toute la période — 60 inscrits"
    assert lines[1].split() == ["France", "(FR)", "36", "60.0", "%"]
    # « pays inconnu » ferme toujours la liste, même s'il est nombreux.
    assert lines[4].split() == ["pays", "inconnu", "18", "30.0", "%"]
    headings = [line for line in lines if line and not line.startswith(" ")]
    assert headings == [
        "Toute la période — 60 inscrits",
        "2026-11 — 50 inscrits",
        "2026-10 — 10 inscrits",
    ]


def test_les_parts_d_un_mois_se_calculent_sur_ce_mois() -> None:
    october = report(since(ROWS, "2026-10")).split("2026-10 — 10 inscrits\n")[1].splitlines()

    assert october[0].split() == ["France", "(FR)", "6", "60.0", "%"]
    assert october[1].split() == ["pays", "inconnu", "4", "40.0", "%"]


def test_since_ecarte_les_mois_anterieurs() -> None:
    assert [row[0] for row in since(ROWS, "2026-11")] == ["2026-11"] * 4
    assert since(ROWS, None) == ROWS
    assert report(since(ROWS, "2027-01")) == "Aucun inscrit sur la période."


def test_un_mois_mal_ecrit_est_refuse() -> None:
    assert _parse(["--since", "2026-11"]).since == "2026-11"
    assert _parse([]).since is None
    for wrong in ("2026", "2026-13", "11-2026", "2026-11-20"):
        with pytest.raises(SystemExit):
            _parse(["--since", wrong])


def test_le_script_ne_fait_que_lire() -> None:
    """Lecture seule : aucune écriture, aucun effacement dans la commande."""
    source = (Path(__file__).parents[3] / "scripts" / "signups.py").read_text(encoding="utf-8")

    for forbidden in (".update(", ".delete(", ".create(", "INSERT", "UPDATE ", "DELETE "):
        assert forbidden not in source
