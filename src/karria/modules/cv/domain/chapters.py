"""Les chapitres du CV, tels que le contrat les fixe.

Contrat
-------
- L'ordre est **fixe** et identique à celui de l'application
  (`src/features/cv/types/cv.types.ts`, `docs/contrat-api-cv.md`) : c'est
  lui qui détermine le « chapitre courant » d'un brouillon.
- Un chapitre **en boucle** (`experiences`, `educations`) collecte des
  éléments ; les autres produisent une valeur unique.
- Un chapitre **obligatoire** (`identity`, `contact`) ne se passe pas : un
  CV sans nom ni moyen de contact ne sert à rien. Les autres se passent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Chapter(StrEnum):
    """Clé d'un chapitre, telle qu'elle voyage dans le contrat."""

    IDENTITY = "identity"
    CONTACT = "contact"
    SUMMARY = "summary"
    EXPERIENCES = "experiences"
    EDUCATIONS = "educations"
    SKILLS = "skills"
    INTERESTS = "interests"
    PHOTO = "photo"


class ChapterStatus(StrEnum):
    """Où en est un chapitre dans le brouillon."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VALIDATED = "validated"
    SKIPPED = "skipped"

    @property
    def is_done(self) -> bool:
        """Validé ou passé : le chapitre ne bloque plus la finalisation."""
        return self in {ChapterStatus.VALIDATED, ChapterStatus.SKIPPED}


#: L'ordre de la conversation. Celui de l'application, à l'identique.
CHAPTER_ORDER: Final[tuple[Chapter, ...]] = (
    Chapter.IDENTITY,
    Chapter.CONTACT,
    Chapter.SUMMARY,
    Chapter.EXPERIENCES,
    Chapter.EDUCATIONS,
    Chapter.SKILLS,
    Chapter.INTERESTS,
    Chapter.PHOTO,
)

#: Chapitres qui collectent plusieurs éléments (« Une autre ? »).
LOOP_CHAPTERS: Final[frozenset[Chapter]] = frozenset({Chapter.EXPERIENCES, Chapter.EDUCATIONS})

#: Chapitres qu'on ne peut pas passer.
MANDATORY_CHAPTERS: Final[frozenset[Chapter]] = frozenset({Chapter.IDENTITY, Chapter.CONTACT})

#: Chapitres que l'interprète sait lire. La photo se choisit avec des
#: boutons : une réponse écrite à son sujet est, par construction, hors
#: sujet.
INTERPRETABLE_CHAPTERS: Final[frozenset[Chapter]] = frozenset(CHAPTER_ORDER) - {Chapter.PHOTO}
