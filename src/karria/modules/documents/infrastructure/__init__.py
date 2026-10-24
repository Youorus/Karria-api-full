"""Infrastructure du module `documents` : PDF, polices, rendus, mesures, dépôts, file."""

from karria.modules.documents.infrastructure.engine import KarriaDocumentEngine
from karria.modules.documents.infrastructure.fonts import FontLibrary
from karria.modules.documents.infrastructure.gotenberg import GotenbergClient
from karria.modules.documents.infrastructure.llm import LlmAtsExplainer, VisionLineLabeler
from karria.modules.documents.infrastructure.queue import ProcrastinateTemplateJobs
from karria.modules.documents.infrastructure.repositories import (
    TortoiseGeneratedCvRepository,
    TortoiseTemplateRepository,
    TortoiseUserTemplateRepository,
)

__all__ = [
    "FontLibrary",
    "GotenbergClient",
    "KarriaDocumentEngine",
    "LlmAtsExplainer",
    "ProcrastinateTemplateJobs",
    "TortoiseGeneratedCvRepository",
    "TortoiseTemplateRepository",
    "TortoiseUserTemplateRepository",
    "VisionLineLabeler",
]
