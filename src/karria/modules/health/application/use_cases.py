"""Cas d'usage du module `health`.

Couche `application` — un use case = une intention métier
---------------------------------------------------------
Un use case orchestre : il appelle des ports, applique les règles du
domaine, renvoie un résultat. Il ne contient ni SQL, ni HTTP, ni appel
réseau direct. Ses dépendances arrivent par le constructeur, jamais par
import — c'est ce qui le rend substituable en test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from karria.modules.health.application.ports import HealthProbe
from karria.modules.health.domain import ComponentHealth, SystemHealth
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Une sonde qui ne répond pas est une sonde en échec. Sans délai maximal,
#: une base injoignable ferait pendre la requête de santé jusqu'au timeout
#: du client — et l'orchestrateur conclurait que l'instance est vivante.
PROBE_TIMEOUT_SECONDS = 2.0


class CheckSystemHealth:
    """Interroge toutes les sondes et agrège leur résultat."""

    def __init__(
        self, probes: Sequence[HealthProbe], *, timeout: float = PROBE_TIMEOUT_SECONDS
    ) -> None:
        self._probes = probes
        self._timeout = timeout

    async def execute(self) -> SystemHealth:
        """Retourne l'état de santé courant du système.

        Les sondes sont interrogées **en parallèle** : leurs latences
        s'additionneraient sinon, et une sonde de santé lente est une sonde
        de santé inutile.
        """
        results = await asyncio.gather(*(self._probe_safely(p) for p in self._probes))
        return SystemHealth(components=tuple(results))

    async def _probe_safely(self, probe: HealthProbe) -> ComponentHealth:
        """Exécute une sonde en garantissant qu'elle rend toujours un verdict.

        Une sonde qui lève ou qui expire ne doit jamais faire échouer
        l'endpoint de santé : ce serait remplacer un diagnostic par une
        absence de diagnostic, au pire moment.
        """
        try:
            async with asyncio.timeout(self._timeout):
                return await probe.check()
        except TimeoutError:
            logger.warning("health.probe_timeout", component=probe.name)
            return ComponentHealth.unhealthy(
                probe.name, f"Aucune réponse en {self._timeout} s.", critical=probe.critical
            )
        except Exception as exc:
            logger.exception("health.probe_failed", component=probe.name)
            return ComponentHealth.unhealthy(
                probe.name, type(exc).__name__, critical=probe.critical
            )
