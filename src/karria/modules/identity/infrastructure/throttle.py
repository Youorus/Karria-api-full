"""Limiteur de tentatives en mémoire — implémentation du port `LoginThrottle`.

**Provisoire, et la limite est réelle** : la mémoire n'est pas partagée
entre processus. Deux répliques accordent chacune leur quota. C'est
acceptable tant que l'API tourne en un exemplaire ; le jour où elle se
réplique, l'implémentation à écrire est un compteur Redis — le port ne
bouge pas.

Ce qu'il protège dès aujourd'hui : l'envoi de courriels à la chaîne vers
une adresse, et la force brute sur un code depuis une origine unique.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Final

from karria.modules.identity.domain import TooManyAttemptsError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Tentatives autorisées dans la fenêtre. Assez pour se tromper plusieurs
#: fois, trop peu pour inonder une boîte ou parcourir des codes.
MAX_ATTEMPTS: Final = 10

#: Largeur de la fenêtre glissante, en secondes.
WINDOW_SECONDS: Final = 300


class InMemoryLoginThrottle:
    """Fenêtre glissante par clé, en mémoire."""

    def __init__(
        self,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        window_seconds: int = WINDOW_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._monotonic = monotonic
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    async def ensure_allowed(self, *, key: str) -> None:
        """Vérifie qu'une tentative supplémentaire est permise.

        Raises:
            TooManyAttemptsError: Le plafond est atteint.
        """
        now = self._monotonic()
        window = self._prune(key, now)
        if len(window) >= self._max:
            retry_after = int(self._window - (now - window[0])) + 1
            logger.warning("auth.throttled", retry_after_seconds=retry_after)
            raise TooManyAttemptsError(retry_after)

    async def record_failure(self, *, key: str) -> None:
        """Consigne une tentative."""
        self._hits[key].append(self._monotonic())

    async def reset(self, *, key: str) -> None:
        """Efface le compteur après une réussite."""
        self._hits.pop(key, None)

    def _prune(self, key: str, now: float) -> deque[float]:
        window = self._hits[key]
        while window and now - window[0] > self._window:
            window.popleft()
        return window
