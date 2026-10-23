"""Un passage de collecte : louer les cibles, interroger les sites, remettre les lots.

Les règles de prudence envers les sites vivent ici :

- une **pause** entre deux requêtes au même site ;
- un **disjoncteur** par site : après `site_failure_limit` échecs
  consécutifs (bannissement, page changée), le site est laissé de côté
  pour le reste du passage — insister ne fait qu'aggraver le bannissement ;
- un **zéro silencieux** : un site qui rend zéro annonce sur plusieurs
  cibles qui en rendaient la veille n'a pas « rien trouvé », il a changé
  (HelloWork, 2026-10-22 : `<span>` au lieu de `<p>`, zéro sans erreur) ;
  il est arrêté pour le passage et journalisé en erreur ;
- un **budget horaire** par site, et une gigue sur la pause ;
- un lot est remis **même vide** : il clôt la cible côté API (« rien de
  neuf ») au lieu d'attendre la fin du bail.

Une panne de l'API arrête le passage ; une panne d'un site ne l'arrête pas.

France Travail (`FranceTravailCollector`) suit un autre rythme : pas de
cible, un **passage incrémental** toutes les quelques minutes sur les
annonces créées depuis le dernier passage, et un **balayage** quotidien
par département qui rattrape ce qu'un passage aurait manqué. Ses lots
partent sans `X-Target-Key`.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from karria.collector.api_client import CollectorApi
from karria.collector.contract import BATCH_MAX_POSTINGS, encode_batch
from karria.collector.france_travail import (
    FranceTravailClient,
    FranceTravailError,
    collect_window,
    offer_to_record,
)
from karria.collector.settings import CollectorSettings
from karria.collector.sources import Target, scrape
from karria.shared.logging import get_logger

logger = get_logger(__name__)

Scraper = Callable[..., list[dict[str, Any]]]


@dataclass(slots=True)
class RunSummary:
    """Ce qu'un passage a fait."""

    targets: int = 0
    postings: int = 0
    batches: int = 0
    site_failures: dict[str, int] = field(default_factory=dict)
    sites_tripped: list[str] = field(default_factory=list)
    #: Sites arrêtés parce qu'ils rendaient zéro là où ils rendaient beaucoup.
    sites_silent: list[str] = field(default_factory=list)
    #: Requêtes non faites faute de budget horaire, par site.
    over_budget: dict[str, int] = field(default_factory=dict)


