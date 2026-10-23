"""L'interprète : une réponse libre → un premier jet au schéma du chapitre, par le port LLM.

Implémente `ChapterInterpreter`. Le schéma imposé au modèle est dérivé du
modèle Pydantic du chapitre (`domain/schemas.py`), enveloppé dans la forme
de l'`Interpretation` : la sortie **ne peut pas** avoir une autre forme. Ce
que le modèle rend reste pourtant une donnée brute — le cas d'usage la
revalide. Le fournisseur et le modèle viennent du routage de la tâche
`cv_interpret` (ADR-0018).
"""

from __future__ import annotations

from functools import cache
from typing import Any, cast

from pydantic import BaseModel, ValidationError, create_model

from karria.modules.cv.application import Interpretation
from karria.modules.cv.domain import CHAPTER_SCHEMAS, Chapter, ModelOutputInvalidError
from karria.modules.cv.infrastructure.llm import call, json_of
from karria.modules.cv.infrastructure.prompts import interpret_message, interpret_system
from karria.shared.llm import LanguageModel, LlmRequest, response_schema_for
from karria.shared.logging import get_logger

logger = get_logger(__name__)

TASK = "cv_interpret"


class _InterpretationOut(BaseModel):
    """La forme brute de ce que le modèle rend. `proposal` n'est pas encore validé."""

    proposal: dict[str, Any] | None = None
    missing: list[str] = []
    follow_up: str | None = None
    off_topic: bool = False


@cache
def interpretation_schema(chapter: Chapter) -> dict[str, Any]:
    """Le schéma JSON imposé pour un chapitre, calculé une fois."""
    proposal_type = CHAPTER_SCHEMAS[chapter]
    model = create_model(
        f"Interpretation_{chapter.value}",
        proposal=(cast(Any, proposal_type | None), ...),
        missing=(list[str], ...),
        follow_up=(str | None, ...),
        off_topic=(bool, ...),
    )
    return response_schema_for(model)


class LlmChapterInterpreter:
    """Implémente `ChapterInterpreter` par le port LLM."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def interpret(
        self, chapter: Chapter, answer: str, correction_of: dict[str, Any] | None
    ) -> Interpretation:
        result = await call(
            self._llm,
            LlmRequest(
                task=TASK,
                parts=(interpret_message(answer, correction_of),),
                system=interpret_system(chapter, correcting=correction_of is not None),
                response_schema=interpretation_schema(chapter),
                temperature=0.2,
            ),
        )
        if result.refused:
            # Un filtre de contenu a bloqué la réponse : ce n'est pas une
            # panne, c'est une réponse qu'on ne traite pas. On recentre.
            logger.warning(
                "cv.interpretation_blocked", chapter=chapter.value, reason=result.finish_reason
            )
            return Interpretation(proposal=None, missing=(), follow_up=None, off_topic=True)
        try:
            out = _InterpretationOut.model_validate(json_of(result))
        except ValidationError as exc:
            logger.warning(
                "cv.interpretation_malformed", chapter=chapter.value, errors=exc.error_count()
            )
            raise ModelOutputInvalidError("interprétation hors schéma") from exc
        return Interpretation(
            proposal=out.proposal,
            missing=tuple(out.missing),
            follow_up=out.follow_up,
            off_topic=out.off_topic,
        )
