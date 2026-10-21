"""Cas d'usage du module `cv`.

Chaque classe est une intention du contrat (`docs/contrat-api-cv.md`) :
lire son CV, ouvrir le brouillon, interpréter une réponse, valider un
chapitre, finaliser, transcrire, envoyer une photo, importer un CV. Les
dépendances arrivent par le constructeur ; aucune n'est un adaptateur
concret, et la suite unitaire les remplace toutes par des doublures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID, uuid4

from pydantic import ValidationError

from karria.modules.cv.application.events import CvFinalized
from karria.modules.cv.application.ports import (
    AudioTranscriber,
    ChapterInterpreter,
    CvDocumentExtractor,
    CvRepository,
    DraftRepository,
    Interpretation,
    PhotoRepository,
    PortraitEnhancer,
    UploadInspector,
)
from karria.modules.cv.domain import (
    CHAPTER_SCHEMAS,
    INTERPRETABLE_CHAPTERS,
    LOOP_CHAPTERS,
    Chapter,
    ChapterSubmission,
    ChapterValidationError,
    CurriculumVitae,
    CvPhoto,
    Draft,
    DraftIncompleteError,
    DraftNotFoundError,
    check_audio,
    check_audio_size,
    check_audio_type,
    check_document,
    check_document_size,
    check_document_type,
    plain_content_type,
    validate_chapter_payload,
)
from karria.shared.clock import Clock
from karria.shared.database import UnitOfWork
from karria.shared.events import EventBus
from karria.shared.logging import get_logger
from karria.shared.storage import (
    REGISTRY,
    Bucket,
    ObjectStorage,
    StorageError,
    build_key,
    prepare_upload,
)

logger = get_logger(__name__)

#: Ce que Karria répond quand on lui écrit à propos de la photo : elle se
#: choisit avec des boutons. Même texte que le simulateur de l'application.
PHOTO_FOLLOW_UP: Final = "La photo se choisit avec les boutons, pas besoin de l'écrire."
#: Quand la réponse est trop courte pour en tirer quoi que ce soit.
TOO_SHORT_FOLLOW_UP: Final = "Je n'ai pas bien saisi. Tu peux me le redire en une phrase ?"
#: Quand le modèle n'a rien proposé d'exploitable et n'a rien demandé.
DEFAULT_FOLLOW_UP: Final = "Je n'ai pas bien compris. Tu peux reformuler ?"
#: En dessous, ce n'est pas une réponse : on ne dépense pas un appel.
MIN_ANSWER_LENGTH: Final = 3
#: Une relance tient en une phrase ; au-delà, le modèle divague.
MAX_FOLLOW_UP_LENGTH: Final = 300


class GetMyCv:
    """Le CV de la personne — vide si elle n'en a pas encore."""

    def __init__(self, cvs: CvRepository, clock: Clock) -> None:
        self._cvs = cvs
        self._clock = clock

    async def execute(self, user_id: UUID) -> CurriculumVitae:
        cv = await self._cvs.find_by_user(user_id)
        return cv or CurriculumVitae.empty(user_id, now=self._clock.now())


class OpenDraft:
    """Le brouillon en cours, ou un nouveau. Idempotent."""

    def __init__(self, drafts: DraftRepository, clock: Clock) -> None:
        self._drafts = drafts
        self._clock = clock

    async def execute(self, user_id: UUID) -> Draft:
        draft = await self._drafts.find_by_user(user_id)
        if draft is None:
            draft = Draft.new(user_id, now=self._clock.now())
            await self._drafts.save(draft)
            logger.info("cv.draft_opened", user_id=str(user_id), draft_id=str(draft.id))
        return draft


class GetDraft:
    """Le brouillon en cours, sinon `DraftNotFoundError`."""

    def __init__(self, drafts: DraftRepository) -> None:
        self._drafts = drafts

    async def execute(self, user_id: UUID) -> Draft:
        draft = await self._drafts.find_by_user(user_id)
        if draft is None:
            raise DraftNotFoundError
        return draft


