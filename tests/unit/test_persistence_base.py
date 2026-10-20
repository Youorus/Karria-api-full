"""Le modèle de persistance de base doit rester valide et abstrait."""

from __future__ import annotations

import pytest

from karria.shared.database import BaseDBModel

pytestmark = pytest.mark.unit


def test_le_modele_de_base_est_abstrait() -> None:
    """Rien ne doit être stocké dans une table `basedbmodel`.

    Ce test paraît trivial ; il vaut surtout pour ce qu'il exerce au moment
    de l'import : la déclaration des champs Tortoise. Une signature de champ
    devenue invalide après une montée de version échoue ici, à l'import,
    plutôt qu'au premier accès en production.
    """
    assert BaseDBModel._meta.abstract


def test_le_modele_de_base_expose_lidentite_et_laudit() -> None:
    champs = set(BaseDBModel._meta.fields)

    assert {"id", "created_at", "updated_at"} <= champs
