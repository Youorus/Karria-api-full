"""Dépôts Tortoise du module `cv`.

Chaque dépôt traduit entre un modèle de persistance et un objet du domaine,
dans les deux sens, et **relit par le schéma** ce qu'il charge : une ligne
JSON corrompue lève ici, où l'on sait de quelle table elle vient, plutôt
que trois couches plus loin.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from karria.modules.cv.domain import (
    CHAPTER_ORDER,
    Chapter,
    ChapterState,
    ChapterStatus,
    Contact,
    CurriculumVitae,
    CvPhoto,
    Draft,
    Education,
    Experience,
    Identity,
    Interest,
    Language,
    Project,
    Skill,
)
from karria.modules.cv.infrastructure.models import (
    CurriculumVitaeModel,
    CvDraftModel,
    CvPhotoModel,
)


class TortoiseDraftRepository:
    """Implémente `DraftRepository`."""

    async def find_by_user(self, user_id: UUID) -> Draft | None:
        row = await CvDraftModel.get_or_none(user_id=user_id)
        return None if row is None else _draft_from_row(row)

    async def save(self, draft: Draft) -> None:
        chapters = _chapters_to_json(draft)
        row = await CvDraftModel.get_or_none(user_id=draft.user_id)
        if row is None:
            await CvDraftModel.create(id=draft.id, user_id=draft.user_id, chapters=chapters)
            return
        row.chapters = chapters
        await row.save(update_fields=["chapters", "updated_at"])

    async def delete(self, draft_id: UUID) -> None:
        await CvDraftModel.filter(id=draft_id).delete()


class TortoiseCvRepository:
    """Implémente `CvRepository`."""

    async def find_by_user(self, user_id: UUID) -> CurriculumVitae | None:
        row = await CurriculumVitaeModel.get_or_none(user_id=user_id)
        return None if row is None else _cv_from_row(row)

    async def save(self, cv: CurriculumVitae) -> None:
        values = _cv_to_values(cv)
        row = await CurriculumVitaeModel.get_or_none(user_id=cv.user_id)
        if row is None:
            await CurriculumVitaeModel.create(id=cv.id, user_id=cv.user_id, **values)
            return
        for name, value in values.items():
            setattr(row, name, value)
        await row.save(update_fields=[*values, "updated_at"])

    async def delete_by_user(self, user_id: UUID) -> None:
        await CurriculumVitaeModel.filter(user_id=user_id).delete()


class TortoisePhotoRepository:
    """Implémente `PhotoRepository`."""

    async def add(self, photo: CvPhoto) -> None:
        await CvPhotoModel.create(
            id=photo.id, user_id=photo.user_id, image_key=photo.image_key, enhanced=photo.enhanced
        )

    async def find(self, photo_id: UUID, *, user_id: UUID) -> CvPhoto | None:
        row = await CvPhotoModel.get_or_none(id=photo_id, user_id=user_id)
        return None if row is None else _photo_from_row(row)

    async def list_by_user(self, user_id: UUID) -> list[CvPhoto]:
        rows = await CvPhotoModel.filter(user_id=user_id).order_by("created_at")
        return [_photo_from_row(row) for row in rows]

    async def delete(self, photo_ids: Sequence[UUID]) -> None:
        if photo_ids:
            await CvPhotoModel.filter(id__in=list(photo_ids)).delete()


# ---------------------------------------------------------------------
# Traductions
# ---------------------------------------------------------------------


def _chapters_to_json(draft: Draft) -> dict[str, Any]:
    return {
        chapter.value: {
            "status": state.status.value,
            "data": state.data,
            "items": state.items,
        }
        for chapter, state in draft.chapters.items()
    }


def _draft_from_row(row: CvDraftModel) -> Draft:
    stored: dict[str, Any] = row.chapters or {}
    chapters: dict[Chapter, ChapterState] = {}
    for chapter in CHAPTER_ORDER:
        raw = stored.get(chapter.value) or {}
        chapters[chapter] = ChapterState(
            status=ChapterStatus(raw.get("status", ChapterStatus.PENDING.value)),
            data=raw.get("data"),
            items=list(raw.get("items") or []),
        )
    return Draft(
        id=row.id,
        user_id=row.user_id,
        chapters=chapters,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _cv_to_values(cv: CurriculumVitae) -> dict[str, Any]:
    return {
        "identity": cv.identity.model_dump(mode="json") if cv.identity else None,
        "contact": cv.contact.model_dump(mode="json") if cv.contact else None,
        "summary": cv.summary,
        "image_key": cv.image_key,
        "experiences": [item.model_dump(mode="json") for item in cv.experiences],
        "educations": [item.model_dump(mode="json") for item in cv.educations],
        "skills": [item.model_dump(mode="json") for item in cv.skills],
        "languages": [item.model_dump(mode="json") for item in cv.languages],
        "interests": [item.model_dump(mode="json") for item in cv.interests],
        "projects": [item.model_dump(mode="json") for item in cv.projects],
    }


def _cv_from_row(row: CurriculumVitaeModel) -> CurriculumVitae:
    return CurriculumVitae(
        id=row.id,
        user_id=row.user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        identity=Identity.model_validate(row.identity) if row.identity else None,
        contact=Contact.model_validate(row.contact) if row.contact else None,
        summary=row.summary,
        image_key=row.image_key,
        experiences=tuple(Experience.model_validate(item) for item in row.experiences or []),
        educations=tuple(Education.model_validate(item) for item in row.educations or []),
        skills=tuple(Skill.model_validate(item) for item in row.skills or []),
        languages=tuple(Language.model_validate(item) for item in row.languages or []),
        interests=tuple(Interest.model_validate(item) for item in row.interests or []),
        projects=tuple(Project.model_validate(item) for item in row.projects or []),
    )


def _photo_from_row(row: CvPhotoModel) -> CvPhoto:
    return CvPhoto(
        id=row.id,
        user_id=row.user_id,
        image_key=row.image_key,
        enhanced=row.enhanced,
        created_at=row.created_at,
    )