class InterpretAnswer:
    """Une réponse libre devient un premier jet — validé avant d'être rendu.

    La sortie du modèle est une donnée (AGENTS.md §6.14) : la proposition
    passe par le schéma du chapitre, `missing` est filtré sur les champs
    qui existent, la relance est bornée. Une proposition hors schéma n'est
    pas une panne : on la retire et on laisse la relance du modèle — ou une
    relance par défaut — redemander, en le journalisant pour qu'un prompt
    qui dérive se voie.
    """

    def __init__(self, interpreter: ChapterInterpreter) -> None:
        self._interpreter = interpreter

    async def execute(
        self, chapter: Chapter, answer: str, correction_of: dict[str, Any] | None = None
    ) -> Interpretation:
        text = " ".join(answer.split())
        if chapter not in INTERPRETABLE_CHAPTERS:
            return Interpretation(
                proposal=None, missing=(), follow_up=PHOTO_FOLLOW_UP, off_topic=True
            )
        if len(text) < MIN_ANSWER_LENGTH:
            return Interpretation(
                proposal=None, missing=(), follow_up=TOO_SHORT_FOLLOW_UP, off_topic=True
            )

        raw = await self._interpreter.interpret(chapter, text, correction_of)
        follow_up = _bounded_follow_up(raw.follow_up)
        if raw.off_topic or raw.proposal is None:
            return Interpretation(
                proposal=None, missing=(), follow_up=follow_up or DEFAULT_FOLLOW_UP, off_topic=True
            )

        try:
            proposal = validate_chapter_payload(chapter, raw.proposal)
        except ValidationError as exc:
            logger.warning(
                "cv.interpretation_rejected",
                chapter=chapter.value,
                errors=[error["msg"] for error in exc.errors()],
            )
            return Interpretation(
                proposal=None,
                missing=_known_fields(chapter, raw.missing),
                follow_up=follow_up or DEFAULT_FOLLOW_UP,
                off_topic=False,
            )

        return Interpretation(
            proposal=proposal,
            missing=_known_fields(chapter, raw.missing),
            follow_up=follow_up,
            off_topic=False,
        )


def _bounded_follow_up(follow_up: str | None) -> str | None:
    if follow_up is None:
        return None
    text = " ".join(follow_up.split())
    if not text:
        return None
    return text[:MAX_FOLLOW_UP_LENGTH]


def _known_fields(chapter: Chapter, missing: tuple[str, ...]) -> tuple[str, ...]:
    """Ne garde de `missing` que les champs du chapitre, sans doublon."""
    known = CHAPTER_SCHEMAS[chapter].model_fields
    return tuple(dict.fromkeys(name for name in missing if name in known))


class ValidateChapter:
    """Valide — ou passe — un chapitre du brouillon, après revalidation."""

    def __init__(self, drafts: DraftRepository, photos: PhotoRepository, clock: Clock) -> None:
        self._drafts = drafts
        self._photos = photos
        self._clock = clock

    async def execute(
        self, user_id: UUID, chapter: Chapter, submission: ChapterSubmission
    ) -> Draft:
        draft = await self._drafts.find_by_user(user_id)
        if draft is None:
            raise DraftNotFoundError
        if chapter is Chapter.PHOTO and not submission.skipped:
            await self._check_photo_belongs(user_id, submission.data)
        draft.submit(chapter, submission, now=self._clock.now())
        await self._drafts.save(draft)
        logger.info(
            "cv.chapter_submitted",
            user_id=str(user_id),
            chapter=chapter.value,
            status=draft.chapters[chapter].status.value,
        )
        return draft

    async def _check_photo_belongs(self, user_id: UUID, data: Any) -> None:
        """Un identifiant de photo doit désigner une photo **de cette personne**."""
        image_id = data.get("image_id") if isinstance(data, dict) else None
        if image_id is None:
            return
        try:
            photo_id = UUID(str(image_id))
        except ValueError:
            photo_id = None
        if photo_id is None or await self._photos.find(photo_id, user_id=user_id) is None:
            raise ChapterValidationError(
                Chapter.PHOTO.value, [{"field": "image_id", "reason": "Photo inconnue."}]
            )


