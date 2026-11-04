"""Le CV : ce qui reste quand le brouillon est fini.

Contrat
-------
Un CV par personne. Il porte **exactement** les sections de la V1 —
`summary`, `image_key` (servi en `image_url`), `experiences`, `educations`,
`skills`, `languages`, `interests`, `projects` — plus `identity` et
`contact`, que le contrat a déplacés de l'inscription vers le CV.

Trois portes d'entrée, et une seule forme :

- **depuis un brouillon complet** (`from_draft`) — la conversation ;
- **depuis une extraction** (`from_extraction`) — l'import d'un CV
  existant, qui écrit le CV directement ; chaque élément est revalidé et
  ceux qui ne tiennent pas sont **comptés**, pas cachés, pour que
  l'application dise ce qu'elle a vraiment trouvé ;
- **chapitre par chapitre** (`with_chapter`) — l'édition directe, qui
  remplace la section d'un bloc, revalidée par le schéma du chapitre.

Ce qui reste à **vérifier** après un import (`missing_fields`) est une
règle du domaine aussi : un CV sans téléphone ni e-mail ne sert à rien,
une expérience sans date se lit mal — c'est ce que l'application met en
avant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from pydantic import ValidationError

from karria.modules.cv.domain.chapters import LOOP_CHAPTERS, MANDATORY_CHAPTERS, Chapter
from karria.modules.cv.domain.draft import (
    ChapterSubmission,
    Draft,
    validated_or_none,
    validated_or_raise,
)
from karria.modules.cv.domain.errors import ChapterSubmissionError, DraftIncompleteError
from karria.modules.cv.domain.schemas import (
    Contact,
    CvModel,
    Education,
    Experience,
    Identity,
    Interest,
    InterestsChapter,
    Language,
    Link,
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

    @classmethod
    def from_extraction(
        cls,
        extracted: Mapping[Chapter, Any],
        *,
        user_id: UUID,
        cv_id: UUID,
        image_key: str | None,
        created_at: datetime,
        now: datetime,
    ) -> ImportAssembly:
        """Assemble le CV depuis ce qu'un modèle a lu d'un document.

        La sortie du modèle est une donnée (AGENTS.md §6.14) : chaque
        chapitre simple et chaque élément de liste repasse par son schéma.
        Ce qui ne tient pas est écarté **et compté** dans `discarded` ; une
        liste qui n'en est pas une vaut une liste vide. La photo n'est pas
        lue dans le document : `image_key` est celle que la personne avait
        déjà, ou rien.
        """
        kept: dict[str, int] = {}
        discarded: dict[str, int] = {}

        def single(chapter: Chapter) -> dict[str, Any] | None:
            raw = extracted.get(chapter)
            if raw is None:
                kept[chapter.value] = 0
                return None
            value = validated_or_none(chapter, raw)
            kept[chapter.value] = 0 if value is None else 1
            if value is None:
                discarded[chapter.value] = 1
            return value

        def items(chapter: Chapter) -> list[dict[str, Any]]:
            raw = extracted.get(chapter)
            candidates = raw if isinstance(raw, list) else []
            valid = [v for item in candidates if (v := validated_or_none(chapter, item))]
            kept[chapter.value] = len(valid)
            if len(valid) < len(candidates):
                discarded[chapter.value] = len(candidates) - len(valid)
            return valid

        identity = single(Chapter.IDENTITY)
        contact = single(Chapter.CONTACT)
        if contact is None and isinstance(extracted.get(Chapter.CONTACT), dict):
            contact = _repaired_contact(extracted[Chapter.CONTACT])
            if contact is not None:
                kept[Chapter.CONTACT.value] = 1
                discarded.pop(Chapter.CONTACT.value, None)
        summary = single(Chapter.SUMMARY)
        skills, dropped_skills = _lenient_skills(extracted.get(Chapter.SKILLS))
        interests, dropped_interests = _lenient_interests(extracted.get(Chapter.INTERESTS))
        experiences = items(Chapter.EXPERIENCES)
        educations = items(Chapter.EDUCATIONS)
        kept["skills"] = len(skills.skills)
        kept["languages"] = len(skills.languages)
        kept["interests"] = len(interests.interests)
        if dropped_skills:
            discarded["skills"] = dropped_skills
        if dropped_interests:
            discarded["interests"] = dropped_interests

        cv = cls(
            id=cv_id,
            user_id=user_id,
            created_at=created_at,
            updated_at=now,
            identity=Identity.model_validate(identity) if identity else None,
            contact=Contact.model_validate(contact) if contact else None,
            summary=Summary.model_validate(summary).summary if summary else None,
            image_key=image_key,
            experiences=tuple(Experience.model_validate(item) for item in experiences),
            educations=tuple(Education.model_validate(item) for item in educations),
            skills=tuple(skills.skills),
            languages=tuple(skills.languages),
            interests=tuple(interests.interests),
        )
        return ImportAssembly(cv=cv, kept=kept, discarded=discarded)

    def with_chapter(
        self, chapter: Chapter, submission: ChapterSubmission, *, now: datetime
    ) -> CurriculumVitae:
        """Remplace une section d'un bloc, revalidée par le schéma du chapitre.

        Les formes sont celles du brouillon (`data`, `items`, `skipped`), à
        deux nuances près, parce qu'un CV n'est pas un parcours : `skipped`
        **vide** la section (rien à passer), et une liste vide est
        acceptée (on peut retirer sa dernière expérience). `identity` et
        `contact` ne se vident pas. La photo ne passe pas ici : son
        identifiant se résout hors du domaine (`with_image_key`).

        Raises:
            ChapterSubmissionError: La forme ne correspond pas au chapitre.
            ChapterValidationError: Le contenu ne respecte pas le schéma.
        """
        if chapter is Chapter.PHOTO:
            raise ChapterSubmissionError(
                chapter.value,
                "La photo se change par son identifiant, pas par ce chapitre.",
                reason="chapter_data_expected",
            )
        if submission.skipped:
            if chapter in MANDATORY_CHAPTERS:
                raise ChapterSubmissionError(
                    chapter.value,
                    "Cette section ne peut pas être vidée.",
                    reason="chapter_required",
                )
            return self._cleared(chapter, now=now)
        if chapter in LOOP_CHAPTERS:
            if submission.items is None or submission.data is not None:
                raise ChapterSubmissionError(
                    chapter.value,
                    "Cette section attend une liste `items`.",
                    reason="chapter_items_expected",
                )
            validated = [validated_or_raise(chapter, item) for item in submission.items]
            return self._with_items(chapter, validated, now=now)
        if submission.data is None or submission.items is not None:
            raise ChapterSubmissionError(
                chapter.value,
                "Cette section attend une valeur `data`.",
                reason="chapter_data_expected",
            )
        return self._with_data(chapter, validated_or_raise(chapter, submission.data), now=now)

    def with_image_key(self, image_key: str | None, *, now: datetime) -> CurriculumVitae:
        """La photo retenue — ou aucune."""
        return replace(self, image_key=image_key, updated_at=now)

    def _cleared(self, chapter: Chapter, *, now: datetime) -> CurriculumVitae:
        if chapter is Chapter.SUMMARY:
            return replace(self, summary=None, updated_at=now)
        if chapter is Chapter.SKILLS:
            return replace(self, skills=(), languages=(), updated_at=now)
        if chapter is Chapter.INTERESTS:
            return replace(self, interests=(), updated_at=now)
        return self._with_items(chapter, [], now=now)

    def _with_items(
        self, chapter: Chapter, items: Sequence[dict[str, Any]], *, now: datetime
    ) -> CurriculumVitae:
        if chapter is Chapter.EXPERIENCES:
            return replace(
                self,
                experiences=tuple(Experience.model_validate(i) for i in items),
                updated_at=now,
            )
        return replace(
            self, educations=tuple(Education.model_validate(i) for i in items), updated_at=now
        )

    def _with_data(
        self, chapter: Chapter, data: dict[str, Any], *, now: datetime
    ) -> CurriculumVitae:
        if chapter is Chapter.IDENTITY:
            return replace(self, identity=Identity.model_validate(data), updated_at=now)
        if chapter is Chapter.CONTACT:
            return replace(self, contact=Contact.model_validate(data), updated_at=now)
        if chapter is Chapter.SUMMARY:
            return replace(self, summary=Summary.model_validate(data).summary, updated_at=now)
        if chapter is Chapter.SKILLS:
            skills = SkillsChapter.model_validate(data)
            return replace(
                self, skills=tuple(skills.skills), languages=tuple(skills.languages), updated_at=now
            )
        interests = InterestsChapter.model_validate(data)
        return replace(self, interests=tuple(interests.interests), updated_at=now)

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

    @property
    def missing_fields(self) -> tuple[str, ...]:
        """Ce qu'il faut vérifier ou compléter, dans l'ordre où l'app le montre.

        Les coordonnées d'abord — sans elles, aucun recruteur ne rappelle —
        puis l'identité, le résumé, les expériences et formations (présence
        et dates), les compétences. Des chemins stables (`contact.phone`,
        `experiences.dates`), jamais des phrases : l'application les
        traduit.
        """
        missing: list[str] = []
        if self.contact is None or self.contact.phone is None:
            missing.append("contact.phone")
        if self.contact is None or self.contact.email is None:
            missing.append("contact.email")
        if self.contact is None or self.contact.city is None:
            missing.append("contact.city")
        if self.identity is None:
            missing.append("identity")
        elif self.identity.headline is None:
            missing.append("identity.headline")
        if not self.summary:
            missing.append("summary")
        if not self.experiences:
            missing.append("experiences")
        elif any(item.start_date is None for item in self.experiences):
            missing.append("experiences.dates")
        if not self.educations:
            missing.append("educations")
        elif any(item.start_date is None and item.end_date is None for item in self.educations):
            missing.append("educations.dates")
        if not self.skills:
            missing.append("skills")
        return tuple(missing)


_PHONE = re.compile(r"(?:\+|00)?\d[\d .()\-]{6,}\d")


def _repaired_contact(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Un contact lu mais refusé en bloc : on sauve ce qui se sauve.

    Constaté sur un vrai CV (2026-11-04) : le modèle recopie deux numéros dans
    `phone` (« 06 … (+33 7 …) »), la chaîne dépasse 30 caractères et **tout**
    le contact tombe — e-mail et ville compris. On garde donc le premier
    numéro, et chaque lien se juge seul. Si même ainsi rien ne tient (ni
    e-mail ni téléphone), le contact reste absent et `missing` le dit.
    """
    phone = raw.get("phone")
    if isinstance(phone, str):
        found = _PHONE.search(phone)
        phone = found.group(0).strip() if found else None
    links = raw.get("links")
    valid_links = (
        [link for link in links if validated_or_none_link(link) is not None]
        if isinstance(links, list)
        else []
    )
    candidate = {**raw, "phone": phone, "links": valid_links}
    return validated_or_none(Chapter.CONTACT, candidate)


