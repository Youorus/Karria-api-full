"""Routes HTTP du module `health`.

Couche `api` — mince par construction
--------------------------------------
Une route fait trois choses : recevoir, déléguer, projeter. Toute logique
qui apparaît ici est une logique qu'aucun test unitaire ne pourra atteindre
sans monter un serveur HTTP. Si une route dépasse une quinzaine de lignes,
c'est qu'un use case manque.

Vivacité et disponibilité : deux questions différentes
------------------------------------------------------
- `/health/live` — « ce processus est-il vivant ? » Ne touche à **aucune**
  dépendance. Si cette sonde consultait la base, une panne PostgreSQL
  provoquerait le redémarrage en boucle de toutes les instances : elles
  seraient déclarées mortes alors qu'elles vont parfaitement bien.
- `/health/ready` — « cette instance peut-elle servir du trafic ? »
  Interroge les dépendances. Un échec retire l'instance du répartiteur de
  charge, sans la tuer.

Confondre les deux transforme une panne de base en panne totale.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from karria.modules.health.api.dependencies import CheckSystemHealthDep
from karria.modules.health.api.schemas import HealthResponse, LivenessResponse

router = APIRouter(prefix="/health", tags=["Santé"])


@router.get(
    "/live",
    summary="Sonde de vivacité",
    response_model=LivenessResponse,
    status_code=status.HTTP_200_OK,
)
async def liveness() -> LivenessResponse:
    """Confirme que le processus répond. N'interroge aucune dépendance."""
    return LivenessResponse()


@router.get(
    "/ready",
    summary="Sonde de disponibilité",
    response_model=HealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthResponse,
            "description": "Une dépendance critique est indisponible.",
        }
    },
)
async def readiness(
    check_system_health: CheckSystemHealthDep, response: Response
) -> HealthResponse:
    """Rapporte l'état des dépendances et la capacité à servir du trafic."""
    health = await check_system_health.execute()
    if not health.can_serve_traffic:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse.from_domain(health)
