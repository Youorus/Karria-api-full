"""Cas d'usage du module `cv`.

Chaque classe est une intention du contrat (`docs/contrat-api-cv.md`) :
lire son CV, ouvrir le brouillon, interpréter une réponse, valider un
chapitre, finaliser, transcrire, envoyer une photo, importer un CV,
modifier une section du CV, suggérer une valeur à la saisie et
matérialiser le vocabulaire qui les nourrit. Les
dépendances arrivent par le constructeur ; aucune n'est un adaptateur
concret, et la suite unitaire les remplace toutes par des doublures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
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
    OfferVocabulary,
    PhotoRepository,
    PortraitEnhancer,
    SuggestionIndex,
    UploadInspector,
)
from karria.modules.cv.domain import (
    CHAPTER_SCHEMAS,
    INTERPRETABLE_CHAPTERS,
    MATERIALIZED_KINDS,
    MAX_DOCUMENT_PAGES,
    REFERENCE_KINDS,
    SUGGESTION_LIMIT,
    Chapter,
    ChapterSubmission,
    ChapterSubmissionError,
    ChapterValidationError,
    CurriculumVitae,
    CvNotFoundError,
    CvPhoto,
    DocumentInfo,
    Draft,
    DraftIncompleteError,
    DraftNotFoundError,
    PhotoMissingError,
    ReceivedFile,
    SuggestionKind,
    UnsupportedUploadError,
    check_audio,
    check_audio_size,
    check_audio_type,
    check_document,
    check_documents,
    clean_vocabulary,
    normalize_query,
    plain_content_type,
    suggest_from_reference,
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


class EnhanceCurrentPhoto:
    """Retravaille la photo **déjà** sur le CV, sans la rechoisir.

    L'amélioration n'existait qu'à l'envoi (`POST /cv/photo`, `enhance`) :
    depuis l'écran du CV, la personne a une photo en place et veut la
    rendre plus professionnelle sans repasser par la galerie. On relit
    l'objet du bucket et on le confie à `UploadPhoto` avec `enhance` — même
    normalisation, même rangement, même référence `CvPhoto` en attente. Le
    CV ne change pas ici : la personne compare, puis retient l'améliorée par
    `PUT /cv/me/chapters/photo` ou garde l'originale, qui est restée.
    """

    def __init__(self, cvs: CvRepository, storage: ObjectStorage, upload: UploadPhoto) -> None:
        self._cvs = cvs
        self._storage = storage
        self._upload = upload

    async def execute(self, user_id: UUID) -> CvPhoto:
        """Rend la photo améliorée, en attente d'être retenue.

        Raises:
            CvNotFoundError: La personne n'a pas de CV.
            PhotoMissingError: Le CV n'a pas de photo.
            PhotoEnhanceRefusedError: Le générateur refuse cette image.
            PhotoEnhanceUnavailableError: Panne, ou rien d'exploitable rendu.
        """
        cv = await self._cvs.find_by_user(user_id)
        if cv is None:
            raise CvNotFoundError
        if cv.image_key is None:
            raise PhotoMissingError
        # Le type réel se lit dans les octets (`prepare_upload`) : rien à annoncer.
        original = await self._storage.get(Bucket.MEDIA, cv.image_key)
        photo = await self._upload.execute(user_id, original, None, enhance=True)
        logger.info("cv.current_photo_enhanced", user_id=str(user_id), photo_id=str(photo.id))
        return photo


@dataclass(frozen=True, slots=True)
class ImportSource:
    """Ce qui a été lu : le type, le nombre de fichiers et de pages."""

    content_type: str
    files: int
    pages: int | None


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Le CV écrit par l'import, et le compte rendu honnête de la lecture.

    Attributes:
        kept: Par section, ce qui a été retenu (`experiences: 4`).
        discarded: Par section, ce que le modèle a lu mais que le schéma a
            refusé — l'application le dit plutôt que de le taire.
        missing: Les champs à vérifier, dans l'ordre d'importance
            (`CurriculumVitae.missing_fields`).
    """

    cv: CurriculumVitae
    source: ImportSource
    kept: dict[str, int] = field(default_factory=dict)
    discarded: dict[str, int] = field(default_factory=dict)
    missing: tuple[str, ...] = ()


