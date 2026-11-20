"""Les recherches enregistrées d'une personne : les retenir, les lister, les activer, les couper (ADR-0041)."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from karria.modules.offers.application.ports import (
    CriteriaIndex,
    CriteriaRepository,
    SavedSearch,
    SavedSearchRepository,
    ScrapeTargetRepository,
    StoredCriteria,
)
from karria.modules.offers.domain.errors import SavedSearchNotFoundError
from karria.modules.offers.domain.saved import HINT_UNTIL, demanded_keys, to_evict
from karria.shared.clock import Clock
from karria.shared.logging import get_logger

logger = get_logger(__name__)


class ClearCurrent(Protocol):
    """Ce que `DeleteSavedSearch` attend de l'oubli de la recherche en cours (`ClearCriteria`)."""

    async def execute(self, user_id: UUID) -> None:
        """Le fil redevient « toutes les offres »."""
        ...


@dataclass(frozen=True, slots=True)
class Remembered:
    """Ce que retenir une recherche a donné."""

    search: SavedSearch
    #: Cette recherche vient d'être enregistrée pour la première fois.
    first_time: bool
    #: Karria peut dire, en une phrase, que l'alerte se coupe dans les paramètres.
    hint: bool


class SavedSearches:
    """Le registre des recherches d'une personne, tenu en phase avec l'index et la collecte."""

    def __init__(
        self,
        *,
        saved: SavedSearchRepository,
        criteria_index: CriteriaIndex,
        targets: ScrapeTargetRepository,
    ) -> None:
        self._saved = saved
        self._criteria_index = criteria_index
        self._targets = targets

    async def alerts_flag(self, user_id: UUID, fingerprint: str) -> bool | None:
        """L'état de l'alerte de cette recherche si elle est déjà enregistrée, sinon `None`."""
        found = await self._saved.find(user_id, fingerprint)
        return None if found is None else found.alerts_enabled

    async def remember(
        self,
        stored: StoredCriteria,
        vector: Sequence[float],
        target_keys: Sequence[str],
        *,
        now: datetime,
    ) -> Remembered:
        """Enregistre (ou rafraîchit) la recherche qui vient d'être appliquée.

        Son vecteur va dans l'index sous **son** identifiant : elle continue de
        surveiller les nouvelles offres quand une autre devient la recherche en
        cours. Au-delà de dix, la moins récemment utilisée sort.
        """
        known = await self._saved.find(stored.user_id, stored.fingerprint)
        search = SavedSearch(
            id=known.id if known else uuid4(),
            user_id=stored.user_id,
            criteria=stored.criteria,
            fingerprint=stored.fingerprint,
            alerts_enabled=stored.alerts_enabled,
            created_at=known.created_at if known else now,
            last_used_at=now,
            target_keys=tuple(target_keys),
            last_alert_at=known.last_alert_at if known else None,
            alerts_sent_on=known.alerts_sent_on if known else None,
            alerts_sent_today=known.alerts_sent_today if known else 0,
            notified_offer_ids=known.notified_offer_ids if known else (),
        )
        await self._saved.save(search)
        await self._criteria_index.upsert(
            search.id, vector, search.criteria, alerts_enabled=search.alerts_enabled
        )
        everything = list(await self._saved.list_of_user(stored.user_id))
        for old in to_evict(everything, current=stored.fingerprint):
            await self._saved.delete(old.id)
            await self._criteria_index.delete(old.id)
            everything.remove(old)
        await self._targets.replace_demands(stored.user_id, demanded_keys(everything), at=now)
        first_time = known is None
        return Remembered(
            search=search,
            first_time=first_time,
            hint=first_time and search.alerts_enabled and len(everything) <= HINT_UNTIL,
        )

    async def set_enabled(
        self, user_id: UUID, fingerprint: str, *, enabled: bool, now: datetime
    ) -> SavedSearch | None:
        """Active ou coupe l'alerte de la recherche de ces critères (si elle est enregistrée)."""
        found = await self._saved.find(user_id, fingerprint)
        if found is None:
            return None
        return await self._switch(found, enabled=enabled, now=now)

    async def _switch(self, search: SavedSearch, *, enabled: bool, now: datetime) -> SavedSearch:
        updated = dataclasses.replace(search, alerts_enabled=enabled)
        await self._saved.save(updated)
        try:
            await self._criteria_index.set_alerts_enabled(search.id, enabled=enabled)
        except Exception:
            # Une recherche reprise d'avant ADR-0041 n'a pas encore son point
            # d'index (il est écrit quand elle est appliquée de nouveau) : la
            # bascule ne doit pas échouer pour autant.
            logger.info("offers.saved_search_not_indexed", search_id=str(search.id))
        await self.sync_demands(search.user_id, now=now)
        return updated

    async def sync_demands(self, user_id: UUID, *, now: datetime) -> None:
        """La collecte suit les recherches dont l'alerte est active, et elles seules."""
        searches = await self._saved.list_of_user(user_id)
        await self._targets.replace_demands(user_id, demanded_keys(searches), at=now)

    async def forget_user(self, user_id: UUID) -> None:
        """À la suppression du compte : plus aucune recherche, plus aucun point d'index."""
        for search_id in await self._saved.delete_all_of_user(user_id):
            await self._criteria_index.delete(search_id)


