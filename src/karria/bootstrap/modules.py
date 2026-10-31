"""Registre des modules — la seule liste des modules de l'application.

    ┌──────────────────────────────────────────────────────────────┐
    │  AJOUTER UN MODULE = AJOUTER UNE LIGNE DANS `ENABLED_MODULES` │
    └──────────────────────────────────────────────────────────────┘

Ce registre alimente **tout** ce qui dépend de la liste des modules :
les routes HTTP, la liste des modèles transmise à Aerich et les
abonnements au bus d'événements. Une seule source de vérité, donc aucune
divergence possible entre une route exposée et une table migrée.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from karria.modules.cv import MODULE as CV_MODULE
from karria.modules.devices import MODULE as DEVICES_MODULE
from karria.modules.documents import MODULE as DOCUMENTS_MODULE
from karria.modules.health import MODULE as HEALTH_MODULE
from karria.modules.identity import MODULE as IDENTITY_MODULE
from karria.modules.offers import MODULE as OFFERS_MODULE
from karria.modules.payments import MODULE as PAYMENTS_MODULE
from karria.modules.support import MODULE as SUPPORT_MODULE

if TYPE_CHECKING:
    from karria.shared.events import EventBus
    from karria.shared.module import ModuleDescriptor


#: Modules composant l'application, dans leur ordre d'enregistrement.
ENABLED_MODULES: Final[tuple[ModuleDescriptor, ...]] = (
    HEALTH_MODULE,
    IDENTITY_MODULE,
    DEVICES_MODULE,
    CV_MODULE,
    OFFERS_MODULE,
    DOCUMENTS_MODULE,
    PAYMENTS_MODULE,
    SUPPORT_MODULE,
)


def all_model_modules() -> list[str]:
    """Retourne les chemins pointés des modules de modèles, pour Aerich."""
    return [module.models_module for module in ENABLED_MODULES if module.models_module]


def register_event_subscriptions(bus: EventBus) -> None:
    """Laisse chaque module s'abonner aux événements qui l'intéressent."""
    for module in ENABLED_MODULES:
        if module.subscribe_events is not None:
            module.subscribe_events(bus)


def all_permissions() -> tuple[str, ...]:
    """Inventaire complet des permissions déclarées par les modules.

    Sert à accorder « toutes les permissions » au premier administrateur,
    et à vérifier qu'une permission configurée existe bien. Assemblé
    **ici** parce que c'est la seule couche qui voit tous les modules :
    ailleurs, il faudrait qu'un module en importe un autre (règle 4).
    """
    return tuple(permission for module in ENABLED_MODULES for permission in module.permissions)
