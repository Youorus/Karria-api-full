"""Limiteur de débit en mémoire — le mécanisme, sans la politique.

Contrat
-------
- **Le socle fournit le compteur, le module fixe le quota.** Combien de
  lectures anonymes par origine, sur quelle fenêtre : c'est une règle du
  module qui ouvre la route (AGENTS.md §6.3), passée à la construction.
- **Fenêtre glissante par clé, en mémoire, par processus.** La même
  limite que celle du limiteur de connexion d'`identity` : deux répliques
  accorderaient chacune leur quota. Acceptable pour une protection contre
  l'aspiration d'un fil public ; le jour de la réplication, un compteur
  Redis prend la place derrière la même interface.
- **Borné.** Une clé par origine, et des origines il en vient sans fin :
  les fenêtres vides sont retirées à chaque passage, et au-delà d'un
  plafond de clés, les plus anciennes partent. La mémoire ne grandit pas
  avec le trafic d'une journée.
- **Aucune adresse n'est conservée** hors de ce compteur : ni journal, ni
  base. La clé ne vit que le temps de sa fenêtre.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Final

from fastapi import Request

#: Au-delà, les origines les plus anciennes sont oubliées : la mémoire d'un
#: processus ne doit pas suivre le nombre d'adresses vues dans la journée.
MAX_TRACKED_KEYS: Final = 10_000


class SlidingWindowLimiter:
    """Compte les passages d'une clé sur une fenêtre glissante."""

    def __init__(
        self,
        *,
        max_hits: int,
        window_seconds: int,
        monotonic: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_TRACKED_KEYS,
    ) -> None:
        if max_hits < 1 or window_seconds < 1:
            msg = "max_hits et window_seconds doivent être strictement positifs."
            raise ValueError(msg)
        self._max = max_hits
        self._window = window_seconds
        self._monotonic = monotonic
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> int | None:
        """Consigne un passage. Rend `None` s'il est permis, sinon les secondes à attendre.

        Un passage refusé n'est **pas** compté : attendre le délai annoncé
        suffit, on ne repousse pas l'échéance de qui a insisté.
        """
        now = self._monotonic()
        window = self._hits.get(key)
        if window is None:
            self._evict_if_full()
            window = deque()
            self._hits[key] = window
        while window and now - window[0] > self._window:
            window.popleft()
        if len(window) >= self._max:
            return int(self._window - (now - window[0])) + 1
        window.append(now)
        return None

    def _evict_if_full(self) -> None:
        if len(self._hits) < self._max_keys:
            return
        now = self._monotonic()
        for key in [k for k, w in self._hits.items() if not w or now - w[-1] > self._window]:
            del self._hits[key]
        while len(self._hits) >= self._max_keys:
            del self._hits[next(iter(self._hits))]

    @property
    def tracked_keys(self) -> int:
        """Combien d'origines sont suivies en ce moment (pour les tests et le diagnostic)."""
        return len(self._hits)


def client_ip(request: Request) -> str:
    """L'origine d'une requête : le premier `X-Forwarded-For`, sinon l'adresse du client.

    Même lecture que le contexte de requête d'`identity` : derrière le
    mandataire de Dokploy, l'adresse du client est dans l'en-tête.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else ""
