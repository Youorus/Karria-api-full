"""Construction de la configuration Tortoise.

Ce module fournit un **constructeur**, pas une constante. La liste des
modules de modèles n'est pas connue ici : elle appartient au registre de
modules (`karria.bootstrap.modules`), et `shared/` n'a pas le droit de
dépendre du métier. C'est l'appelant — la couche d'assemblage — qui fournit
la liste. L'inversion de dépendance vaut aussi pour la configuration.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from karria.shared.config import DatabaseSettings

#: Aerich stocke l'historique des migrations dans ses propres tables : sans
#: cette entrée, `aerich upgrade` ne sait pas où il en est.
AERICH_MODELS = "aerich.models"

DEFAULT_CONNECTION = "default"
MODELS_APP = "models"


def _with_pool_bounds(url: str, settings: DatabaseSettings) -> str:
    """Injecte les bornes du pool dans l'URL, sans écraser un réglage explicite."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.setdefault("minsize", str(settings.min_pool_size))
    query.setdefault("maxsize", str(settings.max_pool_size))
    return urlunsplit(parts._replace(query=urlencode(query)))


def build_tortoise_config(
    settings: DatabaseSettings, model_modules: Sequence[str]
) -> dict[str, Any]:
    """Assemble la configuration Tortoise à partir des réglages et des modèles.

    Args:
        settings: Réglages de connexion issus de la configuration.
        model_modules: Chemins pointés des modules déclarant des modèles,
            fournis par le registre de modules.

    Returns:
        Le dictionnaire de configuration attendu par Tortoise et par Aerich.
    """
    return {
        "connections": {DEFAULT_CONNECTION: _with_pool_bounds(settings.url, settings)},
        "apps": {
            MODELS_APP: {
                "models": [*model_modules, AERICH_MODELS],
                "default_connection": DEFAULT_CONNECTION,
            }
        },
        # Tout instant est stocké et manipulé en UTC, sans exception. La
        # conversion vers un fuseau local est une préoccupation d'affichage,
        # donc du client — jamais de la base.
        "use_tz": True,
        "timezone": "UTC",
    }
