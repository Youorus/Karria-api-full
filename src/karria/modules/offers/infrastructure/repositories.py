"""Dépôts Tortoise du module `offers`. Ne rendent jamais un modèle.

Deux requêtes passent par du SQL écrit à la main : la page du fil (jointure
`offers_feed_entries` x `offers_offers` sans clé étrangère déclarée) et
les options de filtre (agrégats). Tout le reste est l'ORM.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from tortoise import connections
from tortoise.expressions import Q
from tortoise.functions import Count

from karria.modules.offers.application import (
    FeedQuery,
    IngestionReport,
    Message,
    OfferFilters,
    OutboxItem,
    StoredCriteria,
    StoredTarget,
)
from karria.modules.offers.domain import (
    Company,
    ContractType,
    FeedEntry,
    FeedOrigin,
    GeocodedPlace,
    InteractionEvent,
    KarriaMemory,
    Location,
    MatchQuality,
    MemoryFacts,
    Offer,
    OfferState,
    Salary,
    ScrapeTarget,
    SearchCriteria,
    TargetDemand,
    TargetOrigin,
    TargetStats,
    Widening,
    bands,
    fold,
)
from karria.modules.offers.infrastructure.models import (
    ConversationModel,
    CriteriaModel,
    FeedEntryModel,
    GeocodeCacheModel,
    IndexOutboxModel,
    IngestionRunModel,
    InteractionEventModel,
    KarriaMemoryModel,
    MessageModel,
    OfferModel,
    OfferStateModel,
    ScrapeTargetModel,
    TargetDemandModel,
)

_SIGN_BIT = 1 << 63
_UNSIGNED_MASK = (1 << 64) - 1


def to_signed(value: int) -> int:
    """Un entier 64 bits non signé vers la plage signée de `BIGINT`."""
    return value - (1 << 64) if value & _SIGN_BIT else value


def to_unsigned(value: int) -> int:
    """L'inverse."""
    return value & _UNSIGNED_MASK


# ---------------------------------------------------------------------
# Offres
# ---------------------------------------------------------------------


def _to_offer(row: OfferModel) -> Offer:
    location = (
        Location(
            label=row.location_label or row.city or "",
            city=row.city,
            postal_code=row.postal_code,
            department_code=row.department_code,
            latitude=row.latitude,
            longitude=row.longitude,
        )
        if row.location_label or row.city or row.department_code
        else None
    )
    return Offer(
        id=row.id,
        source=row.source,
        source_id=row.source_id,
        title=row.title,
        company=Company(
            name=row.company_name,
            logo_url=row.company_logo_url,
            website=row.company_url,
            logo_hash=row.company_logo_hash,
        )
        if row.company_name
        else None,
        description=row.description,
        location=location,
        contract_type=row.contract_type,
        remote=row.remote,
        schedule=row.schedule,
        experience=row.experience,
        salary=Salary(
            min_monthly=row.salary_min_monthly,
            max_monthly=row.salary_max_monthly,
            raw=row.salary_raw,
        ),
        published_at=row.published_at,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        apply_url=row.apply_url,
        origin_url=row.origin_url,
        is_active=row.is_active,
        content_hash=row.content_hash,
        simhash=to_unsigned(row.simhash),
        duplicate_of=row.duplicate_of,
        indexed_version=row.indexed_version,
        indexed_hash=row.indexed_hash,
        indexed_at=row.indexed_at,
    )