def validated_or_none_link(link: Any) -> dict[str, Any] | None:
    try:
        return Link.model_validate(link).model_dump(mode="json")
    except ValidationError:
        return None


def _lenient_items(model: type[CvModel], raw: Any) -> tuple[list[dict[str, Any]], int]:
    """Valide une liste élément par élément ; rend ce qui tient et le nombre d'écartés."""
    if not isinstance(raw, list):
        return [], 0
    kept: list[dict[str, Any]] = []
    for item in raw:
        try:
            kept.append(model.model_validate(item).model_dump(mode="json"))
        except ValidationError:
            continue
    return kept, len(raw) - len(kept)


def _lenient_skills(raw: Any) -> tuple[SkillsChapter, int]:
    """Compétences et langues, chacune jugée seule : un niveau inconnu ne vide pas le chapitre."""
    if not isinstance(raw, dict):
        return SkillsChapter(), 0
    skills, dropped_skills = _lenient_items(Skill, raw.get("skills"))
    languages, dropped_languages = _lenient_items(Language, raw.get("languages"))
    chapter = SkillsChapter.model_validate(
        {"skills": skills[:_MAX_SKILLS], "languages": languages[:_MAX_LANGUAGES]}
    )
    return chapter, dropped_skills + dropped_languages


def _lenient_interests(raw: Any) -> tuple[InterestsChapter, int]:
    if not isinstance(raw, dict):
        return InterestsChapter(), 0
    interests, dropped = _lenient_items(Interest, raw.get("interests"))
    return InterestsChapter.model_validate({"interests": interests[:_MAX_INTERESTS]}), dropped


#: Les plafonds des chapitres (`schemas.py`) : au-delà, on tronque au lieu de tout refuser.
_MAX_SKILLS: Final = 40
_MAX_LANGUAGES: Final = 15
_MAX_INTERESTS: Final = 20


@dataclass(frozen=True, slots=True)
class ImportAssembly:
    """Ce que l'import a assemblé, et ce qu'il a écarté.

    Attributes:
        kept: Par section, le nombre d'éléments retenus (`0`/`1` pour une
            section simple).
        discarded: Par section, le nombre d'éléments lus par le modèle mais
            refusés par le schéma. Absent quand rien n'a été écarté.
    """

    cv: CurriculumVitae
    kept: dict[str, int]
    discarded: dict[str, int]
