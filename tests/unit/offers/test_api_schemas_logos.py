"""Le logo servi à l'app : notre adresse stable, jamais celle de la source."""

from __future__ import annotations

import pytest

from karria.modules.offers.api.schemas import CompanyResponse, JobOfferResponse
from karria.modules.offers.domain import Company
from offers.fakes import make_offer

pytestmark = pytest.mark.unit


def url(digest: str, size: int) -> str:
    return f"https://storage.karria.fr/karria-production-logos/{digest}/{size}.webp"


def test_le_logo_en_stock_est_servi_en_deux_tailles() -> None:
    company = Company(name="Decathlon", logo_url="https://source/logo.png", logo_hash="ab")
    response = CompanyResponse.from_domain(company, url)
    assert response.logo == url("ab", 128)
    assert response.logo_large == url("ab", 256)


@pytest.mark.parametrize(
    ("company", "builder"),
    [
        (Company(name="X", logo_url="https://source/logo.png"), url),
        (Company(name="X", logo_hash="ab"), None),
    ],
)
def test_sans_logo_en_stock_rien_n_est_servi(company: Company, builder: object) -> None:
    response = CompanyResponse.from_domain(company, builder)  # type: ignore[arg-type]
    assert (response.logo, response.logo_large) == (None, None)


def test_l_offre_porte_le_logo_de_son_entreprise() -> None:
    offer = make_offer(company=Company(name="Decathlon", logo_hash="cd"))
    response = JobOfferResponse.from_domain(offer, logo_url=url)
    assert response.company is not None
    assert response.company.logo == url("cd", 128)
