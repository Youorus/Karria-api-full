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
- `UserNotifier`, `get_user_notifier` — le port « préviens cette personne sur
  son téléphone », branché sur `devices` (le merci d'un paiement, ADR-0024
  § 2026-11-05) ;
- `build_forget_user` — la réaction à la suppression d'un compte (ADR-0015) ;
- `build_thank_for_purchase`, `get_purchase_repository`, `get_webhook_ledger`,
  `ThankYou` — ce que `scripts/payments_thanks.py` assemble pour rejouer un merci.
"""

from karria.modules.payments.api.dependencies import (
    build_forget_user,
    build_thank_for_purchase,
    get_account_directory,
    get_purchase_repository,
    get_template_store,
    get_user_notifier,
    get_webhook_ledger,
)
from karria.modules.payments.api.routers import router
from karria.modules.payments.application import (
    AccountDirectory,
    ForgetUser,
    TemplateStore,
    ThankForPurchase,
    UserNotifier,
)
from karria.modules.payments.domain import PremiumOffer, ThankYou
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
    "ThankForPurchase",
    "ThankYou",
    "UserNotifier",
    "build_forget_user",
    "build_thank_for_purchase",
    "get_account_directory",
    "get_purchase_repository",
    "get_template_store",
    "get_user_notifier",
    "get_webhook_ledger",
]
