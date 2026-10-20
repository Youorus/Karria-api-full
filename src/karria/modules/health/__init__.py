"""Module `health` — surveillance de l'état de l'instance.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module. Tout ce qui n'y est
pas exporté est privé, quelle que soit sa visibilité Python. Un import
comme `karria.modules.health.infrastructure.probes` depuis l'extérieur
fait échouer la CI (`make boundaries`).

Cette contrainte est le cœur de l'architecture : c'est elle qui permet de
réorganiser librement l'intérieur d'un module sans rien casser ailleurs.
"""

from karria.modules.health.api import router
from karria.modules.health.domain import ComponentHealth, HealthStatus, SystemHealth
from karria.shared.module import ModuleDescriptor

#: Déclaration du module auprès de l'application.
#: `use_api_prefix=False` : les sondes s'adressent à l'orchestrateur, pas
#: aux clients du produit, et ne suivent donc pas son versionnement.
MODULE = ModuleDescriptor(name="health", router=router, use_api_prefix=False)

__all__ = ["MODULE", "ComponentHealth", "HealthStatus", "SystemHealth"]
