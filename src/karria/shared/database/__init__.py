"""API publique de la couche persistance."""

from karria.shared.database.base import BaseDBModel
from karria.shared.database.config import (
    AERICH_MODELS,
    DEFAULT_CONNECTION,
    build_tortoise_config,
)
from karria.shared.database.engine import check_health, connect, disconnect
from karria.shared.database.repository import TortoiseRepository
from karria.shared.database.unit_of_work import (
    NullUnitOfWork,
    TortoiseUnitOfWork,
    UnitOfWork,
)

__all__ = [
    "AERICH_MODELS",
    "DEFAULT_CONNECTION",
    "BaseDBModel",
    "NullUnitOfWork",
    "TortoiseRepository",
    "TortoiseUnitOfWork",
    "UnitOfWork",
    "build_tortoise_config",
    "check_health",
    "connect",
    "disconnect",
]