def _columns(offer: Offer) -> dict[str, Any]:
    """Les colonnes qu'écrit l'ingestion. `company_id` et `company_logo_hash` n'en sont pas :
    seul le rattachement aux entreprises les écrit (`TortoiseCompanyRepository`)."""
    b0, b1, b2, b3 = bands(offer.simhash)
    return {
        "source": offer.source,
        "source_id": offer.source_id,
        "title": offer.title,
        "company_name": offer.company.name if offer.company else None,
        "company_key": fold(offer.company.name)[:255] if offer.company else None,
        "company_logo_url": offer.company.logo_url if offer.company else None,
        "company_url": offer.company.website if offer.company else None,
        "description": offer.description,
        "location_label": offer.location.label if offer.location else None,
        "city": offer.location.city if offer.location else None,
        "postal_code": offer.location.postal_code if offer.location else None,
        "department_code": offer.location.department_code if offer.location else None,
        "latitude": offer.location.latitude if offer.location else None,
        "longitude": offer.location.longitude if offer.location else None,
        "contract_type": offer.contract_type,
        "remote": offer.remote,
        "schedule": offer.schedule,
        "experience": offer.experience,
        "salary_min_monthly": offer.salary.min_monthly,
        "salary_max_monthly": offer.salary.max_monthly,
        "salary_raw": offer.salary.raw[:255] if offer.salary.raw else None,
        "published_at": offer.published_at,
        "first_seen_at": offer.first_seen_at,
        "last_seen_at": offer.last_seen_at,
        "apply_url": offer.apply_url[:2048] if offer.apply_url else None,
        "origin_url": offer.origin_url[:2048] if offer.origin_url else None,
        "is_active": offer.is_active,
        "content_hash": offer.content_hash,
        "simhash": to_signed(offer.simhash),
        "simhash_band0": b0 - 32_768,
        "simhash_band1": b1 - 32_768,
        "simhash_band2": b2 - 32_768,
        "simhash_band3": b3 - 32_768,
        "duplicate_of": offer.duplicate_of,
        "indexed_version": offer.indexed_version,
        "indexed_hash": offer.indexed_hash,
        "indexed_at": offer.indexed_at,
    }


class TortoiseOfferRepository:
    """Implémente `OfferRepository`."""

    async def insert_many(self, offers: Sequence[Offer]) -> None:
        """Insère en une requête."""
        if offers:
            await OfferModel.bulk_create([OfferModel(id=o.id, **_columns(o)) for o in offers])

    async def save(self, offer: Offer) -> None:
        """Réécrit tout."""
        await OfferModel.filter(id=offer.id).update(**_columns(offer))

    async def find(self, offer_id: UUID) -> Offer | None:
        """Par identifiant."""
        row = await OfferModel.get_or_none(id=offer_id)
        return None if row is None else _to_offer(row)

    async def find_many(self, offer_ids: Sequence[UUID]) -> dict[UUID, Offer]:
        """Par identifiants."""
        if not offer_ids:
            return {}
        rows = await OfferModel.filter(id__in=list(offer_ids))
        return {row.id: _to_offer(row) for row in rows}

    async def find_by_sources(
        self, keys: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], Offer]:
        """Une requête sur les identifiants de source, le site vérifié ensuite."""
        if not keys:
            return {}
        wanted = set(keys)
        rows = await OfferModel.filter(source_id__in=list({source_id for _, source_id in keys}))
        found: dict[tuple[str, str], Offer] = {}
        for row in rows:
            key = (row.source.value, row.source_id)
            if key in wanted:
                found[key] = _to_offer(row)
        return found

    async def find_canonical_by_hashes(self, hashes: Sequence[str]) -> dict[str, Offer]:
        """La plus ancienne canonique active de chaque condensat."""
        if not hashes:
            return {}
        rows = await OfferModel.filter(
            content_hash__in=list(hashes), duplicate_of__isnull=True, is_active=True
        ).order_by("created_at")
        found: dict[str, Offer] = {}
        for row in rows:
            found.setdefault(row.content_hash, _to_offer(row))
        return found

    async def near_duplicate_candidates(
        self, *, bands: tuple[int, int, int, int], company_key: str | None, since: datetime
    ) -> Sequence[Offer]:
        """Même employeur, une bande SimHash commune, récentes, canoniques, actives."""
        b0, b1, b2, b3 = (b - 32_768 for b in bands)
        query = OfferModel.filter(
            Q(simhash_band0=b0) | Q(simhash_band1=b1) | Q(simhash_band2=b2) | Q(simhash_band3=b3),
            duplicate_of__isnull=True,
            is_active=True,
            published_at__gte=since,
        )
        query = (
            query.filter(company_key=company_key[:255])
            if company_key
            else query.filter(company_key__isnull=True)
        )
        rows = await query.order_by("-published_at").limit(50)
        return [_to_offer(row) for row in rows]

    async def mark_seen(self, offer_ids: Sequence[UUID], *, at: datetime) -> None:
        """`last_seen_at` = maintenant."""
        if offer_ids:
            await OfferModel.filter(id__in=list(offer_ids)).update(last_seen_at=at)

    async def page_active(
        self, *, filters: OfferFilters, limit: int, offset: int
    ) -> tuple[Sequence[Offer], int]:
        """Actives, canoniques, récentes d'abord."""
        query = _apply_filters(
            OfferModel.filter(is_active=True, duplicate_of__isnull=True), filters
        )
        total = await query.count()
        rows = await query.order_by("-published_at", "-id").offset(offset).limit(limit)
        return [_to_offer(row) for row in rows], total

    async def mark_indexed(
        self, indexed: Sequence[tuple[UUID, str]], *, version: str, at: datetime
    ) -> None:
        """Pose version, condensat et instant : une requête par offre, 64 au plus par lot."""
        for offer_id, digest in indexed:
            await OfferModel.filter(id=offer_id).update(
                indexed_version=version, indexed_hash=digest, indexed_at=at
            )

    async def mark_unindexed(self, offer_ids: Sequence[UUID]) -> None:
        """Efface la version."""
        if offer_ids:
            await OfferModel.filter(id__in=list(offer_ids)).update(
                indexed_version=None, indexed_hash=None, indexed_at=None
            )

    async def deactivate_published_before(self, moment: datetime, *, at: datetime) -> list[UUID]:
        """Désactive les anciennes et rend leurs identifiants."""
        ids = await OfferModel.filter(is_active=True, published_at__lt=moment).values_list(
            "id", flat=True
        )
        found = [UUID(str(value)) for value in ids]
        if found:
            await OfferModel.filter(id__in=found).update(is_active=False, deactivated_at=at)
        return found

    async def stale_index_ids(self, *, version: str, limit: int) -> list[UUID]:
        """Indexables mal indexées, et non indexables encore indexées."""
        to_index = await (
            OfferModel.filter(is_active=True, duplicate_of__isnull=True)
            .filter(Q(indexed_version__isnull=True) | ~Q(indexed_version=version))
            .limit(limit)
            .values_list("id", flat=True)
        )
        to_remove = await (
            OfferModel.filter(Q(is_active=False) | Q(duplicate_of__isnull=False))
            .filter(indexed_version__isnull=False)
            .limit(limit)
            .values_list("id", flat=True)
        )
        return [UUID(str(value)) for value in [*to_index, *to_remove]][:limit]

    async def count_indexable(self) -> int:
        """Actives et canoniques."""
        return await OfferModel.filter(is_active=True, duplicate_of__isnull=True).count()

    async def filter_options(
        self, offer_ids: Sequence[UUID] | None
    ) -> tuple[dict[ContractType, int], dict[str, int]]:
        """Agrégats par contrat et par ville."""
        base = OfferModel.filter(is_active=True, duplicate_of__isnull=True)
        if offer_ids is not None:
            if not offer_ids:
                return {}, {}
            base = base.filter(id__in=list(offer_ids))
        contracts = (
            await base.filter(contract_type__isnull=False)
            .annotate(n=Count("id"))
            .group_by("contract_type")
            .values("contract_type", "n")
        )
        cities = (
            await base.filter(city__isnull=False)
            .annotate(n=Count("id"))
            .group_by("city")
            .order_by("-n")
            .limit(200)
            .values("city", "n")
        )
        return (
            {ContractType(str(row["contract_type"])): int(row["n"]) for row in contracts},
            {str(row["city"]): int(row["n"]) for row in cities},
        )


