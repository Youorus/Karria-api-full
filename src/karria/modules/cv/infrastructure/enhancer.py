"""Le portrait Gemini : une photo → un portrait professionnel, la même personne.

Implémente `PortraitEnhancer`. Reprend le générateur `profile_photo` de la
V1 : même prompt, même format 3:4, même liste de motifs de refus. Deux
issues distinctes, parce que l'application réagit différemment :

- **refus** (`PhotoEnhanceRefusedError`, 422) — le générateur ne veut pas
  de cette image ; réessayer la même ne servirait à rien ;
- **indisponible** (`PhotoEnhanceUnavailableError`, 503) — panne, délai,
  ou réponse sans image ; l'application garde l'originale et pourra
  réessayer plus tard.
"""

from __future__ import annotations

from karria.modules.cv.application import GeneratedImage
from karria.modules.cv.domain import (
    ModelUnavailableError,
    PhotoEnhanceRefusedError,
    PhotoEnhanceUnavailableError,
)
from karria.modules.cv.infrastructure.gemini import (
    GenerationRequest,
    GenerationTransport,
    InlinePart,
)
from karria.modules.cv.infrastructure.prompts import PORTRAIT_PROMPT
from karria.shared.logging import get_logger

logger = get_logger(__name__)


class GeminiPortraitEnhancer:
    """Implémente `PortraitEnhancer` avec un modèle d'image Gemini."""

    def __init__(self, transport: GenerationTransport, model: str) -> None:
        self._transport = transport
        self._model = model

    async def enhance(self, image: bytes, content_type: str) -> GeneratedImage:
        try:
            result = await self._transport.generate(
                GenerationRequest(
                    model=self._model,
                    parts=(InlinePart(image, content_type), PORTRAIT_PROMPT),
                    response_modalities=("IMAGE",),
                    aspect_ratio="3:4",
                    # Basse, comme en V1 sans consigne de style : on veut la
                    # même personne, pas une variation créative.
                    temperature=0.1,
                )
            )
        except ModelUnavailableError as exc:
            raise PhotoEnhanceUnavailableError(exc.details.get("detail")) from exc

        if result.refused:
            reason = result.block_reason or result.finish_reason
            logger.info("cv.photo_enhance_refused", reason=reason)
            raise PhotoEnhanceRefusedError(reason)
        if result.image is None or not result.image.payload:
            logger.warning("cv.photo_enhance_empty", finish_reason=result.finish_reason)
            raise PhotoEnhanceUnavailableError("aucune image rendue")
        return result.image