class FinalizeDraft:
    """Le brouillon complet devient le CV, dans une transaction.

    Ce qui est atomique : écrire le CV, supprimer le brouillon, oublier les
    photos en attente. Ce qui ne l'est pas — et ne peut pas l'être — c'est
    le bucket : les objets des photos non retenues se suppriment **après**
    la validation, et un échec s'y journalise sans annuler le CV. Un
    fichier orphelin coûte quelques centimes ; un CV perdu coûte un
    utilisateur.
    """

    def __init__(
        self,
        *,
        drafts: DraftRepository,
        cvs: CvRepository,
        photos: PhotoRepository,
        storage: ObjectStorage,
        unit_of_work: UnitOfWork,
        events: EventBus,
        clock: Clock,
    ) -> None:
        self._drafts = drafts
        self._cvs = cvs
        self._photos = photos
        self._storage = storage
        self._uow = unit_of_work
        self._events = events
        self._clock = clock

    async def execute(self, user_id: UUID) -> CurriculumVitae:
        draft = await self._drafts.find_by_user(user_id)
        if draft is None:
            raise DraftNotFoundError
        if not draft.is_complete:
            raise DraftIncompleteError([c.value for c in draft.incomplete_chapters])

        now = self._clock.now()
        chosen = await self._chosen_photo(draft, user_id)
        existing = await self._cvs.find_by_user(user_id)
        cv = CurriculumVitae.from_draft(
            draft,
            cv_id=existing.id if existing else uuid4(),
            image_key=chosen.image_key if chosen else None,
            created_at=existing.created_at if existing else now,
            now=now,
        )
        pending = await self._photos.list_by_user(user_id)

        async with self._uow.atomic():
            await self._cvs.save(cv)
            await self._drafts.delete(draft.id)
            await self._photos.delete([photo.id for photo in pending])

        obsolete = [photo.image_key for photo in pending if chosen is None or photo.id != chosen.id]
        if existing and existing.image_key and existing.image_key != cv.image_key:
            obsolete.append(existing.image_key)
        await self._forget(obsolete)

        await self._events.publish(
            CvFinalized(
                user_id=user_id,
                cv_id=cv.id,
                first_name=cv.identity.first_name if cv.identity else None,
                last_name=cv.identity.last_name if cv.identity else None,
                headline=cv.identity.headline if cv.identity else None,
                email=cv.contact.email if cv.contact else None,
                phone=cv.contact.phone if cv.contact else None,
                city=cv.contact.city if cv.contact else None,
            )
        )
        logger.info(
            "cv.finalized", user_id=str(user_id), cv_id=str(cv.id), replaced=existing is not None
        )
        return cv

    async def _chosen_photo(self, draft: Draft, user_id: UUID) -> CvPhoto | None:
        data = draft.validated_data(Chapter.PHOTO)
        image_id = data.get("image_id") if data else None
        if image_id is None:
            return None
        photo = await self._photos.find(UUID(str(image_id)), user_id=user_id)
        if photo is None:
            # Validée puis disparue : le chapitre n'est plus vrai.
            raise ChapterValidationError(
                Chapter.PHOTO.value, [{"field": "image_id", "reason": "Photo inconnue."}]
            )
        return photo

    async def _forget(self, keys: list[str]) -> None:
        for key in keys:
            try:
                await self._storage.delete(Bucket.MEDIA, key)
            except StorageError as exc:
                logger.warning("cv.photo_cleanup_failed", key=key, detail=exc.details)


class TranscribeVoice:
    """Un enregistrement devient du texte, après contrôle du fichier."""

    def __init__(self, transcriber: AudioTranscriber, inspector: UploadInspector) -> None:
        self._transcriber = transcriber
        self._inspector = inspector

    async def execute(self, audio: bytes, declared_content_type: str | None) -> str:
        content_type = plain_content_type(declared_content_type)
        check_audio_type(content_type)
        check_audio_size(len(audio))
        info = self._inspector.audio(audio, content_type)
        check_audio(info)
        text = await self._transcriber.transcribe(audio, info.content_type)
        logger.info(
            "cv.voice_transcribed",
            bytes=len(audio),
            duration=info.duration_seconds,
            chars=len(text),
        )
        return " ".join(text.split())