class TortoiseIndexOutbox:
    """Implémente `IndexOutbox` sur `offers_index_outbox`."""

    async def enqueue(self, offer_ids: Sequence[UUID], *, at: datetime) -> None:
        """Une ligne par offre, dans la transaction courante."""
        if offer_ids:
            await IndexOutboxModel.bulk_create(
                [IndexOutboxModel(offer_id=offer_id, enqueued_at=at) for offer_id in offer_ids]
            )

    async def claim(self, *, limit: int) -> Sequence[OutboxItem]:
        """Les plus anciennes. Le consommateur est unique (verrou de la file)."""
        rows = await IndexOutboxModel.all().order_by("id").limit(limit)
        return [OutboxItem(id=int(row.id), offer_id=row.offer_id) for row in rows]

    async def ack(self, item_ids: Sequence[int]) -> None:
        """Supprime les lignes traitées."""
        if item_ids:
            await IndexOutboxModel.filter(id__in=list(item_ids)).delete()

    async def pending(self) -> int:
        """Combien attendent."""
        return await IndexOutboxModel.all().count()


def _apply_filters(query: Any, filters: OfferFilters) -> Any:
    if filters.contract_types:
        query = query.filter(contract_type__in=list(filters.contract_types))
    if filters.cities:
        query = query.filter(city__in=list(filters.cities))
    if filters.published_after is not None:
        query = query.filter(published_at__gte=filters.published_after)
    if filters.exclude_offer_ids:
        query = query.exclude(id__in=list(filters.exclude_offer_ids))
    return query


