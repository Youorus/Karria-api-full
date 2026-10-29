"""Module `payments` — encaissements Stripe : « sans pub », cafés, modèles de CV Premium (ADR-0023, 0024).

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module (règle 3).

- `MODULE` — la déclaration auprès du registre ;
- `AccountDirectory`, `get_account_directory` — le port « quelle est l'adresse
  de ce compte ? », que `bootstrap/wiring.py` branche sur `identity` ;
- `TemplateStore`, `PremiumOffer`, `get_template_store` — le port « quels
  modèles sont en vente, qui les possède, remets celui-ci », branché sur
  `documents` ;
- `build_forget_user` — la réaction à la suppression d'un compte (ADR-0015).
"""

from karria.modules.payments.api.dependencies import (
    build_forget_user,
    get_account_directory,
    get_template_store,
)
from karria.modules.payments.api.routers import router
from karria.modules.payments.application import AccountDirectory, ForgetUser, TemplateStore
from karria.modules.payments.domain import PremiumOffer
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="payments",
    router=router,
    models_module="karria.modules.payments.infrastructure.models",
)

__all__ = [
    "MODULE",
    "AccountDirectory",
    "ForgetUser",
    "PremiumOffer",
    "TemplateStore",
    "build_forget_user",
    "get_account_directory",
    "get_template_store",
]
