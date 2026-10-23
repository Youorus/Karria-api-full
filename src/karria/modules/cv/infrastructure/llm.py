"""Le pont entre le port LLM du socle et les erreurs du module `cv`.

Le port lève ses propres erreurs (`LlmUnavailableError`,
`LlmCapabilityError`, `LlmOutputInvalidError`) ; le module les traduit ici
en `ModelUnavailableError` / `ModelOutputInvalidError`, celles que ses cas
d'usage et ses routes connaissent. Un seul endroit, pour que les quatre
adaptateurs disent la même chose.
"""

from __future__ import annotations

from typing import Any

from karria.modules.cv.domain import ModelOutputInvalidError, ModelUnavailableError
from karria.shared.llm import (
    LanguageModel,
    LlmCapabilityError,
    LlmOutputInvalidError,
    LlmRequest,
    LlmResult,
    LlmUnavailableError,
)


async def call(llm: LanguageModel, request: LlmRequest) -> LlmResult:
    """Un appel, les pannes traduites.

    Raises:
        ModelUnavailableError: Panne, quota, route ou capacité absentes.
    """
    try:
        return await llm.generate(request)
    except LlmUnavailableError as exc:
        raise ModelUnavailableError(exc.detail) from exc
    except LlmCapabilityError as exc:
        raise ModelUnavailableError(str(exc)) from exc


def json_of(result: LlmResult) -> Any:
    """Le JSON de la réponse, ou `ModelOutputInvalidError`."""
    try:
        return result.json()
    except LlmOutputInvalidError as exc:
        raise ModelOutputInvalidError(str(exc)) from exc