# ---------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------


def _to_stored_target(row: ScrapeTargetModel) -> StoredTarget:
    return StoredTarget(
        key=row.key,
        target=ScrapeTarget(
            search_term=row.search_term,
            location=row.location,
            origin=row.origin,
            weight=row.weight,
            distance_km=row.distance_km,
            location_key=row.location_key,
            contract=row.contract,
        ),
        next_scrape_at=row.next_scrape_at,
        last_scraped_at=row.last_scraped_at,
        stats=TargetStats(
            state=row.state,
            scrapes=row.scrapes,
            yield_ema=row.yield_ema,
            zero_streak=row.zero_streak,
            overlap_ema=row.overlap_ema,
            gate_rejects=row.gate_rejects,
            last_found=row.last_found,
        ),
        thin_since=row.thin_since,
        is_active=row.is_active,
    )


class TortoiseScrapeTargetRepository:
    """Implémente `ScrapeTargetRepository`."""

    async def upsert_many(
        self, targets: Sequence[ScrapeTarget], *, at: datetime, due_now: bool = False
    ) -> None:
        """Crée (à l'essai), repèse, réactive ; `due_now` avance la prochaine collecte et note le manque."""
        for target in targets:
            row = await ScrapeTargetModel.get_or_none(key=target.key)
            if row is None:
                await ScrapeTargetModel.create(
                    key=target.key,
                    search_term=target.search_term,
                    location=target.location,
                    location_key=target.location_key,
                    contract=target.contract,
                    origin=target.origin,
                    weight=target.weight,
                    distance_km=target.distance_km,
                    demanded_at=at,
                    next_scrape_at=at,
                    thin_since=at if due_now else None,
                )
                continue
            row.demanded_at = at
            row.is_active = True
            row.distance_km = max(row.distance_km or 0, target.distance_km or 0) or None
            if target.origin is TargetOrigin.DEMAND:
                row.origin = TargetOrigin.DEMAND
            if due_now:
                row.next_scrape_at = at
                row.thin_since = at
            await row.save()

    async def due(self, *, now: datetime, limit: int) -> Sequence[StoredTarget]:
        """Actives et échues ; l'ordre est celui de la priorité, calculée par le cas d'usage."""
        rows = (
            await ScrapeTargetModel.filter(
                Q(next_scrape_at__isnull=True) | Q(next_scrape_at__lte=now), is_active=True
            )
            .order_by("next_scrape_at")
            .limit(limit)
        )
        return [_to_stored_target(row) for row in rows]

    async def lease(self, keys: Sequence[str], *, until: datetime) -> None:
        """Repousse leur échéance."""
        if keys:
            await ScrapeTargetModel.filter(key__in=list(keys)).update(next_scrape_at=until)

    async def find(self, key: str) -> StoredTarget | None:
        """Par clé."""
        row = await ScrapeTargetModel.get_or_none(key=key)
        return None if row is None else _to_stored_target(row)

    async def record_scrape(
        self, key: str, stats: TargetStats, *, at: datetime, next_at: datetime
    ) -> None:
        """Note le passage et ce qu'il a appris."""
        await ScrapeTargetModel.filter(key=key).update(
            last_scraped_at=at,
            next_scrape_at=next_at,
            state=stats.state,
            scrapes=stats.scrapes,
            yield_ema=stats.yield_ema,
            zero_streak=stats.zero_streak,
            overlap_ema=stats.overlap_ema,
            gate_rejects=stats.gate_rejects,
            last_found=stats.last_found,
        )

    async def replace_demands(self, user_id: UUID, keys: Sequence[str], *, at: datetime) -> None:
        """Les liens de la personne : ceux-là, et plus les autres."""
        wanted = set(keys)
        await (
            TargetDemandModel.filter(user_id=user_id).exclude(target_key__in=list(wanted)).delete()
        )
        existing = {
            row.target_key
            for row in await TargetDemandModel.filter(user_id=user_id, target_key__in=list(wanted))
        }
        if existing:
            await TargetDemandModel.filter(user_id=user_id, target_key__in=list(existing)).update(
                demanded_at=at
            )
        for key in wanted - existing:
            await TargetDemandModel.create(user_id=user_id, target_key=key, demanded_at=at)

    async def all_demands(self) -> Sequence[TargetDemand]:
        """Tous les liens."""
        rows = await TargetDemandModel.all().only("user_id", "target_key", "demanded_at")
        return [
            TargetDemand(user_id=r.user_id, target_key=r.target_key, since=r.demanded_at)
            for r in rows
        ]

    async def set_weights(self, weights: Mapping[str, int]) -> None:
        """Le nombre de personnes derrière chaque cible demandée ; zéro pour les autres."""
        await (
            ScrapeTargetModel.filter(origin=TargetOrigin.DEMAND)
            .exclude(key__in=list(weights))
            .update(weight=0)
        )
        for key, weight in weights.items():
            await ScrapeTargetModel.filter(key=key).update(weight=weight)

    async def deactivate_demand_before(self, moment: datetime) -> int:
        """Éteint les demandes périmées."""
        return await ScrapeTargetModel.filter(
            origin=TargetOrigin.DEMAND, is_active=True, demanded_at__lt=moment
        ).update(is_active=False)

    async def count_active(self, origin: TargetOrigin | None = None) -> int:
        """Combien d'actives."""
        query = ScrapeTargetModel.filter(is_active=True)
        if origin is not None:
            query = query.filter(origin=origin)
        return await query.count()

    async def catalogue(self, *, limit: int) -> Sequence[StoredTarget]:
        """Les actives, les plus demandées d'abord."""
        rows = (
            await ScrapeTargetModel.filter(is_active=True).order_by("-weight", "key").limit(limit)
        )
        return [_to_stored_target(row) for row in rows]


