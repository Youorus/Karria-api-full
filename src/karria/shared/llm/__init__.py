"""Modèles de langage : un port, des adaptateurs par fournisseur, un routeur par tâche (ADR-0018).

Ce que le socle offre aux modules :

- `LanguageModel` — le port : `generate(LlmRequest) -> LlmResult` ;
- `LlmRequest`, `Blob`, `LlmResult` — le vocabulaire, sans fournisseur ;
- `structured()` — un appel à schéma imposé, revalidé par Pydantic ;
- `response_schema_for()` — le schéma JSON d'un modèle Pydantic, nettoyé ;
- les erreurs `Llm*Error`, que chaque module traduit dans les siennes.

Ce que `bootstrap` assemble : `build_language_model(settings)`, qui lit
`LLM__*`, construit les adaptateurs configurés et le routeur. Les modules
reçoivent le port par injection (`shared/http/dependencies.py`), jamais un
adaptateur.
"""

from __future__ import annotations

from karria.shared.llm.anthropic import AnthropicAdapter
from karria.shared.llm.factory import build_language_model
from karria.shared.llm.gemini import GeminiAdapter
from karria.shared.llm.openai import OpenAIAdapter
from karria.shared.llm.ports import (
    Blob,
    LanguageModel,
    LlmCapabilityError,
    LlmConfigurationError,
    LlmError,
    LlmOutputInvalidError,
    LlmRequest,
    LlmResult,
    LlmUnavailableError,
    ModelRoute,
    Part,
    Provider,
    ProviderAdapter,
    structured,
    text_parts,
)
from karria.shared.llm.router import DisabledLanguageModel, LlmRouter, RoutingTable
from karria.shared.llm.schema import clean_schema, response_schema_for, strict_schema

__all__ = [
    "AnthropicAdapter",
    "Blob",
    "DisabledLanguageModel",
    "GeminiAdapter",
    "LanguageModel",
    "LlmCapabilityError",
    "LlmConfigurationError",
    "LlmError",
    "LlmOutputInvalidError",
    "LlmRequest",
    "LlmResult",
    "LlmRouter",
    "LlmUnavailableError",
    "ModelRoute",
    "OpenAIAdapter",
    "Part",
    "Provider",
    "ProviderAdapter",
    "RoutingTable",
    "build_language_model",
    "clean_schema",
    "response_schema_for",
    "strict_schema",
    "structured",
    "text_parts",
]
