"""Schémas HTTP du module `health`.

Ces schémas décrivent le **contrat public** de l'API. Ils sont distincts
des objets du domaine, et cette duplication apparente est délibérée :
renommer un champ interne ne doit pas casser silencieusement les clients.
La frontière rend le changement cassant visible, à l'endroit où il l'est.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from karria.modules.health.domain import HealthStatus, SystemHealth


class ComponentHealthResponse(BaseModel):
    """État d'une dépendance."""

    name: str
    status: HealthStatus
    critical: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    """État de santé global de l'instance."""

    status: HealthStatus = Field(description="État agrégé du système.")
    components: list[ComponentHealthResponse]

    @classmethod
    def from_domain(cls, health: SystemHealth) -> HealthResponse:
        """Projette l'objet de domaine sur le contrat HTTP."""
        return cls(
            status=health.status,
            components=[
                ComponentHealthResponse(
                    name=component.name,
                    status=component.status,
                    critical=component.critical,
                    detail=component.detail,
                )
                for component in health.components
            ],
        )


class LivenessResponse(BaseModel):
    """Réponse de la sonde de vivacité."""

    status: str = "alive"