class TortoiseIngestionRunRepository:
    """Implémente `IngestionRunRepository`."""

    async def record(self, report: IngestionReport) -> None:
        """Une ligne par passage."""
        await IngestionRunModel.create(
            run_id=report.run_id,
            started_at=report.started_at,
            finished_at=report.finished_at,
            targets=report.targets,
            fetched=report.fetched,
            inserted=report.inserted,
            refreshed=report.refreshed,
            duplicates=report.duplicates,
            near_duplicates=report.near_duplicates,
            updated=report.updated,
            errors=report.errors,
            landing_keys=list(report.landing_keys),
        )


class TortoiseGeocodeCache:
    """Le cache du géocodeur (`GeocodeCache`)."""

    async def lookup(self, query: str) -> tuple[bool, GeocodedPlace | None]:
        """Connu ? Et quoi."""
        row = await GeocodeCacheModel.get_or_none(query=query)
        if row is None:
            return False, None
        if not row.found or row.latitude is None or row.longitude is None or not row.city:
            return True, None
        return True, GeocodedPlace(
            city=row.city,
            postal_code=row.postal_code,
            department_code=row.department_code,
            latitude=row.latitude,
            longitude=row.longitude,
        )

    async def remember(self, query: str, place: GeocodedPlace | None) -> None:
        """Mémorise."""
        await GeocodeCacheModel.update_or_create(
            query=query,
            defaults={
                "found": place is not None,
                "city": place.city if place else None,
                "postal_code": place.postal_code if place else None,
                "department_code": place.department_code if place else None,
                "latitude": place.latitude if place else None,
                "longitude": place.longitude if place else None,
            },
        )


# ---------------------------------------------------------------------
# La personne
# ---------------------------------------------------------------------


def _to_criteria(row: CriteriaModel) -> StoredCriteria:
    return StoredCriteria(
        user_id=row.user_id,
        criteria=SearchCriteria.model_validate(row.criteria),
        fingerprint=row.fingerprint,
        alerts_enabled=row.alerts_enabled,
        updated_at=row.updated_at,
        last_alert_at=row.last_alert_at,
        alerts_sent_on=row.alerts_sent_on,
        alerts_sent_today=row.alerts_sent_today,
    )


