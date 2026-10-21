"""Le brouillon : le CV en cours d'écriture, chapitre par chapitre.

Contrat
-------
- **Un brouillon par personne.** Il se crée au premier passage, se reprend
  sur n'importe quel appareil, et disparaît à la finalisation.
- **Le chapitre courant se déduit**, il ne se stocke pas : c'est le premier
  chapitre de l'ordre qui n'est ni validé ni passé. Le stocker créerait un
  second état à garder cohérent avec le premier.
- **Valider revalide.** Ce que le client envoie passe par le schéma du
  chapitre ; ce qui en sort est la seule chose qu'on garde.
- **Un chapitre importé est une proposition.** Il est `in_progress` avec
  son contenu : l'utilisateur le voit, le corrige, le valide. Il ne devient
  jamais `validated` sans lui.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from karria.modules.cv.domain.chapters import (
    CHAPTER_ORDER,
    LOOP_CHAPTERS,
    MANDATORY_CHAPTERS,
    Chapter,
    ChapterStatus,
)
from karria.modules.cv.domain.errors import ChapterSubmissionError, ChapterValidationError
from karria.modules.cv.domain.schemas import validate_chapter_payload


@dataclass(slots=True)
class ChapterState:
    """L'état d'un chapitre dans le brouillon.

    Attributes:
        status: Où en est le chapitre.
        data: La valeur d'un chapitre simple — validée, ou proposée.
        items: Les éléments d'un chapitre en boucle — validés, ou proposés.
    """

    status: ChapterStatus = ChapterStatus.PENDING
    data: dict[str, Any] | None = None
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChapterSubmission:
    """Ce qu'un client envoie pour clore un chapitre : une seule des trois formes."""

    data: Any = None
    items: Sequence[Any] | None = None
    skipped: bool = False


@dataclass(slots=True)
class Draft:
    """Le brouillon d'une personne. Agrégat : toute modification passe par ici."""

    id: UUID
    user_id: UUID
    chapters: dict[Chapter, ChapterState]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def new(cls, user_id: UUID, *, now: datetime) -> Draft:
        """Un brouillon vierge : le premier chapitre est ouvert, les autres attendent."""
        chapters = {chapter: ChapterState() for chapter in CHAPTER_ORDER}
        chapters[CHAPTER_ORDER[0]].status = ChapterStatus.IN_PROGRESS
        return cls(id=uuid4(), user_id=user_id, chapters=chapters, created_at=now, updated_at=now)

    @property
    def current_chapter(self) -> Chapter:
        """Le premier chapitre qui reste à faire ; le dernier quand tout est fait."""
        for chapter in CHAPTER_ORDER:
            if not self.chapters[chapter].status.is_done:
                return chapter
        return CHAPTER_ORDER[-1]

    @property
    def incomplete_chapters(self) -> tuple[Chapter, ...]:
        """Ce qui bloque encore la finalisation, dans l'ordre."""
        return tuple(c for c in CHAPTER_ORDER if not self.chapters[c].status.is_done)

    @property
    def is_complete(self) -> bool:
        return not self.incomplete_chapters

    def submit(self, chapter: Chapter, submission: ChapterSubmission, *, now: datetime) -> None:
        """Valide ou passe un chapitre, puis ouvre le suivant.

        Raises:
            ChapterSubmissionError: La forme ne correspond pas au chapitre.
            ChapterValidationError: Le contenu ne respecte pas le schéma.
        """
        state = self.chapters[chapter]
        if submission.skipped:
            if chapter in MANDATORY_CHAPTERS:
                raise ChapterSubmissionError(
                    chapter, "Ce chapitre ne peut pas être passé.", reason="chapter_required"
                )
            state.status = ChapterStatus.SKIPPED
            state.data = None
            state.items = []
        elif chapter in LOOP_CHAPTERS:
            if submission.items is None or submission.data is not None:
                raise ChapterSubmissionError(
                    chapter,
                    "Ce chapitre attend une liste `items`.",
                    reason="chapter_items_expected",
                )
            if not submission.items:
                raise ChapterSubmissionError(
                    chapter,
                    "La liste est vide : passe le chapitre avec `skipped` si tu n'as rien à y mettre.",
                    reason="chapter_items_empty",
                )
            state.items = [self._validated(chapter, item) for item in submission.items]
            state.data = None
            state.status = ChapterStatus.VALIDATED
        else:
            if submission.data is None or submission.items is not None:
                raise ChapterSubmissionError(
                    chapter, "Ce chapitre attend une valeur `data`.", reason="chapter_data_expected"
                )
            state.data = self._validated(chapter, submission.data)
            state.items = []
            state.status = ChapterStatus.VALIDATED

        self._open_next()
        self.updated_at = now

    def propose(
        self, chapter: Chapter, *, data: Any = None, items: Sequence[Any] = (), now: datetime
    ) -> bool:
        """Pose une proposition sur un chapitre non encore validé.

        Les éléments invalides sont ignorés un à un : un import qui tire
        dix expériences d'un PDF ne doit pas échouer parce que la onzième
        n'a pas de nom d'entreprise. Rend `True` si quelque chose a été posé.
        """
        state = self.chapters[chapter]
        if state.status.is_done:
            return False
        if chapter in LOOP_CHAPTERS:
            proposals = [valid for item in items if (valid := self._try_validate(chapter, item))]
            if not proposals:
                return False
            state.items = proposals
        else:
            proposal = self._try_validate(chapter, data) if data is not None else None
            if proposal is None:
                return False
            state.data = proposal
        state.status = ChapterStatus.IN_PROGRESS
        self.updated_at = now
        return True

    def validated_data(self, chapter: Chapter) -> dict[str, Any] | None:
        """La valeur d'un chapitre simple **validé**, sinon `None`."""
        state = self.chapters[chapter]
        return state.data if state.status is ChapterStatus.VALIDATED else None

    def validated_items(self, chapter: Chapter) -> list[dict[str, Any]]:
        """Les éléments d'un chapitre en boucle **validé**, sinon rien."""
        state = self.chapters[chapter]
        return list(state.items) if state.status is ChapterStatus.VALIDATED else []

    def _open_next(self) -> None:
        for chapter in CHAPTER_ORDER:
            state = self.chapters[chapter]
            if not state.status.is_done:
                if state.status is ChapterStatus.PENDING:
                    state.status = ChapterStatus.IN_PROGRESS
                return

    @staticmethod
    def _validated(chapter: Chapter, payload: Any) -> dict[str, Any]:
        try:
            return validate_chapter_payload(chapter, payload)
        except ValidationError as exc:
            fields = [
                {
                    "field": ".".join(str(part) for part in error["loc"]) or "body",
                    "reason": error["msg"],
                }
                for error in exc.errors()
            ]
            raise ChapterValidationError(chapter.value, fields) from exc

    @staticmethod
    def _try_validate(chapter: Chapter, payload: Any) -> dict[str, Any] | None:
        try:
            return validate_chapter_payload(chapter, payload)
        except ValidationError:
            return None
