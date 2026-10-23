"""Le routeur : une tâche → un fournisseur et un modèle, par configuration, avec repli.

Contrat
-------
- Les modules ne nomment jamais un fournisseur : ils disent la **tâche**
  (`LlmRequest.task`), et la configuration (`LLM__TASKS__*`) dit qui la
  sert. Changer de modèle ou de fournisseur est un geste de configuration,
  pas une livraison (ADR-0018).
- **Validé au démarrage** (`RoutingTable.validate`) : une route mal formée,
  un fournisseur sans clé, une tâche sans route — l'application refuse de
  démarrer en environnement déployé avec un message qui nomme la variable.
  En local, l'erreur est portée à l'appel (`LlmUnavailableError`), pour
  pouvoir lancer l'API sans aucune clé.
- **Repli facultatif** par tâche (`LLM__FALLBACKS__*`) : sur une panne du
  fournisseur principal (`LlmUnavailableError`) ou une capacité absente,
  la même requête part au repli, et le changement est journalisé. Un refus
  de contenu ou une sortie hors forme ne déclenche pas de repli : ce n'est
  pas une panne.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from karria.shared.llm.ports import (
    LanguageModel,
    LlmCapabilityError,
    LlmConfigurationError,
    LlmRequest,
    LlmResult,
    LlmUnavailableError,
    ModelRoute,
    Provider,
    ProviderAdapter,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RoutingTable:
    """Les routes par tâche, et leurs replis, telles que la configuration les dit."""

    routes: Mapping[str, ModelRoute]
    fallbacks: Mapping[str, ModelRoute] = field(default_factory=dict)

    @classmethod
    def parse(
        cls, routes: Mapping[str, str], fallbacks: Mapping[str, str] | None = None
    ) -> RoutingTable:
        """Lit des chaînes `fournisseur:modèle` ; une chaîne vide n'est pas une route.

        Raises:
            LlmConfigurationError: Une route mal formée, avec le nom de la tâche.
        """
        parsed: dict[str, ModelRoute] = {}
        for task, value in routes.items():
            if value.strip():
                try:
                    parsed[task] = ModelRoute.parse(value)
                except LlmConfigurationError as exc:
                    raise LlmConfigurationError(f"LLM__TASKS__{task.upper()} : {exc}") from exc
        replacements: dict[str, ModelRoute] = {}
        for task, value in (fallbacks or {}).items():
            if value.strip():
                try:
                    replacements[task] = ModelRoute.parse(value)
                except LlmConfigurationError as exc:
                    raise LlmConfigurationError(f"LLM__FALLBACKS__{task.upper()} : {exc}") from exc
        return cls(parsed, replacements)

    def providers(self) -> frozenset[Provider]:
        """Les fournisseurs que les routes nomment."""
        return frozenset(
            route.provider for route in [*self.routes.values(), *self.fallbacks.values()]
        )

    def validate(
        self, configured: Mapping[Provider, bool], required_tasks: Mapping[str, str]
    ) -> list[str]:
        """Les problèmes à dire au démarrage ; vide si tout est cohérent.

        Args:
            configured: Pour chaque fournisseur, a-t-il de quoi être appelé
                (clé, ou adresse pour un modèle local) ?
            required_tasks: Les tâches que les modules utilisent, et la variable qui les route.
        """
        problems: list[str] = []
        for task, variable in required_tasks.items():
            if task not in self.routes:
                problems.append(f"{variable} doit nommer un modèle (fournisseur:modèle).")
        for task, route in [*self.routes.items(), *self.fallbacks.items()]:
            if not configured.get(route.provider, False):
                problems.append(
                    f"la tâche « {task} » est routée vers {route}, mais "
                    f"LLM__PROVIDERS__{route.provider.value.upper()}__API_KEY est vide."
                )
        return problems


class LlmRouter:
    """Implémente `LanguageModel` : choisit l'adaptateur, applique le repli, journalise."""

    def __init__(self, table: RoutingTable, adapters: Mapping[Provider, ProviderAdapter]) -> None:
        self._table = table
        self._adapters = dict(adapters)

    def route_for(self, task: str) -> ModelRoute | None:
        """La route d'une tâche, ou `None`."""
        return self._table.routes.get(task)

    async def generate(self, request: LlmRequest) -> LlmResult:
        """Un appel, routé par la tâche, avec repli sur panne ou capacité absente."""
        route = self._table.routes.get(request.task)
        if route is None:
            raise LlmUnavailableError(f"aucun modèle configuré pour la tâche « {request.task} »")
        try:
            return await self._call(route, request)
        except (LlmUnavailableError, LlmCapabilityError) as exc:
            fallback = self._table.fallbacks.get(request.task)
            if fallback is None or fallback == route:
                raise
            logger.warning(
                "llm.fallback",
                task=request.task,
                primary=str(route),
                fallback=str(fallback),
                reason=type(exc).__name__,
            )
            return await self._call(fallback, request)

    async def _call(self, route: ModelRoute, request: LlmRequest) -> LlmResult:
        adapter = self._adapters.get(route.provider)
        if adapter is None:
            raise LlmUnavailableError(
                f"le fournisseur {route.provider.value} n'est pas configuré "
                f"(LLM__PROVIDERS__{route.provider.value.upper()}__API_KEY)"
            )
        return await adapter.generate(route.model, request)


class DisabledLanguageModel:
    """Sans aucune route ni clé : refuse à l'appel, pas au démarrage (le local sans compte)."""

    async def generate(self, request: LlmRequest) -> LlmResult:
        """Lève toujours."""
        logger.warning("llm.disabled", task=request.task)
        raise LlmUnavailableError("aucun fournisseur de modèle n'est configuré (LLM__PROVIDERS__*)")


_: type[LanguageModel] = LlmRouter  # vérification statique du contrat