class TortoiseCriteriaRepository:
    """Implémente `CriteriaRepository`."""

    async def find(self, user_id: UUID) -> StoredCriteria | None:
        """Par personne."""
        row = await CriteriaModel.get_or_none(user_id=user_id)
        return None if row is None else _to_criteria(row)

    async def save(self, stored: StoredCriteria) -> None:
        """Crée ou remplace."""
        await CriteriaModel.update_or_create(
            user_id=stored.user_id,
            defaults={
                "criteria": stored.criteria.model_dump(mode="json"),
                "fingerprint": stored.fingerprint,
                "alerts_enabled": stored.alerts_enabled,
                "last_alert_at": stored.last_alert_at,
                "alerts_sent_on": stored.alerts_sent_on,
                "alerts_sent_today": stored.alerts_sent_today,
            },
        )

    async def delete(self, user_id: UUID) -> bool:
        """Oublie."""
        return await CriteriaModel.filter(user_id=user_id).delete() > 0

    async def all_with_alerts(self) -> Sequence[StoredCriteria]:
        """Tous ceux dont les alertes sont actives."""
        rows = await CriteriaModel.filter(alerts_enabled=True)
        return [_to_criteria(row) for row in rows]

    async def find_many(self, user_ids: Sequence[UUID]) -> dict[UUID, StoredCriteria]:
        """Par personnes."""
        if not user_ids:
            return {}
        rows = await CriteriaModel.filter(user_id__in=list(user_ids))
        return {row.user_id: _to_criteria(row) for row in rows}

    async def record_alert(self, user_id: UUID, *, at: datetime) -> None:
        """Compte l'envoi du jour."""
        row = await CriteriaModel.get_or_none(user_id=user_id)
        if row is None:
            return
        same_day = row.alerts_sent_on is not None and row.alerts_sent_on.date() == at.date()
        row.alerts_sent_today = row.alerts_sent_today + 1 if same_day else 1
        row.alerts_sent_on = at
        row.last_alert_at = at
        await row.save()


def _to_message(row: MessageModel) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=row.role,
        content=row.content,
        created_at=row.created_at,
    )


class TortoiseKarriaMemoryRepository:
    """Implémente `KarriaMemoryRepository`."""

    async def find(self, user_id: UUID) -> KarriaMemory | None:
        """Par personne."""
        row = await KarriaMemoryModel.get_or_none(user_id=user_id)
        if row is None:
            return None
        return KarriaMemory(
            facts=MemoryFacts.model_validate(row.facts),
            constitution_version=row.constitution_version,
            updated_at=row.updated_at,
        )

    async def save(self, user_id: UUID, memory: KarriaMemory) -> None:
        """Crée ou remplace."""
        await KarriaMemoryModel.update_or_create(
            defaults={
                "facts": memory.facts.model_dump(mode="json"),
                "constitution_version": memory.constitution_version,
            },
            user_id=user_id,
        )

    async def delete(self, user_id: UUID) -> bool:
        """Oublie."""
        return await KarriaMemoryModel.filter(user_id=user_id).delete() > 0