class Collector:
    """Le passage, avec ses dépendances injectées (testable sans réseau)."""

    def __init__(
        self,
        settings: CollectorSettings,
        api: CollectorApi,
        *,
        scraper: Scraper = scrape,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._api = api
        self._scraper = scraper
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        #: Instants des requêtes de la dernière heure, par site (le budget).
        self._requests: dict[str, deque[float]] = {s: deque() for s in settings.sites}

    def _within_budget(self, site: str) -> bool:
        """Le site a-t-il encore du budget sur l'heure glissante ?"""
        window = self._requests[site]
        horizon = self._clock() - 3_600
        while window and window[0] < horizon:
            window.popleft()
        return len(window) < self._settings.site_hourly_budget

    async def run_once(self) -> RunSummary:
        """Un passage complet."""
        summary = RunSummary()
        targets = await self._api.lease_targets(self._settings.targets_per_run)
        streak: dict[str, int] = dict.fromkeys(self._settings.sites, 0)
        silent: dict[str, int] = dict.fromkeys(self._settings.sites, 0)
        for target in targets:
            records: dict[tuple[str, str], dict[str, Any]] = {}
            for site in self._settings.sites:
                if (
                    streak[site] >= self._settings.site_failure_limit
                    or silent[site] >= self._settings.silent_zero_limit
                ):
                    continue
                if not self._within_budget(site):
                    summary.over_budget[site] = summary.over_budget.get(site, 0) + 1
                    continue
                found = await self._query(site, target, summary, streak)
                self._watch_silence(site, target, found, silent)
                for record in found or []:
                    records.setdefault((record["source"], record["source_id"]), record)
            await self._api.push_batch(encode_batch(records.values()), target_key=target.key)
            summary.targets += 1
            summary.batches += 1
            summary.postings += len(records)
        summary.sites_tripped = [
            s for s, n in streak.items() if n >= self._settings.site_failure_limit
        ]
        summary.sites_silent = [
            s for s, n in silent.items() if n >= self._settings.silent_zero_limit
        ]
        logger.info(
            "collector.run_finished",
            targets=summary.targets,
            postings=summary.postings,
            failures=summary.site_failures,
            tripped=summary.sites_tripped,
            silent=summary.sites_silent,
            over_budget=summary.over_budget,
        )
        return summary

    def _watch_silence(
        self, site: str, target: Target, found: list[dict[str, Any]] | None, silent: dict[str, int]
    ) -> None:
        """Compte les zéros là où la cible rendait ; alerte quand ils se répètent."""
        if found is None or target.last_found < self._settings.silent_zero_floor:
            return
        if found:
            silent[site] = 0
            return
        silent[site] += 1
        if silent[site] >= self._settings.silent_zero_limit:
            logger.error(
                "collector.site_silent",
                site=site,
                target=target.key,
                last_found=target.last_found,
                zeros=silent[site],
            )

    async def _query(
        self, site: str, target: Target, summary: RunSummary, streak: dict[str, int]
    ) -> list[dict[str, Any]] | None:
        """Une requête ; `None` si le site a échoué (distinct de « rien trouvé »)."""
        started = time.monotonic()
        self._requests[site].append(self._clock())
        try:
            found = await asyncio.to_thread(
                self._scraper,
                site,
                target,
                results_wanted=self._settings.results_wanted,
                hours_old=self._settings.hours_old,
                proxies=self._settings.proxies,
                timeout_seconds=self._settings.request_timeout_seconds,
            )
        except Exception as exc:
            streak[site] += 1
            summary.site_failures[site] = summary.site_failures.get(site, 0) + 1
            logger.warning(
                "collector.site_failed", site=site, target=target.key, error=str(exc)[:200]
            )
            found = None
        else:
            streak[site] = 0
            logger.info(
                "collector.fetched",
                site=site,
                target=target.key,
                rows=len(found or []),
                seconds=round(time.monotonic() - started, 1),
            )
        spread = self._settings.pause_jitter * (2 * self._jitter() - 1)
        await self._sleep(round(self._settings.pause_seconds * (1 + spread), 3))
        return found


@dataclass(slots=True)
class PollSummary:
    """Ce qu'un passage France Travail a fait."""

    window_start: datetime
    window_end: datetime
    fetched: int = 0
    postings: int = 0
    batches: int = 0
    requests: int = 0


Windowed = Callable[[FranceTravailClient, datetime, datetime], Iterable[dict[str, Any]]]


class FranceTravailCollector:
    """La source n°1 : incrémentale, sans cible, remise par lots de 2 000."""

    def __init__(
        self,
        settings: CollectorSettings,
        api: CollectorApi,
        client: FranceTravailClient,
        *,
        collect: Windowed = collect_window,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._api = api
        self._client = client
        self._collect = collect
        self._clock = clock
        #: Le dernier instant couvert ; `None` au démarrage.
        self.watermark: datetime | None = None
        self.last_sweep_on: datetime | None = None

    async def poll_once(self) -> PollSummary:
        """Les annonces créées depuis le dernier passage (avec recouvrement)."""
        now = self._clock()
        if self.watermark is None:
            start = now - timedelta(hours=self._settings.france_travail_startup_hours)
        else:
            start = self.watermark - timedelta(
                seconds=self._settings.france_travail_overlap_seconds
            )
        summary = await self._run(start, now, kind="poll")
        self.watermark = now
        return summary

    async def sweep(self, days: int | None = None) -> PollSummary:
        """Un balayage par département des derniers jours : le filet du passage incrémental."""
        now = self._clock()
        span = self._settings.france_travail_sweep_days if days is None else days
        summary = await self._run(now - timedelta(days=span), now, kind="sweep")
        self.last_sweep_on = now
        return summary

    def sweep_due(self, now: datetime) -> bool:
        """Un balayage par jour, à l'heure dite, si la configuration en veut un."""
        if self._settings.france_travail_sweep_days == 0:
            return False
        if now.hour != self._settings.france_travail_sweep_hour_utc:
            return False
        return self.last_sweep_on is None or self.last_sweep_on.date() < now.date()

    async def _run(self, start: datetime, end: datetime, *, kind: str) -> PollSummary:
        summary = PollSummary(window_start=start, window_end=end)
        before = self._client.requests
        started = time.monotonic()
        try:
            offers = await asyncio.to_thread(lambda: list(self._collect(self._client, start, end)))
        except FranceTravailError as exc:
            logger.error(
                "france_travail.failed", kind=kind, status=exc.status, error=str(exc)[:200]
            )
            offers = []
        summary.fetched = len(offers)
        records: dict[str, dict[str, Any]] = {}
        for offer in offers:
            record = offer_to_record(offer)
            if record is not None:
                records.setdefault(record["source_id"], record)
        rows = list(records.values())
        for chunk_start in range(0, len(rows), BATCH_MAX_POSTINGS):
            chunk = rows[chunk_start : chunk_start + BATCH_MAX_POSTINGS]
            await self._api.push_batch(encode_batch(chunk), target_key=None)
            summary.batches += 1
            summary.postings += len(chunk)
        summary.requests = self._client.requests - before
        logger.info(
            "france_travail.collected",
            kind=kind,
            window_start=start.isoformat(),
            window_end=end.isoformat(),
            fetched=summary.fetched,
            postings=summary.postings,
            batches=summary.batches,
            requests=summary.requests,
            seconds=round(time.monotonic() - started, 1),
        )
        return summary
