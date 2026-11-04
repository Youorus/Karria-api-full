"""Infrastructure du module `applications` : persistance, modèle de langage, file."""

from karria.modules.applications.infrastructure.llm import (
    TASK_ADAPT,
    TASK_LETTER,
    LlmCvAdapter,
    LlmLetterWriter,
)
from karria.modules.applications.infrastructure.queue import (
    TASK_ADAPT_CV,
    ProcrastinateDossierJobs,
)
from karria.modules.applications.infrastructure.repositories import TortoiseDossierRepository

__all__ = [
    "TASK_ADAPT",
    "TASK_ADAPT_CV",
    "TASK_LETTER",
    "LlmCvAdapter",
    "LlmLetterWriter",
    "ProcrastinateDossierJobs",
    "TortoiseDossierRepository",
]
