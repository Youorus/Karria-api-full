"""Le dialogue de recherche par le port LLM : un appel à schéma imposé, la tâche `search_dialogue`.

Implémente `SearchDialogue`. La politique est celle de l'ADR-0013 : règle
en consigne de système, donnée balisée, sortie à schéma, revalidation. Le
fournisseur et le modèle viennent du routage (ADR-0018) : rien ici ne nomme
Gemini.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from typing import Any

from pydantic import BaseModel, ValidationError

from karria.modules.offers.domain import (
    DialogueProposal,
    Intent,
    ModelOutputInvalidError,
    ModelUnavailableError,
    SearchCriteria,
)
from karria.modules.offers.infrastructure.prompts import (
    SYSTEM,
    context_block,
    facts_block,
    memory_block,
    user_message,
)
from karria.shared.llm import (
    LanguageModel,
    LlmCapabilityError,
    LlmOutputInvalidError,
    LlmRequest,
    LlmUnavailableError,
    response_schema_for,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)

TASK = "search_dialogue"
OFF_TOPIC_REPLY = "Parlons plutôt de ta recherche d'emploi : quel poste vises-tu ?"


class DialogueOut(BaseModel):
    """La forme imposée à la sortie du modèle."""

    intent: Intent = Intent.SEARCH
    reply: str
    off_topic: bool = False
    criteria: SearchCriteria | None = None
    suggestions: list[str] = []


@cache
def dialogue_schema() -> dict[str, Any]:
    """Le schéma JSON imposé, dérivé de `DialogueOut` et nettoyé."""
    return response_schema_for(DialogueOut, drop_fields=frozenset())


class LlmDialogue:
    """Implémente `SearchDialogue` par le port LLM."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def propose(
        self,
        *,
        history: Sequence[tuple[str, str]],
        current: SearchCriteria | None,
        message: str,
        memory: str | None = None,
        facts: str | None = None,
    ) -> DialogueProposal:
        """Un tour : constitution, mémoire, critères courants, fenêtre récente, message → proposition validée."""
        request = LlmRequest(
            task=TASK,
            parts=(
                "\n\n".join(
                    block
                    for block in (
                        memory_block(memory),
                        facts_block(facts),
                        context_block(current),
                        user_message(message),
                    )
                    if block
                ),
            ),
            system=SYSTEM,
            history=tuple(history),
            response_schema=dialogue_schema(),
            temperature=0.2,
        )
        try:
            result = await self._llm.generate(request)
        except LlmUnavailableError as exc:
            raise ModelUnavailableError(exc.detail) from exc
        except LlmCapabilityError as exc:
            raise ModelUnavailableError(str(exc)) from exc
        if result.refused or result.text is None:
            return DialogueProposal(
                reply=OFF_TOPIC_REPLY, criteria=None, off_topic=True, intent=Intent.OFF_TOPIC
            )
        try:
            out = DialogueOut.model_validate(result.json())
        except (LlmOutputInvalidError, ValidationError) as exc:
            logger.warning("offers.dialogue_output_invalid", model=result.model)
            raise ModelOutputInvalidError("réponse hors schéma") from exc
        return DialogueProposal(
            reply=out.reply,
            criteria=out.criteria,
            off_topic=out.off_topic or out.intent is Intent.OFF_TOPIC,
            suggestions=tuple(out.suggestions),
            intent=out.intent,
        )
