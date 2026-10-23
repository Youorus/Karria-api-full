"""L'assemblage du routeur depuis la configuration — la seule pièce du socle qui lit `LLM__*`.

Un fournisseur n'a d'adaptateur que s'il est configuré (clé, ou adresse
pour un modèle local). En environnement déployé, une route vers un
fournisseur sans clé a déjà fait échouer le démarrage
(`Settings._enforce_production_invariants`) ; en local, elle est portée à
l'appel, pour pouvoir lancer l'API sans aucun compte.
"""

from __future__ import annotations

from karria.shared.config import LlmProviderSettings, Settings
from karria.shared.llm.anthropic import DEFAULT_BASE_URL as ANTHROPIC_URL
from karria.shared.llm.anthropic import AnthropicAdapter
from karria.shared.llm.gemini import DEFAULT_BASE_URL as GEMINI_URL
from karria.shared.llm.gemini import GeminiAdapter
from karria.shared.llm.openai import DEFAULT_BASE_URL as OPENAI_URL
from karria.shared.llm.openai import OpenAIAdapter
from karria.shared.llm.ports import LanguageModel, Provider, ProviderAdapter
from karria.shared.llm.router import DisabledLanguageModel, LlmRouter, RoutingTable
from karria.shared.logging import get_logger

logger = get_logger(__name__)


def _adapter(provider: Provider, conf: LlmProviderSettings) -> ProviderAdapter:
    key = conf.api_key.get_secret_value()
    timeout = conf.request_timeout_seconds
    if provider is Provider.GEMINI:
        return GeminiAdapter.from_credentials(
            key, base_url=conf.base_url or GEMINI_URL, timeout_seconds=timeout
        )
    if provider is Provider.ANTHROPIC:
        return AnthropicAdapter.from_credentials(
            key, base_url=conf.base_url or ANTHROPIC_URL, timeout_seconds=timeout
        )
    return OpenAIAdapter.from_credentials(
        key,
        base_url=conf.base_url or OPENAI_URL,
        timeout_seconds=timeout,
        provider=provider,
        structured_output=conf.structured_output,
    )


def build_language_model(settings: Settings) -> LanguageModel:
    """Le routeur, avec un adaptateur par fournisseur configuré."""
    problems = settings.llm_problems()
    if problems:
        logger.warning("llm.routing_incomplete", problems=problems)
    adapters: dict[Provider, ProviderAdapter] = {}
    for provider in Provider:
        conf: LlmProviderSettings = getattr(settings.llm.providers, provider.value)
        if conf.is_configured(provider.value):
            adapters[provider] = _adapter(provider, conf)
    if not adapters:
        return DisabledLanguageModel()
    table = RoutingTable.parse(settings.llm.tasks.model_dump(), settings.llm.fallbacks.model_dump())
    return LlmRouter(table, adapters)
