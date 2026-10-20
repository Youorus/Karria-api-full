"""Sondes concrètes du module `health`.

Couche `infrastructure` — l'endroit où le monde extérieur entre
--------------------------------------------------------------
C'est la seule couche autorisée à connaître PostgreSQL, un SDK tiers ou un
protocole réseau. Elle implémente les ports déclarés par `application`
sans rien en importer d'autre que leur définition.

Remarquer l'absence d'héritage : `DatabaseProbe` n'hérite pas de
`HealthProbe`. Le `Protocol` est satisfait structurellement, donc
vérifié par mypy sans créer de dépendance à l'exécution.
"""

from __future__ import annotations

from karria.modules.health.domain import ComponentHealth
from karria.shared.database import check_health
from karria.shared.errors import DependencyUnavailableError, KarriaError
from karria.shared.storage import ObjectStorage


class DatabaseProbe:
    """Vérifie que PostgreSQL répond réellement à une requête."""

    #: Sans base, l'API ne peut rien servir : la défaillance est critique.
    critical = True
    name = "postgresql"

    async def check(self) -> ComponentHealth:
        """Exécute une requête triviale et rapporte le résultat."""
        try:
            await check_health()
        except DependencyUnavailableError as exc:
            return ComponentHealth.unhealthy(self.name, exc.message, critical=self.critical)
        return ComponentHealth.healthy(self.name, critical=self.critical)


class ObjectStorageProbe:
    """Vérifie que les buckets déclarés répondent.

    **Non critique**, contrairement à la base : une API dont le stockage est
    indisponible sert encore l'essentiel — consultation, recherche,
    inscription. La retirer du répartiteur de charge transformerait une
    panne partielle en panne totale.

    Le démarrage, lui, applique la règle inverse : le `lifespan` échoue si
    un bucket manque, parce qu'un déploiement mal provisionné doit se voir
    au déploiement et non chez le premier utilisateur qui envoie un fichier.
    """

    critical = False
    name = "object_storage"

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    async def check(self) -> ComponentHealth:
        """Interroge le stockage et rapporte son état."""
        try:
            await self._storage.check_health()
        except KarriaError as exc:
            return ComponentHealth.unhealthy(self.name, exc.message, critical=self.critical)
        return ComponentHealth.healthy(self.name, critical=self.critical)
