"""Les travaux du module `documents`, inscrits sur la file durable.

| Travail | Quand | Pourquoi |
|---|---|---|
| `documents.ingest_template` | au dépôt d'un PDF de modèle | analyse, rendu, mesure (≈ 20 à 40 s, CPU) : jamais dans une requête |
| `documents.explain_ats` | après chaque export | l'explication du score attend le modèle de langage (≈ 3 s) ; le score, lui, est déjà rendu |
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from uuid import UUID

from karria.modules.documents.api.dependencies import build_explain_ats, build_ingest_template
from karria.modules.documents.infrastructure.queue import TASK_EXPLAIN_ATS, TASK_INGEST_TEMPLATE
from karria.shared.config import get_settings
from karria.shared.queue import App
from karria.shared.storage import build_object_storage


def register_tasks(app: App) -> None:
    """Inscrit les travaux de `documents`."""

    @app.task(name=TASK_INGEST_TEMPLATE, retry=1)
    async def ingest_template(version_id: str) -> None:
        settings = get_settings()
        async with AsyncExitStack() as resources:
            storage = await build_object_storage(settings, resources)
            await build_ingest_template(settings, storage).execute(UUID(version_id))

    @app.task(name=TASK_EXPLAIN_ATS, retry=1)
    async def explain_ats(generated_id: str) -> None:
        await build_explain_ats(get_settings()).execute(UUID(generated_id))
