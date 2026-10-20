"""Fabrique du stockage objet.

C'est le **seul** endroit de la base de code où le nom d'un fournisseur
apparaît en dehors du catalogue, et c'est un invariant à surveiller en
revue :

    grep -ri "provider is\\|provider ==" src/ → factory.py uniquement

Si un second aiguillage par fournisseur apparaît ailleurs un jour, la
promesse « changer de fournisseur = changer une variable » est perdue, et
elle le sera silencieusement.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

from karria.shared.config import Provider, Settings
from karria.shared.storage.memory import InMemoryObjectStorage
from karria.shared.storage.ports import ObjectStorage
from karria.shared.storage.s3 import S3ObjectStorage


async def build_object_storage(settings: Settings, stack: AsyncExitStack) -> ObjectStorage:
    """Retourne l'implémentation correspondant à la configuration.

    Args:
        settings: Configuration complète de l'application.
        stack: Pile de fermeture détenue par le cycle de vie. Le client S3
            y est enregistré et sera refermé à l'arrêt du processus.
    """
    environment = settings.app.environment.value
    if settings.storage.provider is Provider.MEMORY:
        return InMemoryObjectStorage(settings.storage, environment)
    return await S3ObjectStorage.open(settings.storage, environment, stack)
