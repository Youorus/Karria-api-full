"""Infrastructure du module `documents` : PDF, polices, rendus, mesures, dépôts, file, e-mail, logos."""

from karria.modules.documents.infrastructure.engine import KarriaDocumentEngine
from karria.modules.documents.infrastructure.fonts import FontLibrary
from karria.modules.documents.infrastructure.gotenberg import GotenbergClient
from karria.modules.documents.infrastructure.layout_planner import VisionLayoutPlanner
from karria.modules.documents.infrastructure.llm import LlmAtsExplainer, VisionLineLabeler
from karria.modules.documents.infrastructure.logos import StorageLogoFetcher
from karria.modules.documents.infrastructure.mail import MailDocumentMailer
from karria.modules.documents.infrastructure.queue import ProcrastinateTemplateJobs
from karria.modules.documents.infrastructure.repositories import (
    TortoiseGeneratedCvRepository,
    TortoiseSendRepository,
    TortoiseTemplateRepository,
    TortoiseUserTemplateRepository,
)

__all__ = [
    "FontLibrary",
    "GotenbergClient",
    "KarriaDocumentEngine",
    "LlmAtsExplainer",
    "MailDocumentMailer",
    "ProcrastinateTemplateJobs",
    "StorageLogoFetcher",
    "TortoiseGeneratedCvRepository",
    "TortoiseSendRepository",
    "TortoiseTemplateRepository",
    "TortoiseUserTemplateRepository",
    "VisionLayoutPlanner",
    "VisionLineLabeler",
]