class UploadPhoto:
    """Range une photo dans le bucket — améliorée d'abord si on le demande."""

    def __init__(
        self,
        enhancer: PortraitEnhancer,
        storage: ObjectStorage,
        photos: PhotoRepository,
        clock: Clock,
    ) -> None:
        self._enhancer = enhancer
        self._storage = storage
        self._photos = photos
        self._clock = clock

    async def execute(
        self, user_id: UUID, image: bytes, declared_content_type: str | None, *, enhance: bool
    ) -> CvPhoto:
        """Écrit la photo et rend sa référence.

        L'image est normalisée **deux fois** quand elle est améliorée : à
        l'entrée, pour ne pas envoyer au générateur 12 mégapixels et des
        coordonnées GPS ; à la sortie, parce que ce que rend le générateur
        n'échappe pas à la politique du bucket (ADR-0009).
        """
        prepared = prepare_upload(Bucket.MEDIA, image, declared_content_type or "")
        if enhance:
            generated = await self._enhancer.enhance(prepared.payload, prepared.content_type)
            prepared = prepare_upload(Bucket.MEDIA, generated.payload, generated.content_type)

        now = self._clock.now()
        key = build_key("users", str(user_id), "cv-photo", extension=prepared.extension, at=now)
        await self._storage.put(
            Bucket.MEDIA,
            key,
            prepared.payload,
            content_type=prepared.content_type,
            cache_seconds=REGISTRY[Bucket.MEDIA].cache_seconds,
        )
        photo = CvPhoto(
            id=uuid4(), user_id=user_id, image_key=key, enhanced=enhance, created_at=now
        )
        await self._photos.add(photo)
        logger.info(
            "cv.photo_stored", user_id=str(user_id), photo_id=str(photo.id), enhanced=enhance
        )
        return photo


class ImportCv:
    """Un CV existant devient un brouillon prérempli — à valider chapitre par chapitre.

    Rien n'est validé à la place de la personne : chaque chapitre extrait
    est posé en **proposition** (`in_progress`), et un chapitre qu'elle a
    déjà validé n'est pas touché. La V1 écrasait le CV à chaque import, y
    compris quand l'extraction avait échoué : c'est le défaut qu'on ne
    reproduit pas.
    """

    def __init__(
        self,
        extractor: CvDocumentExtractor,
        inspector: UploadInspector,
        drafts: DraftRepository,
        clock: Clock,
    ) -> None:
        self._extractor = extractor
        self._inspector = inspector
        self._drafts = drafts
        self._clock = clock

    async def execute(
        self, user_id: UUID, document: bytes, declared_content_type: str | None
    ) -> Draft:
        content_type = plain_content_type(declared_content_type)
        check_document_type(content_type)
        check_document_size(len(document))
        info = self._inspector.document(document, content_type)
        check_document(info)

        extracted = await self._extractor.extract(info.payload, info.content_type)

        now = self._clock.now()
        draft = await self._drafts.find_by_user(user_id) or Draft.new(user_id, now=now)
        proposed: list[str] = []
        for chapter, value in extracted.items():
            if chapter in LOOP_CHAPTERS:
                items = value if isinstance(value, list) else []
                placed = draft.propose(chapter, items=items, now=now)
            else:
                placed = draft.propose(chapter, data=value, now=now)
            if placed:
                proposed.append(chapter.value)
        await self._drafts.save(draft)
        logger.info(
            "cv.imported",
            user_id=str(user_id),
            content_type=info.content_type,
            pages=info.pages,
            chapters=proposed,
        )
        return draft


