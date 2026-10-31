"""Les raisons d'une entrée du fil, lues par la requête SQL brute du fil."""

from __future__ import annotations

import pytest

from karria.modules.offers.infrastructure.repositories import _reasons_from_raw


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ('["Villeneuve-la-Garenne"]', ("Villeneuve-la-Garenne",)),
        (b'["CDI", "Paris"]', ("CDI", "Paris")),
        (["Osny"], ("Osny",)),
        (None, ()),
        ("", ()),
        ('{"pas": "une liste"}', ()),
    ],
)
def test_les_raisons_sont_decodees_et_jamais_eclatees_en_caracteres(
    brut: object, attendu: tuple[str, ...]
) -> None:
    assert _reasons_from_raw(brut) == attendu
