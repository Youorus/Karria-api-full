"""Les entreprises dans PostgreSQL : lot, liens par `unnest`, prise sous bail, copie du logo."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from uuid import uuid4

import pytest
from test_offers_repositories import NOW, make_offer

from karria.modules.offers.application import OfferLink
from karria.modules.offers.domain.companies import CompanyRecord, LogoOrigin, LogoStatus
from karria.modules.offers.infrastructure import TortoiseCompanyRepository, TortoiseOfferRepository

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]


def company(**changes: object) -> CompanyRecord:
    base = CompanyRecord(id=uuid4(), key="bistrot", name="Le Bistrot", logo_next_attempt_at=NOW)
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


async def test_cycle_complet() -> None:
    offers, companies = TortoiseOfferRepository(), TortoiseCompanyRepository()
    active, inactive = make_offer(), make_offer(is_active=False)
    await offers.insert_many([active, inactive])
    bistrot = company(source_logo_url="https://logo", logo_rejected_urls=("https://x",))
    idle = company(key="autre", domain="autre.fr", logo_next_attempt_at=NOW + timedelta(days=1))
    await companies.save_many([bistrot, idle])
    await companies.save_many([dataclasses.replace(bistrot, domain="bistrot.fr")])

    found = await companies.find_by_keys(["bistrot", "absent"])
    assert [r.domain for r in found["bistrot"]] == ["bistrot.fr"]
    assert found["bistrot"][0].logo_rejected_urls == ("https://x",)
    assert await companies.find_by_keys([]) == {}

    unlinked = await companies.unlinked_offers(after=None, limit=10, active_only=True)
    assert [o.id for o in unlinked] == [active.id]
    everything = await companies.unlinked_offers(after=None, limit=10, active_only=False)
    assert len(everything) == 2
    assert (
        await companies.unlinked_offers(
            after=max(o.id for o in everything), limit=10, active_only=False
        )
        == []
    )

    links = [OfferLink(o.source.value, o.source_id, bistrot.id) for o in (active, inactive)]
    assert await companies.link_offers(links) == 2
    assert await companies.link_offers(links) == 0
    assert await companies.link_offers([]) == 0

    claimed = await companies.claim_logo_work(now=NOW, until=NOW + timedelta(minutes=15), limit=5)
    assert [r.id for r in claimed] == [bistrot.id]
    assert await companies.claim_logo_work(now=NOW, until=NOW, limit=5) == []

    ready = dataclasses.replace(
        claimed[0], logo_hash="ab" * 16, logo_status=LogoStatus.READY, logo_origin=LogoOrigin.SOURCE
    )
    assert await companies.save_logo(ready) == 2
    stored = await offers.find(active.id)
    assert stored is not None
    assert stored.company is not None
    assert stored.company.logo_hash == "ab" * 16
    assert await companies.save_logo(dataclasses.replace(idle, logo_status=LogoStatus.MISSING)) == 0

    # Une offre arrivée après le logo le reçoit en même temps que son lien.
    late = make_offer()
    await offers.insert_many([late])
    await companies.link_offers([OfferLink(late.source.value, late.source_id, bistrot.id)])
    again = await offers.find(late.id)
    assert again is not None
    assert again.company is not None
    assert again.company.logo_hash == "ab" * 16
    await companies.save_many([])
