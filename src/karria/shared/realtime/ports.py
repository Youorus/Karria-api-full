"""Le port des réveils : publier un sujet, attendre qu'un sujet bouge."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol


class Subscription(Protocol):
    """Un abonnement ouvert à un sujet."""

    async def wait(self, at_most: float) -> bool:
        """Attend un réveil, au plus `at_most` secondes.

        Rend `True` si le sujet a bougé, `False` si le délai est écoulé.
        Dans les deux cas l'appelant relit la base : le délai n'est pas un
        échec, c'est le repli qui rend l'ensemble robuste à un réveil perdu.
        Plusieurs réveils arrivés pendant une lecture n'en font qu'un.
        """
        ...


class Broker(Protocol):
    """Port : réveiller qui écoute un sujet, dans tous les processus."""

    async def publish(self, topic: str) -> None:
        """Annonce que ce sujet a bougé. Ne lève pas si personne n'écoute."""
        ...

    def subscribe(self, topic: str) -> AbstractAsyncContextManager[Subscription]:
        """Ouvre un abonnement, refermé en sortie de bloc."""
        ...

    async def close(self) -> None:
        """Libère les ressources (connexion d'écoute)."""
        ...
