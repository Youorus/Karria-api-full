"""Les travaux du module `applications`, inscrits sur la file durable.

| Travail | Quand | Pourquoi |
|---|---|---|
| `applications.adapt_cv` | quand l'adaptation dépasse les dix secondes de la requête de création | le modèle finit son travail hors requête ; l'app relit `GET /applications/{id}` jusqu'à `faite` ou `echouee` |
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from karria.modules.applications.api.dependencies import build_adapt_cv
from karria.modules.applications.application import CvValidator
from karria.modules.applications.infrastructure import TASK_ADAPT_CV
from karria.shared.config import get_settings
from karria.shared.queue import App


def register_tasks(app: App, *, validator: Callable[[], CvValidator]) -> None:
    """Inscrit les travaux d'`applications`.

    `validator` juge la forme d'un CV ; c'est le module `cv` qui sait, et
    `bootstrap/worker.py` le passe (règle 4).
    """

    @app.task(name=TASK_ADAPT_CV, retry=1)
    async def adapt_cv(dossier_id: str) -> None:
        await build_adapt_cv(get_settings(), validator).execute(UUID(dossier_id))