# ---------------------------------------------------------------------
# Quand un compte disparaît
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForgetOutcome:
    """Ce que l'oubli d'une personne a effacé — et ce qu'il n'a pas pu.

    Attributes:
        objects_deleted: Objets effacés du bucket.
        objects_failed: Clés dont la suppression a échoué. Les lignes qui
            les portent sont **conservées** pour qu'un rejeu les retrouve.
    """

    draft_deleted: bool
    cv_deleted: bool
    photos_deleted: int
    objects_deleted: int
    objects_failed: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Tout est parti, lignes et objets."""
        return not self.objects_failed


class ForgetUser:
    """Efface tout ce que le module détient d'un compte — réaction à `AccountDeleted`.

    L'ordre est l'inverse de la finalisation, et pour une raison précise :
    **les objets d'abord, les lignes ensuite**. Une ligne porte la clé de son
    objet ; tant qu'elle existe, un rejeu retrouve l'objet et peut réessayer.
    Effacer la ligne d'abord et rater l'objet laisserait une photo orpheline
    que plus rien ne désigne. Donc : chaque objet est supprimé ; les lignes
    dont l'objet a bien disparu sont effacées dans une transaction ; celles
    dont l'objet résiste restent, et l'échec est journalisé avec ses clés
    (`cv.forget_incomplete`). Rejouer l'événement termine le travail — le
    cas d'usage est idempotent.

    Ce que ça coûte : en cas de panne du stockage, un CV orphelin reste en
    base jusqu'au rejeu. Ce qu'on y gagne : aucune donnée n'est jamais
    perdue de vue (ADR-0015).
    """

    def __init__(
        self,
        *,
        drafts: DraftRepository,
        cvs: CvRepository,
        photos: PhotoRepository,
        storage: ObjectStorage,
        unit_of_work: UnitOfWork,
    ) -> None:
        self._drafts = drafts
        self._cvs = cvs
        self._photos = photos
        self._storage = storage
        self._uow = unit_of_work

    async def execute(self, user_id: UUID) -> ForgetOutcome:
        """Efface brouillon, CV, photos et objets de la personne."""
        draft = await self._drafts.find_by_user(user_id)
        cv = await self._cvs.find_by_user(user_id)
        photos = await self._photos.list_by_user(user_id)

        keys = {photo.image_key for photo in photos}
        if cv is not None and cv.image_key is not None:
            keys.add(cv.image_key)
        failed = await self._delete_objects(sorted(keys))

        forget_cv = cv is not None and cv.image_key not in failed
        photo_ids = [photo.id for photo in photos if photo.image_key not in failed]
        async with self._uow.atomic():
            if draft is not None:
                await self._drafts.delete(draft.id)
            if photo_ids:
                await self._photos.delete(photo_ids)
            if forget_cv:
                await self._cvs.delete_by_user(user_id)

        outcome = ForgetOutcome(
            draft_deleted=draft is not None,
            cv_deleted=forget_cv,
            photos_deleted=len(photo_ids),
            objects_deleted=len(keys) - len(failed),
            objects_failed=tuple(sorted(failed)),
        )
        if outcome.complete:
            logger.info(
                "cv.user_forgotten",
                user_id=str(user_id),
                draft=outcome.draft_deleted,
                cv=outcome.cv_deleted,
                photos=outcome.photos_deleted,
                objects=outcome.objects_deleted,
            )
        else:
            # Niveau erreur : ce n'est pas un incident de confort, c'est une
            # donnée personnelle qui reste. Les clés sont là pour le rejeu.
            logger.error(
                "cv.forget_incomplete",
                user_id=str(user_id),
                objects_failed=list(outcome.objects_failed),
                rows_kept=len(photos) - outcome.photos_deleted + (cv is not None and not forget_cv),
            )
        return outcome

    async def _delete_objects(self, keys: list[str]) -> set[str]:
        """Supprime chaque objet ; rend les clés qui ont résisté."""
        failed: set[str] = set()
        for key in keys:
            try:
                await self._storage.delete(Bucket.MEDIA, key)
            except StorageError:
                logger.warning("cv.object_delete_failed", bucket=Bucket.MEDIA.value, key=key)
                failed.add(key)
        return failed
