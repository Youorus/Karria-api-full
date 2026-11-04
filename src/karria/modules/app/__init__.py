"""Module `app` — ce que l'application installée doit savoir d'elle-même.

API PUBLIQUE DU MODULE
======================
Ce fichier est la **seule** porte d'entrée du module (règle 3).

Aujourd'hui : la version minimale et la dernière version publiée de l'app,
par plateforme (`GET /api/v1/app/release`), pour que l'app sache dire
« mets-toi à jour » — ou l'imposer. Rien d'autre : ni compte, ni contenu.
Le module n'a ni persistance ni cas d'usage : une règle de domaine pure et
une route publique sous quota par origine.
"""

from karria.modules.app.api import router
from karria.modules.app.domain import ReleaseStatus, ReleaseVerdict, evaluate_release
from karria.shared.module import ModuleDescriptor

MODULE = ModuleDescriptor(name="app", router=router)

__all__ = ["MODULE", "ReleaseStatus", "ReleaseVerdict", "evaluate_release"]