class ImportCv:
    """Un CV existant devient **le CV** de la personne — à vérifier ensuite, pas à revalider.

    Décision du 2026-11-04 (retour de Marc sur l'appareil) : répondre à
    chaque question est long, et la plupart des gens ont déjà un CV. L'import
    écrit donc directement le CV, et l'application mène la personne à ses
    coordonnées, ses expériences et ses formations pour les **vérifier** —
    avec ce qui manque mis en avant (`missing`). Le brouillon de la
    conversation, s'il y en avait un, est supprimé : le CV existe désormais,
    et une finalisation ultérieure l'aurait écrasé sans prévenir.

    Ce qu'on garde de la version précédente : rien n'est écrit si la
    lecture échoue (la V1 écrasait le CV par une structure vide) ; la
    photo n'est pas lue dans le document, celle que la personne avait reste.
    Plusieurs fichiers = les photos des pages d'un même CV, lues ensemble.
    """

    def __init__(
        self,
        *,
        extractor: CvDocumentExtractor,
        inspector: UploadInspector,
        cvs: CvRepository,
        drafts: DraftRepository,
        unit_of_work: UnitOfWork,
        events: EventBus,
        clock: Clock,
    ) -> None:
        self._extractor = extractor
        self._inspector = inspector
        self._cvs = cvs
        self._drafts = drafts
        self._uow = unit_of_work
        self._events = events
        self._clock = clock

    async def execute(self, user_id: UUID, files: Sequence[ReceivedFile]) -> ImportResult:
        received = [
            ReceivedFile(payload=f.payload, content_type=plain_content_type(f.content_type))
            for f in files
        ]
        check_documents(received)
        documents: list[DocumentInfo] = []
        for f in received:
            info = self._inspector.document(f.payload, f.content_type)
            check_document(info)
            documents.append(info)
        pages = _total_pages(documents)
        if pages is not None and pages > MAX_DOCUMENT_PAGES_TOTAL:
            raise UnsupportedUploadError(
                f"Le document fait {pages} pages ; un CV en compte {MAX_DOCUMENT_PAGES_TOTAL} au plus.",
                reason="document_too_many_pages",
                field="file",
            )

        extracted = await self._extractor.extract(documents)

        now = self._clock.now()
        existing = await self._cvs.find_by_user(user_id)
        assembly = CurriculumVitae.from_extraction(
            extracted,
            user_id=user_id,
            cv_id=existing.id if existing else uuid4(),
            image_key=existing.image_key if existing else None,
            created_at=existing.created_at if existing else now,
            now=now,
        )
        if assembly.cv.is_blank:
            raise UnsupportedUploadError(
                "Je n'ai trouvé ni nom, ni expérience, ni formation dans ce document. "
                "Vérifie que c'est bien ton CV, ou prends une photo plus nette.",
                reason="document_not_a_cv",
                field="file",
            )
        draft = await self._drafts.find_by_user(user_id)
        async with self._uow.atomic():
            await self._cvs.save(assembly.cv)
            if draft is not None:
                await self._drafts.delete(draft.id)

        cv = assembly.cv
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
        source = ImportSource(
            content_type=received[0].content_type, files=len(received), pages=pages
        )
        logger.info(
            "cv.imported",
            user_id=str(user_id),
            content_type=source.content_type,
            files=source.files,
            pages=source.pages,
            kept=assembly.kept,
            discarded=assembly.discarded,
            missing=list(cv.missing_fields),
            replaced=existing is not None,
            draft_dropped=draft is not None,
        )
        return ImportResult(
            cv=cv,
            source=source,
            kept=assembly.kept,
            discarded=assembly.discarded,
            missing=cv.missing_fields,
        )


#: Un CV de dix pages est déjà un portfolio ; les photos de pages comptent
#: comme des pages. Même borne que `MAX_DOCUMENT_PAGES`, nommée pour le total.
MAX_DOCUMENT_PAGES_TOTAL: Final = MAX_DOCUMENT_PAGES


def _total_pages(documents: Sequence[DocumentInfo]) -> int | None:
    pages = [d.pages for d in documents if d.pages is not None]
    return sum(pages) if pages else None


