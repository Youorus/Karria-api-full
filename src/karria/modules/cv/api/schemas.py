"""Schémas HTTP du module `cv` — le contrat de `docs/contrat-api-cv.md`.

Les sections du CV (`Experience`, `Education`, `Skill`…) sont les modèles
du domaine eux-mêmes : leurs noms de champs **sont** le contrat (ceux de la
V1), et la route qui reçoit un chapitre revalide avec exactement ce schéma.
Les dupliquer ici créerait deux vérités à garder alignées. Les enveloppes
— CV, brouillon, interprétation, photo — sont en revanche propres à HTTP :
c'est là que la clé objet devient une URL signée, et nulle part ailleurs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from karria.modules.cv.domain import (
    Chapter,
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
from karria.shared.storage import Bucket, ObjectStorage

#: Produit l'URL signée d'une photo (identifiant de photo, identifiant du
#: propriétaire), ou `None` si la photo n'existe plus. Injecté par
#: `api/dependencies.py`.
PhotoUrlResolver = Callable[[UUID, UUID], Awaitable[str | None]]


class CvRead(BaseModel):
    """Le CV, au format `GET /cv/me` de la V1, plus `identity` et `contact`."""

    id: UUID
    summary: str | None
    image_url: str | None = Field(
        description="URL signée, recalculée à chaque lecture. Ne pas persister."
    )
    identity: Identity | None
    contact: Contact | None
    experiences: list[Experience]
    educations: list[Education]
    skills: list[Skill]
    languages: list[Language]
    interests: list[Interest]
    projects: list[Project]
    created_at: datetime
    updated_at: datetime

    @classmethod
    async def present(cls, cv: CurriculumVitae, storage: ObjectStorage) -> CvRead:
        """Projette le CV sur le contrat ; la clé de photo devient une URL signée."""
        return cls(
            id=cv.id,
            summary=cv.summary,
            image_url=(
                await storage.presigned_get_url(Bucket.MEDIA, cv.image_key)
                if cv.image_key
                else None
            ),
            identity=cv.identity,
            contact=cv.contact,
            experiences=list(cv.experiences),
            educations=list(cv.educations),
            skills=list(cv.skills),
            languages=list(cv.languages),
            interests=list(cv.interests),
            projects=list(cv.projects),
            created_at=cv.created_at,
            updated_at=cv.updated_at,
        )


class ChapterRead(BaseModel):
    """L'état d'un chapitre : `data` pour un chapitre simple, `items` pour une boucle."""

    status: ChapterStatus
    data: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = None


class DraftRead(BaseModel):
    """Le brouillon, tel que l'application le reprend."""

    id: UUID
    current_chapter: Chapter
    chapters: dict[Chapter, ChapterRead]
    updated_at: datetime


class DraftPresenter:
    """Projette un brouillon ; la photo retenue reçoit son URL signée du moment."""

    def __init__(self, photo_url: PhotoUrlResolver) -> None:
        self._photo_url = photo_url

    async def present(self, draft: Draft) -> DraftRead:
        chapters: dict[Chapter, ChapterRead] = {}
        for chapter, state in draft.chapters.items():
            data = state.data
            if chapter is Chapter.PHOTO and data is not None:
                data = await self._with_photo_url(data, draft.user_id)
            chapters[chapter] = ChapterRead(
                status=state.status,
                data=data,
                items=list(state.items) if state.items else None,
            )
        return DraftRead(
            id=draft.id,
            current_chapter=draft.current_chapter,
            chapters=chapters,
            updated_at=draft.updated_at,
        )

    async def _with_photo_url(self, data: dict[str, Any], user_id: UUID) -> dict[str, Any]:
        image_id = data.get("image_id")
        url = await self._photo_url(UUID(str(image_id)), user_id) if image_id else None
        return {**data, "image_url": url}


class InterpretRequest(BaseModel):
    """Une réponse libre à interpréter pour un chapitre."""

    model_config = ConfigDict(extra="forbid")

    chapter: Chapter
    answer: str = Field(
        min_length=1,
        max_length=4000,
        description="La réponse de la personne, écrite ou transcrite. Traitée comme une donnée.",
    )
    correction_of: dict[str, Any] | None = Field(
        default=None, description="Le jet à corriger ; la réponse dit alors ce qui ne va pas."
    )


class InterpretationRead(BaseModel):
    """Le premier jet, ou une relance."""

    proposal: dict[str, Any] | None = Field(
        description="Le jet, au format du chapitre. Déjà validé."
    )
    missing: list[str] = Field(description="Champs importants absents de la réponse.")
    follow_up: str | None = Field(description="Une seule question, la plus utile.")
    off_topic: bool = Field(description="Vrai si la réponse ne parlait pas du chapitre.")


class ChapterSubmissionRequest(BaseModel):
    """Clore un chapitre : `data`, `items` **ou** `skipped`, jamais deux à la fois."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    skipped: bool = False

    @model_validator(mode="after")
    def _exactly_one_form(self) -> ChapterSubmissionRequest:
        forms = sum((self.data is not None, self.items is not None, self.skipped))
        if forms != 1:
            msg = "Envoyer `data`, `items` ou `skipped: true`, et un seul des trois."
            raise ValueError(msg)
        return self


class TranscriptionRead(BaseModel):
    """Le texte entendu. Vide si rien n'était audible."""

    text: str


class PhotoRead(BaseModel):
    """Une photo écrite, à retenir ensuite par `image_id` dans le chapitre `photo`."""

    image_id: UUID
    image_url: str = Field(description="URL signée pour l'afficher. Expire ; ne pas persister.")
    enhanced: bool

    @classmethod
    async def present(cls, photo: CvPhoto, storage: ObjectStorage) -> PhotoRead:
        return cls(
            image_id=photo.id,
            image_url=await storage.presigned_get_url(Bucket.MEDIA, photo.image_key),
            enhanced=photo.enhanced,
        )
