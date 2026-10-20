"""Configuration Tortoise exposée à Aerich.

Aerich a besoin d'une constante à un emplacement fixe (déclaré dans
`pyproject.toml`). Elle vit ici, dans la couche d'assemblage, parce qu'elle
croise deux informations : la configuration technique — qui appartient à
`shared/` — et la liste des modules — qui appartient au registre. `shared/`
ne peut pas la construire seul sans dépendre du métier, ce qui violerait la
première règle d'architecture.

La liste des modèles étant **dérivée du registre**, il est structurellement
impossible d'enregistrer un module en oubliant de migrer ses tables.
"""

from __future__ import annotations

from typing import Any, Final

from karria.bootstrap.modules import all_model_modules
from karria.shared.config import get_settings
from karria.shared.database import build_tortoise_config

TORTOISE_ORM: Final[dict[str, Any]] = build_tortoise_config(get_settings().db, all_model_modules())
