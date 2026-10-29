"""Le dialogue de recherche par le port LLM : un appel à schéma imposé, la tâche `search_dialogue`.

Implémente `SearchDialogue`. La politique est celle de l'ADR-0013 : règle
en consigne de système, donnée balisée, sortie à schéma, revalidation. Le
fournisseur et le modèle viennent du routage (ADR-0018) : rien ici ne nomme
Gemini.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from typing import Any, Final

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
    """La forme imposée à la sortie du modèle.

    `criteria` est **exigé par le schéma** (`dialogue_schema` l'ajoute à
    `required`, nullable) mais toléré absent à la validation : le modèle doit
    écrire `null` plutôt que l'omettre, et s'il l'omet quand même, on
    redemande plutôt que de répondre 503. Mesuré en production le
    2026-10-29 : avec un champ facultatif, Gemini omettait `criteria` sur 9
    demandes sur 20 tout en répondant « c'est noté, je recherche… » — et
    rien ne cherchait.
    """

    intent: Intent = Intent.SEARCH
    reply: str
    off_topic: bool = False
    criteria: SearchCriteria | None = None
    suggestions: list[str] = []

    @property
    def stated_intent(self) -> Intent | None:
        """L'intention telle que le modèle l'a **écrite** ; `None` s'il l'a omise."""
        return self.intent if "intent" in self.model_fields_set else None


#: Rappel ajouté à la donnée quand le modèle a classé `search` sans critères.
CRITERIA_REMINDER: Final = (
    "Ta sortie précédente classait l'intention `search` ou `refine` sans remplir "
    "`criteria`. Pour ces intentions, `criteria` est obligatoire : remplis-le maintenant."
)


@cache
def dialogue_schema() -> dict[str, Any]:
    """Le schéma JSON imposé, dérivé de `DialogueOut` et nettoyé ; `criteria` y est exigé."""
    schema = response_schema_for(DialogueOut, drop_fields=frozenset())
    schema["required"] = sorted({*schema.get("required", []), "criteria", "intent"})
    return schema


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
        blocks = [
            block
            for block in (
                memory_block(memory),
                facts_block(facts),
                context_block(current),
                user_message(message),
            )
            if block
        ]
        out = await self._ask(blocks, history)
        if out is None:
            return DialogueProposal(
                reply=OFF_TOPIC_REPLY, criteria=None, off_topic=True, intent=Intent.OFF_TOPIC
            )
        if out.criteria is None and out.stated_intent in (Intent.SEARCH, Intent.REFINE):
            # Le schéma l'exige, le modèle l'a tout de même laissé nul : un
            # second appel, avec le rappel ; au-delà, le domaine posera la question.
            logger.warning("offers.dialogue_criteria_missing", intent=out.intent.value)
            retried = await self._ask([*blocks, CRITERIA_REMINDER], history)
            if retried is not None:
                out = retried
        return DialogueProposal(
            reply=out.reply,
            criteria=out.criteria,
            off_topic=out.off_topic or out.intent is Intent.OFF_TOPIC,
            suggestions=tuple(out.suggestions),
            intent=out.stated_intent,
        )

    async def _ask(
        self, blocks: Sequence[str], history: Sequence[tuple[str, str]]
    ) -> DialogueOut | None:
        """Un appel ; `None` si le modèle a refusé (la constitution répond alors)."""
        request = LlmRequest(
            task=TASK,
            parts=("\n\n".join(blocks),),
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
            return None
        try:
            return DialogueOut.model_validate(result.json())
        except (LlmOutputInvalidError, ValidationError) as exc:
            logger.warning("offers.dialogue_output_invalid", model=result.model)
            raise ModelOutputInvalidError("réponse hors schéma") from exc