@dataclass(frozen=True, slots=True)
class ListedSearch:
    """Une recherche enregistrée, telle que les paramètres la montrent."""

    search: SavedSearch
    #: C'est la recherche en cours : elle fait le fil.
    is_current: bool


class ListSavedSearches:
    """Les recherches enregistrées d'une personne, la plus récemment utilisée d'abord."""

    def __init__(self, *, saved: SavedSearchRepository, criteria: CriteriaRepository) -> None:
        self._saved = saved
        self._criteria = criteria

    async def execute(self, user_id: UUID) -> list[ListedSearch]:
        """Rend la liste ; vide tant que la personne n'a rien cherché."""
        current = await self._criteria.find(user_id)
        fingerprint = current.fingerprint if current else None
        return [
            ListedSearch(search=s, is_current=s.fingerprint == fingerprint)
            for s in await self._saved.list_of_user(user_id)
        ]


class SetSavedSearchAlerts:
    """Active ou coupe l'alerte d'une recherche enregistrée, depuis les paramètres."""

    def __init__(
        self,
        *,
        saved: SavedSearchRepository,
        registry: SavedSearches,
        criteria: CriteriaRepository,
        criteria_index: CriteriaIndex,
        clock: Clock,
    ) -> None:
        self._saved = saved
        self._registry = registry
        self._criteria = criteria
        self._criteria_index = criteria_index
        self._clock = clock

    async def execute(self, user_id: UUID, search_id: UUID, *, enabled: bool) -> ListedSearch:
        """Bascule, et tient la recherche en cours en phase si c'est elle.

        Raises:
            SavedSearchNotFoundError: Recherche inconnue, ou d'une autre personne.
        """
        search = await _owned(self._saved, user_id, search_id)
        now = self._clock.now()
        updated = await self._registry.set_enabled(
            user_id, search.fingerprint, enabled=enabled, now=now
        )
        current = await self._criteria.find(user_id)
        is_current = current is not None and current.fingerprint == search.fingerprint
        if current is not None and is_current and current.alerts_enabled != enabled:
            await self._criteria.save(
                dataclasses.replace(current, alerts_enabled=enabled, updated_at=now)
            )
            await self._criteria_index.set_alerts_enabled(user_id, enabled=enabled)
        logger.info(
            "offers.saved_search_switched",
            user_id=str(user_id),
            search_id=str(search_id),
            enabled=enabled,
            current=is_current,
        )
        return ListedSearch(search=updated or search, is_current=is_current)


class DeleteSavedSearch:
    """Supprime une recherche enregistrée ; si c'est la recherche en cours, le fil redevient général."""

    def __init__(
        self,
        *,
        saved: SavedSearchRepository,
        registry: SavedSearches,
        criteria: CriteriaRepository,
        criteria_index: CriteriaIndex,
        clear: ClearCurrent,
        clock: Clock,
    ) -> None:
        self._saved = saved
        self._registry = registry
        self._criteria = criteria
        self._criteria_index = criteria_index
        self._clear = clear
        self._clock = clock

    async def execute(self, user_id: UUID, search_id: UUID) -> None:
        """Oublie la recherche, son point d'index et ce qu'elle demandait à la collecte.

        Raises:
            SavedSearchNotFoundError: Recherche inconnue, ou d'une autre personne.
        """
        search = await _owned(self._saved, user_id, search_id)
        current = await self._criteria.find(user_id)
        await self._saved.delete(search.id)
        await self._criteria_index.delete(search.id)
        if current is not None and current.fingerprint == search.fingerprint:
            await self._clear.execute(user_id)
        await self._registry.sync_demands(user_id, now=self._clock.now())
        logger.info("offers.saved_search_deleted", user_id=str(user_id), search_id=str(search_id))


async def _owned(saved: SavedSearchRepository, user_id: UUID, search_id: UUID) -> SavedSearch:
    search = await saved.get(search_id)
    if search is None or search.user_id != user_id:
        raise SavedSearchNotFoundError()
    return search
