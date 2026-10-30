"""Le catalogue des fournisseurs d'IA, lu dans le routage configuré (ADR-0025).

L'information donnée à la personne nomme les fournisseurs qui reçoivent
**réellement** ses données : ceux que `LLM__TASKS__*` et
`LLM__FALLBACKS__*` désignent pour les tâches concernées, et qui ont de
quoi être appelés. Une route vers un fournisseur sans clé n'envoie rien
— en environnement déployé, elle a d'ailleurs empêché le démarrage.

Lire la configuration est un geste d'infrastructure : le domaine ne sait
pas qu'une variable d'environnement existe.
"""

from __future__ import annotations

from collections.abc import Sequence

from karria.shared.config import LLM_PROVIDERS, Settings


class SettingsAiProviderCatalog:
    """Implémente `AiProviderCatalog` sur `Settings.llm`."""

    def __init__(self, settings: Settings) -> None:
        self._llm = settings.llm

    def providers_for(self, tasks: Sequence[str]) -> frozenset[str]:
        """Les fournisseurs appelables derrière ces tâches, repli compris."""
        found: set[str] = set()
        routes = self._llm.tasks.model_dump()
        fallbacks = self._llm.fallbacks.model_dump()
        for task in tasks:
            for value in (routes.get(task, ""), fallbacks.get(task, "")):
                provider = value.strip().partition(":")[0].strip().lower()
                if provider in LLM_PROVIDERS and self._configured(provider):
                    found.add(provider)
        return frozenset(found)

    def _configured(self, provider: str) -> bool:
        return bool(getattr(self._llm.providers, provider).is_configured(provider))
