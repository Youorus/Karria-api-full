"""Le nom français d'un pays d'après son code — la table de `shared/countries.py`."""

from __future__ import annotations

import re

import pytest

from karria.shared.countries import COUNTRY_NAMES_FR, country_name_fr

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("FR", "France"),
        ("BE", "Belgique"),
        ("CH", "Suisse"),
        ("CA", "Canada"),
        ("CI", "Côte d'Ivoire"),
        ("CM", "Cameroun"),
        ("SN", "Sénégal"),
        ("MA", "Maroc"),
        ("US", "États-Unis"),
        ("GB", "Royaume-Uni"),
        ("RE", "La Réunion"),
        ("XK", "Kosovo"),
    ],
)
def test_un_code_connu_donne_son_nom(code: str, name: str) -> None:
    assert country_name_fr(code) == name


@pytest.mark.parametrize("code", [None, "", "QQ", "ZZ", "FRA", "419", "UK", "SU"])
def test_un_code_inconnu_absent_ou_retire_ne_donne_rien(code: str | None) -> None:
    assert country_name_fr(code) is None


def test_la_table_ne_contient_que_des_codes_a_deux_lettres_et_des_noms_lisibles() -> None:
    assert len(COUNTRY_NAMES_FR) > 240
    for code, name in COUNTRY_NAMES_FR.items():
        assert re.fullmatch(r"[A-Z]{2}", code), code
        assert name.strip() == name, code
        assert name, code