class TortoiseConversationRepository:
    """Implémente `ConversationRepository`."""

    async def create(self, user_id: UUID, *, at: datetime) -> UUID:
        """Ouvre."""
        row = await ConversationModel.create(user_id=user_id, last_message_at=at)
        return row.id

    async def belongs_to(self, conversation_id: UUID, user_id: UUID) -> bool:
        """Existe pour cette personne ?"""
        return await ConversationModel.filter(id=conversation_id, user_id=user_id).exists()

    async def add_message(
        self, conversation_id: UUID, *, role: str, content: str, at: datetime
    ) -> Message:
        """Ajoute."""
        conversation = await ConversationModel.get(id=conversation_id)
        row = await MessageModel.create(
            conversation_id=conversation_id,
            user_id=conversation.user_id,
            role=role,
            content=content,
        )
        await ConversationModel.filter(id=conversation_id).update(last_message_at=at)
        return _to_message(row)

    async def recent_messages(self, conversation_id: UUID, *, limit: int) -> Sequence[Message]:
        """Les derniers, dans l'ordre chronologique."""
        rows = (
            await MessageModel.filter(conversation_id=conversation_id)
            .order_by("-created_at")
            .limit(limit)
        )
        return [_to_message(row) for row in reversed(rows)]

    async def set_pending_widenings(
        self, conversation_id: UUID, widenings: Sequence[Widening]
    ) -> None:
        """Remplace."""
        await ConversationModel.filter(id=conversation_id).update(
            pending_widenings=[w.to_dict() for w in widenings] or None
        )

    async def pending_widenings(self, conversation_id: UUID) -> Sequence[Widening]:
        """Relit, validé."""
        row = await ConversationModel.get_or_none(id=conversation_id)
        if row is None or not row.pending_widenings:
            return []
        return [Widening.from_dict(item) for item in row.pending_widenings]

    async def user_turns_since(self, user_id: UUID, since: datetime) -> int:
        """Messages de la personne depuis."""
        return await MessageModel.filter(
            user_id=user_id, role="user", created_at__gte=since
        ).count()

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface."""
        await MessageModel.filter(user_id=user_id).delete()
        return await ConversationModel.filter(user_id=user_id).delete()


def _to_entry(row: FeedEntryModel) -> FeedEntry:
    return FeedEntry(
        user_id=row.user_id,
        offer_id=row.offer_id,
        score=row.score,
        quality=row.quality,
        reasons=tuple(str(r) for r in (row.reasons or [])),
        origin=row.origin,
        created_at=row.created_at,
        notified_at=row.notified_at,
    )


class TortoiseFeedRepository:
    """Implémente `FeedRepository`."""

    async def entries_of(self, user_id: UUID) -> Sequence[FeedEntry]:
        """Toutes."""
        return [_to_entry(row) for row in await FeedEntryModel.filter(user_id=user_id)]

    async def replace(
        self, user_id: UUID, entries: Sequence[FeedEntry], *, remove: Sequence[UUID]
    ) -> None:
        """Retire puis écrit — en un lot : un fil peut porter mille offres."""
        if remove:
            await FeedEntryModel.filter(user_id=user_id, offer_id__in=list(remove)).delete()
        if entries:
            await FeedEntryModel.bulk_create(
                [
                    FeedEntryModel(
                        user_id=user_id,
                        offer_id=e.offer_id,
                        score=e.score,
                        quality=e.quality,
                        reasons=list(e.reasons),
                        origin=e.origin,
                    )
                    for e in entries
                ],
                on_conflict=["user_id", "offer_id"],
                update_fields=["score", "quality", "reasons", "origin", "updated_at"],
            )

    async def add_many(self, entries: Sequence[FeedEntry]) -> None:
        """Ajoute sans écraser."""
        if entries:
            await FeedEntryModel.bulk_create(
                [
                    FeedEntryModel(
                        user_id=e.user_id,
                        offer_id=e.offer_id,
                        score=e.score,
                        quality=e.quality,
                        reasons=list(e.reasons),
                        origin=e.origin,
                    )
                    for e in entries
                ],
                ignore_conflicts=True,
            )

    async def page(self, query: FeedQuery) -> tuple[Sequence[FeedEntry], int]:
        """Jointure avec les offres pour filtrer et trier ; offres actives seulement."""
        conditions = ["f.user_id = $1", "o.is_active = TRUE", "o.duplicate_of IS NULL"]
        params: list[Any] = [query.user_id]
        if query.filters.contract_types:
            params.append([c.value for c in query.filters.contract_types])
            conditions.append(f"o.contract_type = ANY(${len(params)}::text[])")
        if query.filters.cities:
            params.append(list(query.filters.cities))
            conditions.append(f"o.city = ANY(${len(params)}::text[])")
        if query.filters.published_after is not None:
            params.append(query.filters.published_after)
            conditions.append(f"o.published_at >= ${len(params)}")
        if query.filters.exclude_offer_ids:
            params.append(list(query.filters.exclude_offer_ids))
            conditions.append(f"NOT (f.offer_id = ANY(${len(params)}::uuid[]))")
        where = " AND ".join(conditions)
        order = (
            "o.published_at DESC, f.score DESC"
            if query.sort == "recent"
            else "f.score DESC, o.published_at DESC"
        )
        base = f"FROM offers_feed_entries f JOIN offers_offers o ON o.id = f.offer_id WHERE {where}"
        connection = connections.get("default")
        total_rows = await connection.execute_query_dict(f"SELECT COUNT(*) AS n {base}", params)
        rows = await connection.execute_query_dict(
            f"SELECT f.* {base} ORDER BY {order}, f.offer_id LIMIT {int(query.limit)} OFFSET {int(query.offset)}",
            params,
        )
        entries = [
            FeedEntry(
                user_id=row["user_id"],
                offer_id=row["offer_id"],
                score=float(row["score"]),
                quality=MatchQuality(row["quality"]),
                reasons=tuple(str(r) for r in (row["reasons"] or [])),
                origin=FeedOrigin(row["origin"]),
                created_at=row["created_at"],
                notified_at=row["notified_at"],
            )
            for row in rows
        ]
        return entries, int(total_rows[0]["n"]) if total_rows else 0

    async def find(self, user_id: UUID, offer_id: UUID) -> FeedEntry | None:
        """Une entrée."""
        row = await FeedEntryModel.get_or_none(user_id=user_id, offer_id=offer_id)
        return None if row is None else _to_entry(row)

    async def mark_notified(
        self, user_id: UUID, offer_ids: Sequence[UUID], *, at: datetime
    ) -> None:
        """Pose l'instant."""
        if offer_ids:
            await FeedEntryModel.filter(user_id=user_id, offer_id__in=list(offer_ids)).update(
                notified_at=at
            )

    async def offer_ids_of(self, user_id: UUID) -> Sequence[UUID]:
        """Les identifiants."""
        rows = await FeedEntryModel.filter(user_id=user_id).values_list("offer_id", flat=True)
        return [UUID(str(value)) for value in rows]

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface."""
        return await FeedEntryModel.filter(user_id=user_id).delete()


def _to_state(row: OfferStateModel) -> OfferState:
    return OfferState(
        user_id=row.user_id,
        offer_id=row.offer_id,
        seen=row.seen,
        opened=row.opened,
        link_clicked=row.link_clicked,
        saved=row.saved,
        applied=row.applied,
        dismissed=row.dismissed,
        seen_at=row.seen_at,
        opened_at=row.opened_at,
        saved_at=row.saved_at,
        applied_at=row.applied_at,
        dismissed_at=row.dismissed_at,
        updated_at=row.updated_at,
    )


class TortoiseOfferStateRepository:
    """Implémente `OfferStateRepository`."""

    async def find(self, user_id: UUID, offer_id: UUID) -> OfferState | None:
        """Un état."""
        row = await OfferStateModel.get_or_none(user_id=user_id, offer_id=offer_id)
        return None if row is None else _to_state(row)

    async def find_many(self, user_id: UUID, offer_ids: Sequence[UUID]) -> dict[UUID, OfferState]:
        """Plusieurs."""
        if not offer_ids:
            return {}
        rows = await OfferStateModel.filter(user_id=user_id, offer_id__in=list(offer_ids))
        return {row.offer_id: _to_state(row) for row in rows}

    async def all_of_user(self, user_id: UUID) -> dict[UUID, OfferState]:
        """Tous."""
        rows = await OfferStateModel.filter(user_id=user_id)
        return {row.offer_id: _to_state(row) for row in rows}

    async def offer_ids_where(
        self,
        user_id: UUID,
        *,
        saved: bool = False,
        dismissed: bool = False,
        seen: bool = False,
        applied: bool = False,
    ) -> Sequence[UUID]:
        """Au moins un indicateur demandé vrai."""
        flags = {"saved": saved, "dismissed": dismissed, "seen": seen, "applied": applied}
        wanted = [name for name, on in flags.items() if on]
        if not wanted:
            return []
        condition = Q()
        for name in wanted:
            condition |= Q(**{name: True})  # type: ignore[arg-type]  # Tortoise type `**kwargs` en `str`
        rows = await OfferStateModel.filter(condition, user_id=user_id).values_list(
            "offer_id", flat=True
        )
        return [UUID(str(value)) for value in rows]

    async def save(self, state: OfferState) -> None:
        """Crée ou remplace."""
        await OfferStateModel.update_or_create(
            user_id=state.user_id,
            offer_id=state.offer_id,
            defaults={
                "seen": state.seen,
                "opened": state.opened,
                "link_clicked": state.link_clicked,
                "saved": state.saved,
                "applied": state.applied,
                "dismissed": state.dismissed,
                "seen_at": state.seen_at,
                "opened_at": state.opened_at,
                "saved_at": state.saved_at,
                "applied_at": state.applied_at,
                "dismissed_at": state.dismissed_at,
            },
        )

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface."""
        return await OfferStateModel.filter(user_id=user_id).delete()


class TortoiseInteractionLog:
    """Implémente `InteractionLog` : un lot, une requête."""

    async def append(self, events: Sequence[InteractionEvent]) -> None:
        """Consigne."""
        if not events:
            return
        await InteractionEventModel.bulk_create(
            [
                InteractionEventModel(
                    user_id=e.user_id,
                    offer_id=e.offer_id,
                    kind=e.kind.value,
                    at=e.at,
                    position=e.position,
                    dwell_ms=e.dwell_ms,
                    feed_mode=e.snapshot.feed_mode.value,
                    criteria_fingerprint=e.snapshot.criteria_fingerprint,
                    score=e.snapshot.score,
                    quality=e.snapshot.quality,
                    origin=e.snapshot.origin,
                    source=e.snapshot.source,
                    age_hours=e.snapshot.age_hours,
                    distance_km=e.snapshot.distance_km,
                    title_match=e.snapshot.title_match,
                    contract_match=e.snapshot.contract_match,
                    schedule_match=e.snapshot.schedule_match,
                    has_salary=e.snapshot.has_salary,
                )
                for e in events
            ]
        )
