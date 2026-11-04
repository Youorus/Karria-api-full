"""Les travaux du module `cv`, inscrits sur la file durable.

| Travail | Quand | Pourquoi ce rythme |
|---|---|---|
| `cv.refresh_suggestions` | chaque nuit, 4 h 20 | le vocabulaire de l'index (intitulés, entreprises, villes) bouge à l'échelle des collectes ; une matérialisation par jour suffit, hors des heures d'usage et après l'expiration des offres (3 h 15) |

Le vocabulaire vient du module `offers` par le port `OfferVocabulary`, que
`bootstrap/worker.py` fournit (règle 4). Verrou d'exécution et de file :
deux matérialisations ne tournent ni n'attendent jamais ensemble.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from karria.modules.cv.api.dependencies import build_refresh_suggestions
from karria.modules.cv.application import OfferVocabulary, SuggestionRefreshReport
from karria.shared.queue import App

TASK_REFRESH_SUGGESTIONS: Final = "cv.refresh_suggestions"


def register_tasks(app: App, *, vocabulary: Callable[[], OfferVocabulary]) -> None:
    """Inscrit les travaux de `cv`. `vocabulary` vient de `bootstrap` (règle 4)."""

    @app.periodic(cron="20 4 * * *", periodic_id=TASK_REFRESH_SUGGESTIONS)
    @app.task(
        name=TASK_REFRESH_SUGGESTIONS,
        lock=TASK_REFRESH_SUGGESTIONS,
        queueing_lock=TASK_REFRESH_SUGGESTIONS,
    )
    async def refresh_suggestions(timestamp: int | None = None) -> None:  # noqa: ARG001 - périodique ou différé
        await run_refresh(vocabulary)


async def run_refresh(vocabulary: Callable[[], OfferVocabulary]) -> SuggestionRefreshReport:
    """Une matérialisation complète ; partagée par le worker et `scripts/suggestions.py`."""
    return await build_refresh_suggestions(vocabulary()).execute()
