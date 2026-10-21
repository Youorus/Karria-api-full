"""Infrastructure du module `cv` : persistance, Gemini, lecture de fichiers."""

from karria.modules.cv.infrastructure.documents import FileInspector
from karria.modules.cv.infrastructure.enhancer import GeminiPortraitEnhancer
from karria.modules.cv.infrastructure.extractor import GeminiCvExtractor, extraction_schema
from karria.modules.cv.infrastructure.gemini import (
    REFUSAL_REASONS,
    DisabledTransport,
    GeminiClient,
    GenerationRequest,
    GenerationResult,
    GenerationTransport,
    InlinePart,
    response_schema_for,
)
from karria.modules.cv.infrastructure.interpreter import (
    GeminiChapterInterpreter,
    interpretation_schema,
)
from karria.modules.cv.infrastructure.repositories import (
    TortoiseCvRepository,
    TortoiseDraftRepository,
    TortoisePhotoRepository,
)
from karria.modules.cv.infrastructure.transcriber import GeminiTranscriber

__all__ = [
    "REFUSAL_REASONS",
    "DisabledTransport",
    "FileInspector",
    "GeminiChapterInterpreter",
    "GeminiClient",
    "GeminiCvExtractor",
    "GeminiPortraitEnhancer",
    "GeminiTranscriber",
    "GenerationRequest",
    "GenerationResult",
    "GenerationTransport",
    "InlinePart",
    "TortoiseCvRepository",
    "TortoiseDraftRepository",
    "TortoisePhotoRepository",
    "extraction_schema",
    "interpretation_schema",
    "response_schema_for",
]
