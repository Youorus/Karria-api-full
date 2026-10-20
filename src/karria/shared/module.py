"""Contrat d'un module métier.

Chaque module se **déclare lui-même** en exposant un `ModuleDescriptor`
depuis son `__init__.py`. La couche d'assemblage se contente de lister ces
descripteurs : elle n'a jamais besoin de savoir comment un module est
structuré à l'intérieur.

Conséquence directe, et objectif principal : `main.py` ne référence aucun
module. Là où la V1 exigeait de modifier son point d'entrée à chaque
ajout — cinquante lignes d'imports et quarante-et-un `include_router` —
il suffit ici d'ajouter **une ligne au registre**.

Pourquoi un registre explicite plutôt qu'une découverte automatique ?
--------------------------------------------------------------------
Parcourir `modules/` avec `pkgutil` supprimerait aussi cette ligne, mais au
prix d'un chargement implicite : plus aucun outil statique ne saurait quels
modules composent l'application, l'ordre d'import deviendrait imprévisible,
et un dossier laissé par erreur serait exposé en production. Une ligne
visible vaut mieux qu'une magie invisible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

    from karria.shared.events import EventBus


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    """Tout ce que l'application doit savoir d'un module métier.

    Attributes:
        name: Identifiant technique du module, utilisé dans les journaux.
        router: Routes HTTP exposées. `None` pour un module sans API (un
            consommateur d'événements ou une tâche de fond, par exemple).
        models_module: Chemin pointé du module déclarant les modèles
            Tortoise. Alimente Aerich : c'est ce qui garantit qu'un module
            enregistré et ses tables ne peuvent pas diverger.
        subscribe_events: Point d'accroche appelé au démarrage pour abonner
            le module au bus. C'est la voie normale pour réagir à ce qui se
            passe ailleurs, sans importer l'autre module.
        permissions: Permissions que ce module fait respecter, sous la forme
            `<ressource>:<action>`. Déclarées ici pour que l'assemblage
            connaisse l'inventaire complet **sans qu'aucun module n'ait à
            en importer un autre** (règle 4) : c'est ce qui permet
            d'accorder « toutes les permissions » au premier administrateur
            sans qu'`identity` ait à lire `travel`.
        use_api_prefix: Place les routes sous le préfixe versionné de l'API.
            Passer à `False` pour les endpoints d'exploitation (sondes de
            santé, métriques) : ils s'adressent à l'infrastructure, pas aux
            clients du produit, et ne suivent donc pas son versionnement.
    """

    name: str
    router: APIRouter | None = None
    models_module: str | None = None
    subscribe_events: Callable[[EventBus], None] | None = None
    permissions: tuple[str, ...] = ()
    use_api_prefix: bool = True
