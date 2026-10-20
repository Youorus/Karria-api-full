"""Ports du module `health`.

Un **port** est une interface déclarée par la couche qui a besoin du
service, et non par celle qui le rend. C'est l'inversion de dépendance :
`application` dit ce dont elle a besoin, `infrastructure` s'y conforme.

Concrètement, c'est ce qui permet au use case ci-contre d'être testé avec
une sonde factice écrite en trois lignes, sans PostgreSQL. La V1 ne
pouvait pas le faire : ses services appelaient l'ORM directement.

On utilise `Protocol` plutôt qu'une classe abstraite : l'implémentation n'a
rien à hériter, donc rien à importer depuis ce module. Le découplage va
dans les deux sens.
"""

from __future__ import annotations

from typing import Protocol

from karria.modules.health.domain import ComponentHealth


class HealthProbe(Protocol):
    """Sonde vérifiant l'état d'une dépendance externe."""

    @property
    def name(self) -> str:
        """Nom du composant sondé."""
        ...

    @property
    def critical(self) -> bool:
        """Indique si la défaillance du composant empêche de servir."""
        ...

    async def check(self) -> ComponentHealth:
        """Interroge la dépendance et rapporte son état."""
        ...
