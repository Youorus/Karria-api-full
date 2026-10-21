"""Module `identity` — comptes et authentification.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module. Tout ce qui n'y est
pas exporté est privé (`make boundaries` l'impose).

Ce qu'il expose, et pour qui :

- `MODULE` — la déclaration auprès du registre (`bootstrap/modules.py`) ;
- `ResolveSession`, `AuthenticatedCaller`, `get_resolve_session` — ce que
  `bootstrap/wiring.py` branche sur `resolve_principal` (ADR-0007). Aucun
  autre module n'en a l'usage : ils lisent le `Principal` de `shared/auth`;
- `UserRegistered`, `UserSignedIn`, `AccountDeleted` — les faits publiés
  sur le bus, auxquels un autre module s'abonne via `bootstrap` ;
- `MainDeviceLookup`, `MainDeviceSnapshot`, `get_main_device_lookup` — le
  port « sur quoi tournait l'app de cette personne ? », que
  `bootstrap/wiring.py` branche sur le module `devices` (ADR-0015).
"""

from karria.modules.identity.api import router
from karria.modules.identity.api.dependencies import (
    get_main_device_lookup,
    get_resolve_session,
)
from karria.modules.identity.application import (
    AccountDeleted,
    AuthenticatedCaller,
    MainDeviceLookup,
    MainDeviceSnapshot,
    ResolveSession,
    UserRegistered,
    UserSignedIn,
)
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="identity",
    router=router,
    models_module="karria.modules.identity.infrastructure.models",
)

__all__ = [
    "MODULE",
    "AccountDeleted",
    "AuthenticatedCaller",
    "MainDeviceLookup",
    "MainDeviceSnapshot",
    "ResolveSession",
    "UserRegistered",
    "UserSignedIn",
    "get_main_device_lookup",
    "get_resolve_session",
]
