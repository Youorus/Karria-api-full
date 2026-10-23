"""Le portrait : une photo → un portrait professionnel, la même personne, par le port LLM.

Implémente `PortraitEnhancer`. Reprend le générateur `profile_photo` de la
V1 : même prompt, même format 3:4. La tâche `portrait` demande une **image**
en sortie : seul un fournisseur qui en génère (Gemini) peut la servir ; un
autre lève `LlmCapabilityError`, traduite en indisponibilité. Deux issues :

- **refus** (`PhotoEnhanceRefusedError`, 422) — le générateur ne veut pas
  de cette image ; réessayer la même ne servirait à rien ;
- **indisponible** (`PhotoEnhanceUnavailableError`, 503) — panne, délai,
  ou réponse sans image ; l'application garde l'originale.
"""

from __future__ import annotations

from karria.modules.cv.application import GeneratedImage
from karria.modules.cv.domain import (
    ModelUnavailableError,
    PhotoEnhanceRefusedError,
    PhotoEnhanceUnavailableError,
)
from karria.modules.cv.infrastructure.llm import call
from karria.modules.cv.infrastructure.prompts import PORTRAIT_PROMPT
from karria.shared.llm import Blob, LanguageModel, LlmRequest
from karria.shared.logging import get_logger

logger = get_logger(__name__)

TASK = "portrait"


class LlmPortraitEnhancer:
    """Implémente `PortraitEnhancer` par le port LLM (modèle d'image)."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def enhance(self, image: bytes, content_type: str) -> GeneratedImage:
        try:
            result = await call(
                self._llm,
                LlmRequest(
                    task=TASK,
                    parts=(Blob(image, content_type), PORTRAIT_PROMPT),
                    image_output=True,
                    aspect_ratio="3:4",
                    # Basse, comme en V1 sans consigne de style : on veut la
                    # même personne, pas une variation créative.
                    temperature=0.1,
                ),
            )
        except ModelUnavailableError as exc:
            raise PhotoEnhanceUnavailableError(exc.details.get("detail")) from exc

        if result.refused:
            logger.info("cv.photo_enhance_refused", reason=result.finish_reason)
            raise PhotoEnhanceRefusedError(result.finish_reason)
        if result.image is None or not result.image.data:
            logger.warning("cv.photo_enhance_empty", finish_reason=result.finish_reason)
            raise PhotoEnhanceUnavailableError("aucune image rendue")
        return GeneratedImage(payload=result.image.data, content_type=result.image.content_type)
