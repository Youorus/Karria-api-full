"""L'extraction : un CV existant → les chapitres du brouillon, par le port LLM.

Implémente `CvDocumentExtractor`. Le document part tel quel au modèle
(PDF, image) ou sous forme de texte (DOCX converti) ; la sortie a le schéma
du CV, dérivé des modèles du domaine. Elle est rendue **brute**, chapitre
par chapitre : le cas d'usage revalide chaque élément et ignore ceux qui ne
tiennent pas, plutôt que de tout refuser.

Pourquoi plusieurs appels, et plusieurs fois
--------------------------------------------
Mesuré le 2026-11-04 sur deux vrais CV (PDF à couche texte, image) avec
`gemini-3.8-flash`, réflexion coupée : **un seul appel** sur le schéma
complet rendait tantôt 3 expériences et 22 compétences, tantôt 1 et 0, pour
le même document (6 essais sur 6 avec au moins un trou) — le modèle clôt
ses listes trop tôt, sans erreur ni troncature annoncée. Changer de modèle
(`gemini-3.5-flash`), activer la réflexion (3 à 10 fois plus lent) ou lui
faire d'abord compter les rubriques n'y changeait rien.

Ce qui a marché : **un appel par groupe de sections**, schéma réduit, en
parallèle (les listes deviennent complètes dans la plupart des essais), et
**deux tentatives par groupe** dont on garde la plus riche — trois pour les
expériences, la liste la plus longue et la plus souvent coupée. Avec ces
deux mesures : 7 extractions complètes sur 8. Le coût reste celui de
quelques milliers de jetons d'entrée (le CV est court) ; la latence est
celle d'un seul appel puisque tout part ensemble (5 à 9 s mesurées).

Ce qui change par rapport à la V1 (`cv_extraction/extractor.py`) : les
niveaux sont les énumérations du contrat et non des entiers ; les dates
restent en `AAAA-MM` ; `identity` et `contact` sont extraits et **gardés**
(la V1 les normalisait puis les jetait) ; les centres d'intérêt sont des
objets et non des chaînes (la V1 en perdait la totalité) ; une extraction
qui échoue lève au lieu de rendre une structure vide qu'on écrirait par
dessus le CV.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from functools import cache
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from karria.modules.cv.domain import (
    Chapter,
    Contact,
    DocumentInfo,
    Education,
    Experience,
    Identity,
    InterestsChapter,
    ModelOutputInvalidError,
    ModelUnavailableError,
    SkillsChapter,
    Summary,
)
from karria.modules.cv.infrastructure.llm import call, json_of
from karria.modules.cv.infrastructure.prompts import (
    EXTRACT_MESSAGE,
    EXTRACT_PAGES_MESSAGE,
    EXTRACT_SYSTEM,
    EXTRACT_TEXT_MESSAGE,
)
from karria.shared.llm import (
    Blob,
    LanguageModel,
    LlmRequest,
    LlmResult,
    Part,
    response_schema_for,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)

TASK = "cv_extract"

#: Les groupes de sections demandés chacun par un appel, et le nombre de
#: tentatives de chacun. Voir l'en-tête du module pour la mesure.
GROUPS: Final[tuple[tuple[str, ...], ...]] = (
    ("identity", "contact", "summary"),
    ("skills", "interests"),
    ("experiences",),
    ("educations",),
)
ATTEMPTS: Final[dict[str, int]] = {"experiences": 3}
DEFAULT_ATTEMPTS: Final = 2


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
    """Le schéma JSON du CV complet, calculé une fois — la source des schémas de groupe."""
    return response_schema_for(_ExtractedCv)


@cache
def group_schema(group: tuple[str, ...]) -> dict[str, Any]:
    """Le schéma d'un groupe de sections : le schéma complet restreint à ces clés."""
    full = extraction_schema()
    return {
        "type": "object",
        "properties": {key: full["properties"][key] for key in group},
        "required": [key for key in full.get("required", []) if key in group],
    }


class LlmCvExtractor:
    """Implémente `CvDocumentExtractor` par le port LLM — un appel par groupe, le meilleur gardé."""

    def __init__(self, llm: LanguageModel) -> None:
        self._llm = llm

    async def extract(self, documents: Sequence[DocumentInfo]) -> dict[Chapter, Any]:
        parts = _parts(documents)
        plan = [
            (group, attempt)
            for group in GROUPS
            for attempt in range(ATTEMPTS.get(group[0], DEFAULT_ATTEMPTS))
        ]
        results = await asyncio.gather(*(self._ask(parts, group) for group, _ in plan))

        merged: dict[str, Any] = {}
        for (group, _), result in zip(plan, results, strict=True):
            if result.refused:
                raise ModelUnavailableError(f"refus : {result.finish_reason}")
            try:
                raw = _RawExtraction.model_validate(json_of(result))
            except ValidationError as exc:
                logger.warning("cv.extraction_malformed", group=group, errors=exc.error_count())
                continue
            for key in group:
                merged[key] = _richer(merged.get(key), getattr(raw, key))
        missing_groups = [g for g in GROUPS if not any(k in merged for k in g)]
        if missing_groups:
            raise ModelOutputInvalidError("extraction hors schéma")

        singles = {
            Chapter.IDENTITY: merged.get("identity"),
            Chapter.CONTACT: merged.get("contact"),
            Chapter.SUMMARY: merged.get("summary"),
            Chapter.SKILLS: merged.get("skills"),
            Chapter.INTERESTS: merged.get("interests"),
        }
        extracted: dict[Chapter, Any] = {
            Chapter.EXPERIENCES: merged.get("experiences") or [],
            Chapter.EDUCATIONS: merged.get("educations") or [],
        }
        extracted.update(
            {chapter: value for chapter, value in singles.items() if value is not None}
        )
        return extracted

    async def _ask(self, parts: tuple[Part, ...], group: tuple[str, ...]) -> LlmResult:
        return await call(
            self._llm,
            LlmRequest(
                task=TASK,
                parts=parts,
                system=EXTRACT_SYSTEM,
                response_schema=group_schema(group),
                temperature=0.1,
            ),
        )


def _parts(documents: Sequence[DocumentInfo]) -> tuple[Part, ...]:
    parts: list[Part] = []
    for document in documents:
        if document.content_type == "text/plain":
            text = document.payload.decode("utf-8", errors="replace").replace("</", "<\\/")
            parts.append(f"<document>\n{text}\n</document>")
        else:
            parts.append(Blob(document.payload, document.content_type))
    if len(documents) > 1:
        parts.append(EXTRACT_PAGES_MESSAGE.format(count=len(documents)))
    elif documents and documents[0].content_type == "text/plain":
        parts.append(EXTRACT_TEXT_MESSAGE)
    else:
        parts.append(EXTRACT_MESSAGE)
    return tuple(parts)


def _size(value: Any) -> int:
    """Combien une valeur « contient » : la longueur d'une liste, la somme des listes d'un objet, 1 sinon."""
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        inner = [v for v in value.values() if isinstance(v, list)]
        return sum(len(v) for v in inner) if inner else 1
    return 1


def _richer(current: Any, candidate: Any) -> Any:
    """Entre deux tentatives, celle qui a lu le plus. Le modèle n'invente pas : manquer est le risque."""
    if current is None:
        return candidate
    return candidate if _size(candidate) > _size(current) else current
