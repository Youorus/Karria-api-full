"""Le CV : ce qui reste quand le brouillon est fini.

Contrat
-------
Un CV par personne. Il porte **exactement** les sections de la V1 —
`summary`, `image_key` (servi en `image_url`), `experiences`, `educations`,
`skills`, `languages`, `interests`, `projects` — plus `identity` et
`contact`, que le contrat a déplacés de l'inscription vers le CV.

Il se construit **depuis un brouillon complet** et rien d'autre : la
conversation est la seule porte d'entrée aujourd'hui. Les routes
d'édition directe de la V1 (`PUT /cv/update`, sous-ressources) viendront
s'y brancher quand elles seront nommées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from karria.modules.cv.domain.chapters import Chapter
from karria.modules.cv.domain.draft import Draft
from karria.modules.cv.domain.errors import DraftIncompleteError
from karria.modules.cv.domain.schemas import (
    Contact,
    Education,
    Experience,
    Identity,
    Interest,
    InterestsChapter,
    Language,
    Project,
    Skill,
    SkillsChapter,
    Summary,
)


@dataclass(frozen=True, slots=True)
class CurriculumVitae:
    """Le CV d'une personne, prêt à être lu, adapté, généré."""

    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    identity: Identity | None = None
    contact: Contact | None = None
    summary: str | None = None
    #: Clé objet de la photo dans le bucket `media`. Jamais une URL.
    image_key: str | None = None
    experiences: tuple[Experience, ...] = ()
    educations: tuple[Education, ...] = ()
    skills: tuple[Skill, ...] = ()
    languages: tuple[Language, ...] = ()
    interests: tuple[Interest, ...] = ()
    projects: tuple[Project, ...] = field(default=())

    @classmethod
    def empty(cls, user_id: UUID, *, now: datetime) -> CurriculumVitae:
        """Le CV d'une personne qui n'en a pas encore : toutes les sections vides.

        Rendu plutôt qu'un 404 : l'application affiche alors « à créer », ce
        qu'elle déduit elle-même des sections vides.
        """
        return cls(id=uuid4(), user_id=user_id, created_at=now, updated_at=now)

    @classmethod
    def from_draft(
        cls,
        draft: Draft,
        *,
        cv_id: UUID,
        image_key: str | None,
        created_at: datetime,
        now: datetime,
    ) -> CurriculumVitae:
        """Assemble le CV à partir des chapitres validés d'un brouillon complet.

        Args:
            cv_id: Identifiant à conserver — celui du CV existant si la
                personne en refait un, pour que les références (CV générés,
                dossiers) restent valides.
            image_key: Clé de la photo retenue, résolue par l'appelant
                depuis l'identifiant du chapitre `photo`.

        Raises:
            DraftIncompleteError: Un chapitre n'est ni validé ni passé.
        """
        if not draft.is_complete:
            raise DraftIncompleteError([c.value for c in draft.incomplete_chapters])

        identity = draft.validated_data(Chapter.IDENTITY)
        contact = draft.validated_data(Chapter.CONTACT)
        summary = draft.validated_data(Chapter.SUMMARY)
        skills = draft.validated_data(Chapter.SKILLS)
        interests = draft.validated_data(Chapter.INTERESTS)
        skills_chapter = SkillsChapter.model_validate(skills) if skills else SkillsChapter()
        interests_chapter = (
            InterestsChapter.model_validate(interests) if interests else InterestsChapter()
        )
        return cls(
            id=cv_id,
            user_id=draft.user_id,
            created_at=created_at,
            updated_at=now,
            identity=Identity.model_validate(identity) if identity else None,
            contact=Contact.model_validate(contact) if contact else None,
            summary=Summary.model_validate(summary).summary if summary else None,
            image_key=image_key,
            experiences=tuple(
                Experience.model_validate(item)
                for item in draft.validated_items(Chapter.EXPERIENCES)
            ),
            educations=tuple(
                Education.model_validate(item) for item in draft.validated_items(Chapter.EDUCATIONS)
            ),
            skills=tuple(skills_chapter.skills),
            languages=tuple(skills_chapter.languages),
            interests=tuple(interests_chapter.interests),
        )

    @property
    def is_blank(self) -> bool:
        """Aucune section remplie : le CV reste à créer."""
        return (
            self.identity is None
            and not self.summary
            and not self.experiences
            and not self.educations
            and not self.skills
        )
