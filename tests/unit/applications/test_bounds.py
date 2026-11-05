"""Les bornes par chemin : rien ne s'écrit dans le CV hors de la forme attendue."""

from __future__ import annotations

import pytest

from karria.modules.applications.domain import BoundsError, check_bounds

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("identity.headline", "Conseillère de vente"),
        ("summary", "Trois phrases.\nSur deux lignes."),
        ("experiences.0.position", "Vendeuse"),
        ("experiences.2.missions.4", "Tenir la caisse"),
        ("skills", [{"name": "Caisse"}]),
        ("experiences", []),
    ],
)
def test_une_valeur_dans_les_bornes_passe(path: str, value: object) -> None:
    check_bounds(path, value)


@pytest.mark.parametrize(
    ("path", "value", "why"),
    [
        ("identity.headline", "x" * 151, "too_long"),
        ("identity.headline", "Deux\nlignes", "one_line_expected"),
        ("identity.headline", "", "text_expected"),
        ("identity.headline", ["liste"], "text_expected"),
        ("summary", "x" * 1501, "too_long"),
        ("experiences.0.position", "a\rb", "one_line_expected"),
        ("experiences.0.missions.0", "x" * 301, "too_long"),
        ("skills", "pas une liste", "list_expected"),
        ("skills", ["chaîne"], "list_expected"),
        ("identity.first_name", "Awa", "unknown_path"),
        ("contact.email", "x@y.z", "unknown_path"),
    ],
)
def test_une_valeur_hors_bornes_est_refusee(path: str, value: object, why: str) -> None:
    with pytest.raises(BoundsError) as excinfo:
        check_bounds(path, value)

    assert excinfo.value.why == why
    assert excinfo.value.path == path
