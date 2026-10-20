"""Règle d'agrégation de santé.

Démonstration de ce que l'architecture achète : ces tests exercent une
règle métier réelle **sans base de données, sans application, sans réseau**.
Ils s'exécutent en quelques millisecondes. C'est exactement ce que la V1
ne pouvait pas faire, ses règles étant enchevêtrées avec l'ORM.
"""

from __future__ import annotations

import pytest

from karria.modules.health.domain import ComponentHealth, HealthStatus, SystemHealth

pytestmark = pytest.mark.unit


def test_tout_va_bien() -> None:
    health = SystemHealth(
        components=(ComponentHealth.healthy("db"), ComponentHealth.healthy("cache"))
    )

    assert health.status is HealthStatus.HEALTHY
    assert health.can_serve_traffic


def test_un_composant_critique_defaillant_rend_le_systeme_hors_service() -> None:
    health = SystemHealth(
        components=(
            ComponentHealth.unhealthy("db", "injoignable", critical=True),
            ComponentHealth.healthy("cache", critical=False),
        )
    )

    assert health.status is HealthStatus.UNHEALTHY
    assert not health.can_serve_traffic


def test_un_composant_non_critique_defaillant_degrade_sans_retirer_du_trafic() -> None:
    """Le cœur de la règle : une panne accessoire ne doit pas sortir l'instance.

    Sans cette distinction, un cache indisponible retirerait du service des
    instances parfaitement capables de répondre — en transformant un
    ralentissement en panne complète.
    """
    health = SystemHealth(
        components=(
            ComponentHealth.healthy("db", critical=True),
            ComponentHealth.unhealthy("cache", "timeout", critical=False),
        )
    )

    assert health.status is HealthStatus.DEGRADED
    assert health.can_serve_traffic


def test_un_systeme_sans_composant_est_sain() -> None:
    assert SystemHealth(components=()).status is HealthStatus.HEALTHY
