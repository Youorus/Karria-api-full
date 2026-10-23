"""Module `offers` — le corpus d'offres, la recherche, le fil, les alertes.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module.

Ce qu'il expose, et pour qui :

- `MODULE` — la déclaration auprès du registre ;
- `UserNotifier`, `get_user_notifier` — le port « prévenir cette
  personne », que `bootstrap` branche sur `devices` (règle 4) ;
- `build_forget_user` — la réaction à la suppression d'un compte, abonnée
  au bus par `bootstrap/wiring.py` (ADR-0015) ;
- `register_tasks` — les travaux de la file durable, inscrits par
  `bootstrap/worker.py` ;
- `build_*`, `ensure_indexes` — pour les commandes `scripts/offers.py` ;
- `SearchCriteria`, `RawPosting` — les valeurs que ces commandes manipulent.
"""

from karria.modules.offers.api import router
from karria.modules.offers.api.dependencies import (
    build_apply_criteria,
    build_expire_offers,
    build_forget_user,
    build_ingest_batch,
    build_ingest_postings,
    build_lease_targets,
    build_reconcile_index,
    build_refresh_targets,
    build_search_offers,
    build_sync_index,
    ensure_indexes,
    get_user_notifier,
)
from karria.modules.offers.api.tasks import register_tasks
from karria.modules.offers.application import UserNotifier
from karria.modules.offers.domain import RawPosting, SearchCriteria
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(
    name="offers",
    router=router,
    models_module="karria.modules.offers.infrastructure.models",
)

__all__ = [
    "MODULE",
    "RawPosting",
    "SearchCriteria",
    "UserNotifier",
    "build_apply_criteria",
    "build_expire_offers",
    "build_forget_user",
    "build_ingest_batch",
    "build_ingest_postings",
    "build_lease_targets",
    "build_reconcile_index",
    "build_refresh_targets",
    "build_search_offers",
    "build_sync_index",
    "ensure_indexes",
    "get_user_notifier",
    "register_tasks",
]
