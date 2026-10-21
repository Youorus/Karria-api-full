"""La transcription Gemini : un enregistrement → du texte français.

Implémente `AudioTranscriber`. La V1 passait par Whisper (OpenAI) ; le
contrat de la refonte veut un seul fournisseur. Gemini comprend l'audio
nativement, en `inline_data`, et la sortie à schéma `{text}` garantit
qu'aucun commentaire du modèle ne se glisse dans la transcription.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ValidationError

from karria.modules.cv.domain import ModelOutputInvalidError, ModelUnavailableError
from karria.modules.cv.infrastructure.gemini import (
    GenerationRequest,
    GenerationTransport,
    InlinePart,
)
from karria.modules.cv.infrastructure.prompts import TRANSCRIBE_MESSAGE, TRANSCRIBE_SYSTEM
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Ce que les clients annoncent → ce que Gemini accepte. Le M4A d'expo-audio
#: est un conteneur MP4 ; `audio/mpeg` est le nom officiel du MP3.
_GEMINI_AUDIO_TYPES: Final[dict[str, str]] = {
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/mpeg": "audio/mp3",
    "audio/x-wav": "audio/wav",
}

_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


class _Transcript(BaseModel):
    text: str


class GeminiTranscriber:
    """Implémente `AudioTranscriber` avec Gemini."""

    def __init__(self, transport: GenerationTransport, model: str) -> None:
        self._transport = transport
        self._model = model

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        result = await self._transport.generate(
            GenerationRequest(
                model=self._model,
                parts=(
                    InlinePart(audio, _GEMINI_AUDIO_TYPES.get(content_type, content_type)),
                    TRANSCRIBE_MESSAGE,
                ),
                system=TRANSCRIBE_SYSTEM,
                response_schema=_SCHEMA,
                temperature=0.0,
                thinking_budget=0,
            )
        )
        if result.refused:
            raise ModelUnavailableError(f"refus : {result.block_reason or result.finish_reason}")
        try:
            return _Transcript.model_validate(result.json()).text
        except ValidationError as exc:
            logger.warning("cv.transcription_malformed", errors=exc.error_count())
            raise ModelOutputInvalidError("transcription hors schéma") from exc
