"""Le portrait d'exemple d'un modèle, généré par le modèle d'image (port LLM).

Implémente `PortraitGenerator`. La tâche `portrait` (celle du portrait de
la personne, module `cv`) route déjà vers le modèle d'image de Gemini ; on
la réutilise avec une consigne **sans image d'entrée** : la personne est
fictive. Meilleur effort : une panne, un refus ou une réponse sans image
rendent `None`, et l'appelant garde la photo extraite du PDF.
"""

from __future__ import annotations

from karria.modules.documents.domain.portrait import Persona, portrait_prompt
from karria.shared.llm import LanguageModel, LlmCapabilityError, LlmRequest, LlmUnavailableError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

TASK = "portrait"


class LlmPortraitGenerator:
    """Implémente `PortraitGenerator` par le port LLM (modèle d'image)."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def generate(self, persona: Persona) -> bytes | None:
        try:
            result = await self._llm.generate(
                LlmRequest(
                    task=TASK,
                    parts=(portrait_prompt(persona),),
                    image_output=True,
                    aspect_ratio="3:4",
                    # Une personne plausible et variée : plus libre que le portrait
                    # de la vraie personne, qui doit rester elle-même.
                    temperature=0.7,
                )
            )
        except (LlmUnavailableError, LlmCapabilityError) as exc:
            logger.warning("documents.portrait_unavailable", detail=str(exc)[:200])
            return None
        if result.refused or result.image is None or not result.image.data:
            logger.warning(
                "documents.portrait_empty", refused=result.refused, finish=result.finish_reason
            )
            return None
        logger.info(
            "documents.portrait_generated",
            name=persona.full_name,
            bytes=len(result.image.data),
            model=result.model,
        )
        return result.image.data
