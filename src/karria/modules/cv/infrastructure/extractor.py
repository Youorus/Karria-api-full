"""L'extraction Gemini : un CV existant → les chapitres du brouillon.

Implémente `CvDocumentExtractor`. Le document part tel quel au modèle
(PDF, image) ou sous forme de texte (DOCX converti) ; la sortie a le schéma
du CV complet, dérivé des modèles du domaine. Elle est rendue **brute**,
chapitre par chapitre : le cas d'usage revalide chaque élément et ignore
ceux qui ne tiennent pas, plutôt que de tout refuser.

Ce qui change par rapport à la V1 (`cv_extraction/extractor.py`) : les
niveaux sont les énumérations du contrat et non des entiers ; les dates
restent en `AAAA-MM` ; `identity` et `contact` sont extraits et **gardés**
(la V1 les normalisait puis les jetait) ; les centres d'intérêt sont des
objets et non des chaînes (la V1 en perdait la totalité) ; une extraction
qui échoue lève au lieu de rendre une structure vide qu'on écrirait par
dessus le CV.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from pydantic import BaseModel, ValidationError

from karria.modules.cv.domain import (
    Chapter,
    Contact,
    Education,
    Experience,
    Identity,
    InterestsChapter,
    ModelOutputInvalidError,
    ModelUnavailableError,
    SkillsChapter,
    Summary,
)
from karria.modules.cv.infrastructure.gemini import (
    GenerationRequest,
    GenerationTransport,
    InlinePart,
    response_schema_for,
)
from karria.modules.cv.infrastructure.prompts import (
    EXTRACT_MESSAGE,
    EXTRACT_SYSTEM,
    EXTRACT_TEXT_MESSAGE,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)


class _ExtractedCv(BaseModel):
    """La forme imposée au modèle : le CV entier, aux schémas du domaine."""

    identity: Identity | None
    contact: Contact | None
    summary: Summary | None
    experiences: list[Experience]
    educations: list[Education]
    skills: SkillsChapter
    interests: InterestsChapter


class _RawExtraction(BaseModel):
    """Ce qu'on accepte de lire avant revalidation : la structure, pas le détail."""

    identity: dict[str, Any] | None = None
    contact: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    experiences: list[dict[str, Any]] = []
    educations: list[dict[str, Any]] = []
    skills: dict[str, Any] | None = None
    interests: dict[str, Any] | None = None


@cache
def extraction_schema() -> dict[str, Any]:
    """Le schéma JSON imposé pour l'extraction, calculé une fois."""
    return response_schema_for(_ExtractedCv)


class GeminiCvExtractor:
    """Implémente `CvDocumentExtractor` avec Gemini."""

    def __init__(self, transport: GenerationTransport, model: str) -> None:
        self._transport = transport
        self._model = model

    async def extract(self, document: bytes, content_type: str) -> dict[Chapter, Any]:
        parts: tuple[str | InlinePart, ...]
        if content_type == "text/plain":
            text = document.decode("utf-8", errors="replace").replace("</", "<\\/")
            parts = (f"<document>\n{text}\n</document>", EXTRACT_TEXT_MESSAGE)
        else:
            parts = (InlinePart(document, content_type), EXTRACT_MESSAGE)

        result = await self._transport.generate(
            GenerationRequest(
                model=self._model,
                parts=parts,
                system=EXTRACT_SYSTEM,
                response_schema=extraction_schema(),
                temperature=0.1,
                thinking_budget=0,
            )
        )
        if result.refused:
            raise ModelUnavailableError(f"refus : {result.block_reason or result.finish_reason}")
        try:
            raw = _RawExtraction.model_validate(result.json())
        except ValidationError as exc:
            logger.warning("cv.extraction_malformed", errors=exc.error_count())
            raise ModelOutputInvalidError("extraction hors schéma") from exc

        singles = {
            Chapter.IDENTITY: raw.identity,
            Chapter.CONTACT: raw.contact,
            Chapter.SUMMARY: raw.summary,
            Chapter.SKILLS: raw.skills,
            Chapter.INTERESTS: raw.interests,
        }
        extracted: dict[Chapter, Any] = {
            Chapter.EXPERIENCES: raw.experiences,
            Chapter.EDUCATIONS: raw.educations,
        }
        extracted.update(
            {chapter: value for chapter, value in singles.items() if value is not None}
        )
        return extracted
