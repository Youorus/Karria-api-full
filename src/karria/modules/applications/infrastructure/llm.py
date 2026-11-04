"""Les deux adaptateurs d'IA du module, sur le port LLM du socle (ADR-0018).

Deux tâches routées par configuration : `cv_adapt` (l'adaptation du CV) et
`letter_draft` (la lettre, et la réécriture d'un paragraphe). Les pannes du
port sont traduites ici dans les erreurs du module ; la sortie est une
donnée que le domaine revalide (`AdaptationOutput`, `Letter.validated`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from karria.modules.applications.domain import (
    AdaptationOutput,
    Letter,
    LetterInvalidError,
    LetterShape,
    ModelOutputInvalidError,
    ModelUnavailableError,
    OfferSnapshot,
    ParagraphOutput,
)
from karria.modules.applications.infrastructure.prompts import (
    ADAPT_SYSTEM,
    LETTER_SYSTEM,
    PARAGRAPH_SYSTEM,
    adapt_message,
    letter_message,
    paragraph_message,
)
from karria.shared.llm import (
    LanguageModel,
    LlmCapabilityError,
    LlmOutputInvalidError,
    LlmRequest,
    LlmResult,
    LlmUnavailableError,
    response_schema_for,
)

TASK_ADAPT: Final = "cv_adapt"
TASK_LETTER: Final = "letter_draft"
#: Plafonds de sortie : une adaptation complète tient en quelques milliers de
#: jetons, une lettre d'une page en moins de mille.
ADAPT_MAX_TOKENS: Final = 6000
LETTER_MAX_TOKENS: Final = 2500
PARAGRAPH_MAX_TOKENS: Final = 800


async def call(llm: LanguageModel, request: LlmRequest) -> LlmResult:
    """Un appel, les pannes traduites.

    Raises:
        ModelUnavailableError: Panne, quota, route ou capacité absentes, ou refus.
    """
    try:
        result = await llm.generate(request)
    except LlmUnavailableError as exc:
        raise ModelUnavailableError(exc.detail) from exc
    except LlmCapabilityError as exc:
        raise ModelUnavailableError(str(exc)) from exc
    if result.refused:
        raise ModelUnavailableError("refus du modèle")
    return result


def json_of(result: LlmResult) -> Any:
    """Le JSON de la réponse, ou `ModelOutputInvalidError`."""
    try:
        return result.json()
    except LlmOutputInvalidError as exc:
        raise ModelOutputInvalidError(str(exc)) from exc


class LlmCvAdapter:
    """Implémente `CvAdapter` (tâche `cv_adapt`)."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def adapt(self, offer: OfferSnapshot, cv: Mapping[str, Any]) -> AdaptationOutput:
        """La sortie structurée, revalidée par son schéma."""
        result = await call(
            self._llm,
            LlmRequest(
                task=TASK_ADAPT,
                system=ADAPT_SYSTEM,
                parts=(adapt_message(offer, cv),),
                response_schema=response_schema_for(AdaptationOutput),
                temperature=0.3,
                max_output_tokens=ADAPT_MAX_TOKENS,
            ),
        )
        try:
            return AdaptationOutput.model_validate(json_of(result))
        except ValueError as exc:
            raise ModelOutputInvalidError("adaptation hors schéma") from exc


class LlmLetterWriter:
    """Implémente `LetterWriter` (tâche `letter_draft`)."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def draft(self, offer: OfferSnapshot, cv: Mapping[str, Any]) -> Letter:
        """La première ébauche, revalidée par le domaine."""
        result = await call(
            self._llm,
            LlmRequest(
                task=TASK_LETTER,
                system=LETTER_SYSTEM,
                parts=(letter_message(offer, cv),),
                response_schema=response_schema_for(LetterShape),
                temperature=0.5,
                max_output_tokens=LETTER_MAX_TOKENS,
            ),
        )
        data = json_of(result)
        if not isinstance(data, Mapping):
            raise ModelOutputInvalidError("lettre hors schéma")
        try:
            return Letter.validated(data)
        except LetterInvalidError as exc:
            raise ModelOutputInvalidError("lettre hors schéma") from exc

    async def rewrite_paragraph(
        self,
        offer: OfferSnapshot,
        cv: Mapping[str, Any],
        letter: Letter,
        index: int,
        instruction: str | None,
    ) -> str:
        """Le paragraphe réécrit ; la consigne voyage comme une donnée."""
        result = await call(
            self._llm,
            LlmRequest(
                task=TASK_LETTER,
                system=PARAGRAPH_SYSTEM,
                parts=(paragraph_message(offer, cv, letter, index, instruction),),
                response_schema=response_schema_for(ParagraphOutput),
                temperature=0.5,
                max_output_tokens=PARAGRAPH_MAX_TOKENS,
            ),
        )
        try:
            return ParagraphOutput.model_validate(json_of(result)).text
        except ValueError as exc:
            raise ModelOutputInvalidError("paragraphe hors schéma") from exc