class UpdateCvChapter:
    """Modifie une section du CV directement — l'édition manuelle de l'application.

    Même schéma que le chapitre du brouillon : le client n'est jamais cru
    sur parole. La photo est le seul cas qui sorte du domaine : son
    identifiant (rendu par `POST /cv/photo`) se résout ici en clé d'objet,
    après vérification qu'elle appartient à la personne ; l'ancienne photo
    et les photos en attente sont oubliées **après** l'écriture, et un
    échec du bucket se journalise sans annuler la modification.
    """

    def __init__(
        self,
        *,
        cvs: CvRepository,
        photos: PhotoRepository,
        storage: ObjectStorage,
        clock: Clock,
    ) -> None:
        self._cvs = cvs
        self._photos = photos
        self._storage = storage
        self._clock = clock

    async def execute(
        self, user_id: UUID, chapter: Chapter, submission: ChapterSubmission
    ) -> CurriculumVitae:
        cv = await self._cvs.find_by_user(user_id)
        if cv is None:
            raise CvNotFoundError
        now = self._clock.now()
        if chapter is Chapter.PHOTO:
            updated = await self._with_photo(cv, submission, now=now)
        else:
            updated = cv.with_chapter(chapter, submission, now=now)
            await self._cvs.save(updated)
        logger.info("cv.chapter_updated", user_id=str(user_id), chapter=chapter.value)
        return updated

    async def _with_photo(
        self, cv: CurriculumVitae, submission: ChapterSubmission, *, now: datetime
    ) -> CurriculumVitae:
        if submission.skipped:
            image_id = None
        else:
            if submission.data is None or submission.items is not None:
                raise ChapterSubmissionError(
                    Chapter.PHOTO.value,
                    "La photo attend `data: { image_id }`.",
                    reason="chapter_data_expected",
                )
            data = validate_chapter_payload(Chapter.PHOTO, submission.data)
            image_id = data.get("image_id")
        chosen: CvPhoto | None = None
        if image_id is not None:
            chosen = await self._photos.find(UUID(str(image_id)), user_id=cv.user_id)
            if chosen is None:
                raise ChapterValidationError(
                    Chapter.PHOTO.value, [{"field": "image_id", "reason": "Photo inconnue."}]
                )
        updated = cv.with_image_key(chosen.image_key if chosen else None, now=now)
        pending = await self._photos.list_by_user(cv.user_id)
        await self._cvs.save(updated)
        await self._photos.delete([photo.id for photo in pending])
        obsolete = [p.image_key for p in pending if chosen is None or p.id != chosen.id]
        if cv.image_key and cv.image_key != updated.image_key:
            obsolete.append(cv.image_key)
        for key in obsolete:
            try:
                await self._storage.delete(Bucket.MEDIA, key)
            except StorageError as exc:
                logger.warning("cv.photo_cleanup_failed", key=key, detail=exc.details)
        return updated


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


# ---------------------------------------------------------------------
# Suggestions à la saisie
# ---------------------------------------------------------------------


class SuggestCompletions:
    """Propose des valeurs connues pour ce qu'une personne commence à taper.

    Les familles embarquées (diplômes, écoles, compétences, langues) se
    cherchent en mémoire, sans base ; les familles matérialisées
    (intitulés, entreprises, villes) dans la table que le worker remplit.
    Une saisie vide ne rend rien : on ne liste pas, on complète.
    """

    def __init__(self, index: SuggestionIndex) -> None:
        self._index = index

    async def execute(self, kind: SuggestionKind, query: str) -> list[str]:
        folded = normalize_query(query)
        if not folded:
            return []
        if kind in REFERENCE_KINDS:
            return suggest_from_reference(kind, folded, limit=SUGGESTION_LIMIT)
        return await self._index.search(kind, folded, limit=SUGGESTION_LIMIT)


@dataclass(frozen=True, slots=True)
class SuggestionRefreshReport:
    """Ce qu'une matérialisation a écrit, par famille."""

    written: dict[SuggestionKind, int]


class RefreshSuggestions:
    """Matérialise le vocabulaire de l'index d'offres dans la table des suggestions.

    Un passage par jour suffit : le vocabulaire bouge à l'échelle des
    collectes, pas des frappes. Chaque famille est remplacée d'un bloc
    (`SuggestionIndex.replace`), après nettoyage par le domaine. Une
    famille dont le vocabulaire est **vide** n'est pas remplacée : un
    index momentanément indisponible ne doit pas effacer ce qu'on servait.
    """

    def __init__(self, vocabulary: OfferVocabulary, index: SuggestionIndex) -> None:
        self._vocabulary = vocabulary
        self._index = index

    async def execute(self) -> SuggestionRefreshReport:
        written: dict[SuggestionKind, int] = {}
        for kind in sorted(MATERIALIZED_KINDS):
            # Une famille qui échoue — une ligne de l'index que la base
            # refuse, une panne pendant le remplacement — ne prive pas les
            # autres de leur matérialisation : l'échec se journalise, on
            # continue, et le rapport dit 0 pour elle.
            try:
                written[kind] = await self._refresh(kind)
            except Exception:
                logger.exception("cv.suggestions_refresh_failed", kind=kind.value)
                written[kind] = 0
        return SuggestionRefreshReport(written=written)

    async def _refresh(self, kind: SuggestionKind) -> int:
        raw = await self._vocabulary.distinct(kind)
        entries = clean_vocabulary(kind, raw)
        if not entries:
            logger.warning("cv.suggestions_empty", kind=kind.value, raw=len(raw))
            return 0
        count = await self._index.replace(kind, entries)
        logger.info("cv.suggestions_refreshed", kind=kind.value, raw=len(raw), written=count)
        return count
