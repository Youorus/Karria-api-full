"""Règles de santé du système.

Couche `domain` — la plus stricte du projet
-------------------------------------------
Ce fichier n'importe **que la bibliothèque standard**. Ni FastAPI, ni
Tortoise, ni `shared.config`, ni `shared.database`. C'est vérifié en CI.

La raison n'est pas dogmatique : c'est ce qui rend la règle d'agrégation
ci-dessous testable en une milliseconde, sans base de données, sans réseau
et sans application. Un test qui a besoin de PostgreSQL pour vérifier une
règle de trois lignes est un test que personne ne lancera.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HealthStatus(StrEnum):
    """État de santé d'un composant ou de l'ensemble du système."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """Santé d'une dépendance unique.

    Attributes:
        name: Nom de la dépendance tel qu'il apparaîtra dans la réponse.
        status: État constaté.
        critical: Si vrai, la défaillance de ce composant rend le système
            inapte à servir. Sinon elle le dégrade seulement — un cache
            indisponible ralentit l'API, il ne l'empêche pas de répondre.
        detail: Précision facultative destinée à l'exploitation.
    """

    name: str
    status: HealthStatus
    critical: bool = True
    detail: str | None = None

    @classmethod
    def healthy(cls, name: str, *, critical: bool = True) -> ComponentHealth:
        """Raccourci pour un composant en bon état."""
        return cls(name=name, status=HealthStatus.HEALTHY, critical=critical)

    @classmethod
    def unhealthy(cls, name: str, detail: str, *, critical: bool = True) -> ComponentHealth:
        """Raccourci pour un composant défaillant."""
        return cls(name=name, status=HealthStatus.UNHEALTHY, critical=critical, detail=detail)


@dataclass(frozen=True, slots=True)
class SystemHealth:
    """Santé globale, dérivée de celle de ses composants."""

    components: tuple[ComponentHealth, ...]

    @property
    def status(self) -> HealthStatus:
        """Agrège l'état des composants en un état unique.

        La règle, et sa justification :

        - un composant **critique** défaillant rend le système `UNHEALTHY` ;
        - un composant **non critique** défaillant, ou n'importe quel
          composant dégradé, rend le système `DEGRADED` ;
        - sinon le système est `HEALTHY`.

        La distinction critique / non critique est ce qui évite de retirer
        du trafic à une instance parfaitement capable de répondre au seul
        motif qu'une dépendance accessoire est tombée.
        """
        if any(c.critical and c.status is HealthStatus.UNHEALTHY for c in self.components):
            return HealthStatus.UNHEALTHY
        if any(c.status is not HealthStatus.HEALTHY for c in self.components):
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    @property
    def can_serve_traffic(self) -> bool:
        """Indique si l'instance doit continuer à recevoir du trafic.

        Un système dégradé sert encore : c'est précisément la différence
        entre « dégradé » et « hors service ».
        """
        return self.status is not HealthStatus.UNHEALTHY
