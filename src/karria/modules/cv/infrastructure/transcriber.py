"""La transcription : un enregistrement → du texte français, par le port LLM.

Implémente `AudioTranscriber`. La sortie à schéma `{text}` garantit
qu'aucun commentaire du modèle ne se glisse dans la transcription. La tâche
`transcribe` ne peut être routée que vers un fournisseur qui lit l'audio
(Gemini, OpenAI) : un autre lève `LlmCapabilityError`, traduite en 503.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ValidationError

from karria.modules.cv.domain import ModelOutputInvalidError, ModelUnavailableError
from karria.modules.cv.infrastructure.llm import call, json_of
from karria.modules.cv.infrastructure.prompts import TRANSCRIBE_MESSAGE, TRANSCRIBE_SYSTEM
from karria.shared.llm import Blob, LanguageModel, LlmRequest
from karria.shared.logging import get_logger

logger = get_logger(__name__)

TASK = "transcribe"
#: Ce que les clients annoncent → le type MIME canonique que les fournisseurs
#: acceptent. Le M4A d'expo-audio est un conteneur MP4 ; `audio/mpeg` est le
#: nom officiel du MP3, que Gemini écrit `audio/mp3`.
AUDIO_TYPES: Final[dict[str, str]] = {
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


class LlmTranscriber:
    """Implémente `AudioTranscriber` par le port LLM."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        result = await call(
            self._llm,
            LlmRequest(
                task=TASK,
                parts=(
                    Blob(audio, AUDIO_TYPES.get(content_type, content_type)),
                    TRANSCRIBE_MESSAGE,
                ),
                system=TRANSCRIBE_SYSTEM,
                response_schema=_SCHEMA,
                temperature=0.0,
            ),
        )
        if result.refused:
            raise ModelUnavailableError(f"refus : {result.finish_reason}")
        try:
            return _Transcript.model_validate(json_of(result)).text
        except ValidationError as exc:
            logger.warning("cv.transcription_malformed", errors=exc.error_count())
            raise ModelOutputInvalidError("transcription hors schéma") from exc
