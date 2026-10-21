"""Module `devices` — appareils et notifications push.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module.

Ce qu'il expose, et pour qui :

- `MODULE` — la déclaration auprès du registre ;
- `SendPushToUser`, `PushDispatchSummary`, `get_send_push_to_user` — le
  service d'envoi à un compte. Un autre module ne l'importe **pas** : il
  déclare un port (« prévenir cette personne ») et `bootstrap/wiring.py`
  y branche `get_send_push_to_user` (règle 4) ;
- `CheckPushReceipts`, `get_check_push_receipts` — pour la commande
  planifiée `scripts/push_receipts.py` ;
- `GetMainDevice`, `MainDeviceSummary`, `get_get_main_device` — l'appareil
  principal d'un compte, que `bootstrap/wiring.py` branche sur le port du
  module des comptes pour la trace de départ (ADR-0015) ;
- `ForgetUserDevices`, `build_forget_user_devices` — la réaction à la
  suppression d'un compte, abonnée au bus par `bootstrap/wiring.py`.
"""

from karria.modules.devices.api import router
from karria.modules.devices.api.dependencies import (
    build_forget_user_devices,
    get_check_push_receipts,
    get_get_main_device,
    get_send_push_to_user,
)
from karria.modules.devices.application import (
    CheckPushReceipts,
    ForgetUserDevices,
    GetMainDevice,
    MainDeviceSummary,
    PushDispatchSummary,
    SendPushToUser,
)
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="devices",
    router=router,
    models_module="karria.modules.devices.infrastructure.models",
)

__all__ = [
    "MODULE",
    "CheckPushReceipts",
    "ForgetUserDevices",
    "GetMainDevice",
    "MainDeviceSummary",
    "PushDispatchSummary",
    "SendPushToUser",
    "build_forget_user_devices",
    "get_check_push_receipts",
    "get_get_main_device",
    "get_send_push_to_user",
]
