"""La validation stricte du contrat `devices`, et la lecture du pays."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from karria.modules.devices.api.dependencies import COUNTRY_HEADER, get_request_country
from karria.modules.devices.api.schemas import DeviceRegistrationRequest

pytestmark = pytest.mark.unit

VALID = {
    "platform": "ios",
    "push_token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
    "push_permission": "granted",
    "os_name": "iOS",
    "os_version": "18.1",
    "is_physical_device": True,
    "app_version": "2.0.0",
}


class TestDeviceRegistrationRequest:
    def test_accepte_une_declaration_minimale(self) -> None:
        info = DeviceRegistrationRequest(**VALID).to_info()
        assert info.push_token == VALID["push_token"]
        assert info.device_brand is None

    def test_une_chaine_vide_vaut_absence_de_jeton(self) -> None:
        body = DeviceRegistrationRequest(**{**VALID, "push_token": ""})
        assert body.push_token is None

    @pytest.mark.parametrize(
        ("champ", "valeur"),
        [
            ("push_token", "fcm:abc"),
            ("platform", "windows"),
            ("push_permission", "maybe"),
            ("os_name", ""),
            ("app_version", "x" * 101),
            ("device_year_class", 1900),
        ],
    )
    def test_refuse_ce_qui_sort_du_contrat(self, champ: str, valeur: object) -> None:
        with pytest.raises(ValidationError):
            DeviceRegistrationRequest(**{**VALID, champ: valeur})


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


class TestRequestCountry:
    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            ("FR", "FR"),
            ("fr", "FR"),
            ("XX", None),
            ("T1", None),
            ("FRA", None),
            ("", None),
            ("1A", None),
        ],
    )
    def test_lit_len_tete_du_mandataire(self, valeur: str, attendu: str | None) -> None:
        assert get_request_country(_request({COUNTRY_HEADER: valeur})) == attendu

    def test_sans_en_tete_rien(self) -> None:
        assert get_request_country(_request({})) is None
