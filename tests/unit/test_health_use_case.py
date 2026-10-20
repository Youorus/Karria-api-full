"""Cas d'usage `CheckSystemHealth`.

Les sondes sont remplacées par des doublures de quelques lignes : c'est le
bénéfice direct du port `HealthProbe`. Aucun PostgreSQL n'est requis pour
vérifier que le use case agrège correctement, résiste à une sonde qui lève
et coupe une sonde qui traîne.
"""

from __future__ import annotations

import asyncio

import pytest

from karria.modules.health.application import CheckSystemHealth
from karria.modules.health.domain import ComponentHealth, HealthStatus

pytestmark = pytest.mark.unit


class FakeProbe:
    """Doublure du port `HealthProbe`.

    N'hérite de rien : le `Protocol` est satisfait structurellement.
    """

    def __init__(
        self,
        name: str,
        *,
        healthy: bool = True,
        critical: bool = True,
        raises: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self.critical = critical
        self._healthy = healthy
        self._raises = raises
        self._delay = delay

    async def check(self) -> ComponentHealth:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        if self._healthy:
            return ComponentHealth.healthy(self.name, critical=self.critical)
        return ComponentHealth.unhealthy(self.name, "ko", critical=self.critical)


async def test_agrege_letat_de_toutes_les_sondes() -> None:
    use_case = CheckSystemHealth(probes=[FakeProbe("db"), FakeProbe("cache")])

    health = await use_case.execute()

    assert health.status is HealthStatus.HEALTHY
    assert [c.name for c in health.components] == ["db", "cache"]


async def test_une_sonde_qui_leve_est_rapportee_comme_defaillante() -> None:
    """L'endpoint de santé ne doit jamais échouer : il doit diagnostiquer."""
    use_case = CheckSystemHealth(probes=[FakeProbe("db", raises=RuntimeError("boum"))])

    health = await use_case.execute()

    assert health.status is HealthStatus.UNHEALTHY
    assert health.components[0].detail == "RuntimeError"


async def test_une_sonde_trop_lente_est_coupee() -> None:
    """Sans délai maximal, une base injoignable ferait pendre la sonde."""
    use_case = CheckSystemHealth(probes=[FakeProbe("db", delay=1.0)], timeout=0.01)

    health = await use_case.execute()

    assert health.status is HealthStatus.UNHEALTHY
    assert health.components[0].detail is not None
    assert "0.01" in health.components[0].detail


async def test_les_sondes_sont_interrogees_en_parallele() -> None:
    """Trois sondes de 50 ms doivent coûter 50 ms, pas 150 ms."""
    probes = [FakeProbe(f"dep-{i}", delay=0.05) for i in range(3)]
    use_case = CheckSystemHealth(probes=probes, timeout=1.0)

    started = asyncio.get_running_loop().time()
    await use_case.execute()
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.12
