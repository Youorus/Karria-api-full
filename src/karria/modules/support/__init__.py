"""Module `support` — « Parler à l'équipe » : la messagerie entre une personne et l'équipe Karria.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module.

Ce qu'il expose, et pour qui :

- `MODULE` — la déclaration auprès du registre ;
- `UserNotifier`, `get_user_notifier` — le port « prévenir cette personne »,
  que `bootstrap/wiring.py` branche sur `devices.SendPushToUser` (règle 4) ;
- `AccountDirectory`, `get_account_directory` — le port « l'adresse de ce
  compte », branché sur `identity.GetCurrentUser` pour la liste de l'équipe ;
- `ForgetUserSupport`, `build_forget_user` — la réaction à la suppression
  d'un compte, abonnée au bus par `bootstrap/wiring.py`.
"""

from karria.modules.support.api import router
from karria.modules.support.api.dependencies import (
    build_forget_user,
    get_account_directory,
    get_user_notifier,
)
from karria.modules.support.application import AccountDirectory, ForgetUserSupport, UserNotifier
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="support",
    router=router,
    models_module="karria.modules.support.infrastructure.models",
)

__all__ = [
    "MODULE",
    "AccountDirectory",
    "ForgetUserSupport",
    "UserNotifier",
    "build_forget_user",
    "get_account_directory",
    "get_user_notifier",
]
