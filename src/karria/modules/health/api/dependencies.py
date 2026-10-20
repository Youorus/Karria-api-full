"""Câblage des dépendances du module `health`.

C'est ici — et uniquement ici — que les implémentations concrètes sont
associées aux ports. Le use case, lui, n'a jamais entendu parler de
`DatabaseProbe`.

Ce fichier est la couture que la V1 n'avait pas : un test remplace ce
fournisseur par `app.dependency_overrides` et exerce la route entière avec
des sondes factices, sans base de données.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from karria.modules.health.application import CheckSystemHealth
from karria.modules.health.infrastructure import DatabaseProbe, ObjectStorageProbe
from karria.shared.http.dependencies import ObjectStorageDep


def get_check_system_health(storage: ObjectStorageDep) -> CheckSystemHealth:
    """Assemble le cas d'usage avec les sondes réellement déployées."""
    return CheckSystemHealth(probes=[DatabaseProbe(), ObjectStorageProbe(storage)])


CheckSystemHealthDep = Annotated[CheckSystemHealth, Depends(get_check_system_health)]
