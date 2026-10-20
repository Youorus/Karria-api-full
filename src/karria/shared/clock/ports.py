"""Le port : ce dont le métier a besoin, sans dire comment l'obtenir."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Port : donne l'instant courant.

    Le temps est une dépendance externe comme une autre. L'injecter permet
    de vérifier qu'une suppression horodate bien `deleted_at`, sans figer
    l'horloge du processus ni tolérer un test qui échoue à minuit.
    """

    def now(self) -> datetime:
        """Retourne l'instant courant, en UTC."""
        ...
