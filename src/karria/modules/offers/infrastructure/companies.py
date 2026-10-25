"""Les entreprises dans PostgreSQL : implémente `CompanyRepository`.

Deux écritures en SQL direct, parce qu'elles portent sur des milliers de
lignes : le rattachement des offres d'un lot (une seule mise à jour par
`unnest`) et la prise de travail des logos (`FOR UPDATE SKIP LOCKED`, pour
qu'un worker et une commande de rattrapage ne prennent jamais la même
entreprise).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from tortoise import connections

from karria.modules.offers.application import OfferLink
from karria.modules.offers.domain import Offer
from karria.modules.offers.domain.companies import CompanyRecord, LogoOrigin, LogoStatus
from karria.modules.offers.infrastructure.models import CompanyModel, OfferModel
from karria.modules.offers.infrastructure.repositories import _to_offer

_COLUMNS = (
    "key",
    "domain",
    "name",
    "website",
    "source_logo_url",
    "v1_id",
    "v1_logo_url",
    "logo_hash",
    "logo_origin",
    "logo_status",
    "logo_attempts",
    "logo_next_attempt_at",
    "logo_checked_at",
    "logo_rejected_urls",
)

_LINK_SQL = """
UPDATE offers_offers AS o
   SET company_id = v.company_id, company_logo_hash = c.logo_hash
  FROM unnest($1::text[], $2::text[], $3::uuid[]) AS v(source, source_id, company_id)
  JOIN offers_companies AS c ON c.id = v.company_id
 WHERE o.source = v.source AND o.source_id = v.source_id
   AND (o.company_id IS DISTINCT FROM v.company_id
        OR o.company_logo_hash IS DISTINCT FROM c.logo_hash)
"""

# Les entreprises qui ont des offres actives passent d'abord : c'est ce que
# l'app affiche aujourd'hui. Le bail (`logo_next_attempt_at = until`) rend
# la prise sûre même si le worker meurt en route.
_CLAIM_SQL = """
WITH picked AS (
    SELECT c.id
      FROM offers_companies AS c
     WHERE c.logo_status = 'pending' AND c.logo_next_attempt_at <= $1
     ORDER BY EXISTS (
                SELECT 1 FROM offers_offers AS o WHERE o.company_id = c.id AND o.is_active
              ) DESC,
              c.logo_next_attempt_at
     LIMIT $3
       FOR UPDATE SKIP LOCKED
)
UPDATE offers_companies AS c
   SET logo_next_attempt_at = $2
  FROM picked
 WHERE c.id = picked.id
RETURNING c.id
"""


def _to_record(row: CompanyModel) -> CompanyRecord:
    return CompanyRecord(
        id=row.id,
        key=row.key,
        name=row.name,
        domain=row.domain,
        website=row.website,
        source_logo_url=row.source_logo_url,
        v1_id=row.v1_id,
        v1_logo_url=row.v1_logo_url,
        logo_hash=row.logo_hash,
        logo_origin=LogoOrigin(row.logo_origin) if row.logo_origin else None,
        logo_status=LogoStatus(row.logo_status),
        logo_attempts=row.logo_attempts,
        logo_next_attempt_at=row.logo_next_attempt_at,
        logo_checked_at=row.logo_checked_at,
        logo_rejected_urls=tuple(row.logo_rejected_urls or ()),
    )


def _values(record: CompanyRecord) -> dict[str, object]:
    return {
        "key": record.key,
        "domain": record.domain,
        "name": record.name,
        "website": record.website,
        "source_logo_url": record.source_logo_url,
        "v1_id": record.v1_id,
        "v1_logo_url": record.v1_logo_url,
        "logo_hash": record.logo_hash,
        "logo_origin": record.logo_origin,
        "logo_status": record.logo_status,
        "logo_attempts": record.logo_attempts,
        "logo_next_attempt_at": record.logo_next_attempt_at,
        "logo_checked_at": record.logo_checked_at,
        "logo_rejected_urls": list(record.logo_rejected_urls),
    }


class TortoiseCompanyRepository:
    """Implémente `CompanyRepository`."""

    async def find_by_keys(self, keys: Sequence[str]) -> dict[str, list[CompanyRecord]]:
        """Une requête pour tout le lot."""
        found: dict[str, list[CompanyRecord]] = {}
        if not keys:
            return found
        rows = await CompanyModel.filter(key__in=list(keys)).order_by("created_at", "id")
        for row in rows:
            found.setdefault(row.key, []).append(_to_record(row))
        return found

    async def save_many(self, records: Sequence[CompanyRecord]) -> None:
        """Insère les nouvelles en un lot, met à jour les autres."""
        if not records:
            return
        ids = [r.id for r in records]
        existing = set(await CompanyModel.filter(id__in=ids).values_list("id", flat=True))
        fresh = [CompanyModel(id=r.id, **_values(r)) for r in records if r.id not in existing]
        if fresh:
            await CompanyModel.bulk_create(fresh, batch_size=1_000)
        for record in records:
            if record.id in existing:
                await CompanyModel.filter(id=record.id).update(**_values(record))

    async def link_offers(self, links: Sequence[OfferLink]) -> int:
        """Une mise à jour pour tout le lot ; ne touche que les lignes qui changent."""
        if not links:
            return 0
        connection = connections.get("default")
        count, _ = await connection.execute_query(
            _LINK_SQL,
            [
                [link.source for link in links],
                [link.source_id for link in links],
                [link.company_id for link in links],
            ],
        )
        return int(count)

    async def claim_logo_work(
        self, *, now: datetime, until: datetime, limit: int
    ) -> list[CompanyRecord]:
        """Prise sous bail, sans double prise entre processus."""
        connection = connections.get("default")
        rows = await connection.execute_query_dict(_CLAIM_SQL, [now, until, limit])
        if not rows:
            return []
        models = await CompanyModel.filter(id__in=[row["id"] for row in rows])
        return [_to_record(model) for model in models]

    async def save_logo(self, record: CompanyRecord) -> int:
        """L'issue, puis le logo recopié sur les offres de l'entreprise."""
        await CompanyModel.filter(id=record.id).update(**_values(record))
        if record.logo_hash is None:
            return 0
        return await OfferModel.filter(company_id=record.id).update(
            company_logo_hash=record.logo_hash
        )

    async def unlinked_offers(
        self, *, after: UUID | None, limit: int, active_only: bool
    ) -> list[Offer]:
        """Par identifiant croissant (pagination par clé, stable pendant qu'on écrit)."""
        query = OfferModel.filter(company_id__isnull=True, company_name__isnull=False)
        if active_only:
            query = query.filter(is_active=True)
        if after is not None:
            query = query.filter(id__gt=after)
        rows = await query.order_by("id").limit(limit)
        return [_to_offer(row) for row in rows]
