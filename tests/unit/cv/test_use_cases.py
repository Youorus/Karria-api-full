"""Les cas d'usage du module `cv`, exercés avec des doublures : aucune I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from doubles import (
    CV_IDENTITY,
    CV_NOW,
    CollectingBus,
    FakeClock,
    FakeEnhancer,
    FakeExtractor,
    FakeInspector,
    FakeInterpreter,
    FakeTranscriber,
    InMemoryCvRepository,
    InMemoryDraftRepository,
    InMemoryPhotoRepository,
    complete_draft,
    sample_image,
)
from karria.modules.cv.application import (
    DEFAULT_FOLLOW_UP,
    PHOTO_FOLLOW_UP,
    TOO_SHORT_FOLLOW_UP,
    CvFinalized,
    FinalizeDraft,
    GeneratedImage,
    GetDraft,
    GetMyCv,
    ImportCv,
    InterpretAnswer,
    Interpretation,
    OpenDraft,
    TranscribeVoice,
    UploadPhoto,
    ValidateChapter,
)
from karria.modules.cv.domain import (
    Chapter,
    ChapterStatus,
    ChapterSubmission,
    ChapterValidationError,
    CurriculumVitae,
    CvPhoto,
    Draft,
    DraftIncompleteError,
    DraftNotFoundError,
    ModelUnavailableError,
    PhotoEnhanceRefusedError,
    UnsupportedUploadError,
)
from karria.shared.config import Environment, StorageSettings
from karria.shared.database import NullUnitOfWork
from karria.shared.storage import (
    Bucket,
    InMemoryObjectStorage,
    PayloadRejectedError,
    StorageError,
)

pytestmark = pytest.mark.unit

USER = uuid4()


def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage(StorageSettings(), Environment.TEST.value)


class TestGetMyCv:
    async def test_rend_le_cv_de_la_personne(self) -> None:
        cvs = InMemoryCvRepository()
        cv = CurriculumVitae.empty(USER, now=CV_NOW)
        await cvs.save(cv)

        assert await GetMyCv(cvs, FakeClock()).execute(USER) is cv

    async def test_rend_un_cv_vide_a_qui_nen_a_pas(self) -> None:
        cv = await GetMyCv(InMemoryCvRepository(), FakeClock()).execute(USER)

        assert cv.user_id == USER
        assert cv.is_blank


class TestOpenDraft:
    async def test_cree_le_brouillon_au_premier_passage(self) -> None:
        drafts = InMemoryDraftRepository()

        draft = await OpenDraft(drafts, FakeClock()).execute(USER)

        assert drafts.drafts[USER] is draft
        assert draft.current_chapter is Chapter.IDENTITY

    async def test_rend_le_brouillon_existant_sans_le_recreer(self) -> None:
        drafts = InMemoryDraftRepository()
        use_case = OpenDraft(drafts, FakeClock())
        first = await use_case.execute(USER)

        assert (await use_case.execute(USER)).id == first.id


class TestGetDraft:
    async def test_rend_le_brouillon_ou_leve(self) -> None:
        drafts = InMemoryDraftRepository()
        with pytest.raises(DraftNotFoundError) as excinfo:
            await GetDraft(drafts).execute(USER)
        assert excinfo.value.details["reason"] == "draft_not_found"

        draft = Draft.new(USER, now=CV_NOW)
        await drafts.save(draft)
        assert await GetDraft(drafts).execute(USER) is draft


class TestInterpretAnswer:
    async def test_la_photo_ne_sinterprete_pas(self) -> None:
        interpreter = FakeInterpreter()

        result = await InterpretAnswer(interpreter).execute(Chapter.PHOTO, "une photo svp")

        assert result == Interpretation(None, (), PHOTO_FOLLOW_UP, True)
        assert interpreter.calls == []

    async def test_une_reponse_trop_courte_ne_coute_pas_un_appel(self) -> None:
        interpreter = FakeInterpreter()

        result = await InterpretAnswer(interpreter).execute(Chapter.SUMMARY, " a ")

        assert result.off_topic
        assert result.follow_up == TOO_SHORT_FOLLOW_UP
        assert interpreter.calls == []

    async def test_une_proposition_valide_est_normalisee_et_les_manquants_filtres(self) -> None:
        interpreter = FakeInterpreter(
            Interpretation(
                proposal={"position": " Vendeuse ", "company": "Zara", "start_date": "sept. 2022"},
                missing=("start_date", "invente", "start_date"),
                follow_up="  Tu y es   encore ?  ",
                off_topic=False,
            )
        )

        result = await InterpretAnswer(interpreter).execute(
            Chapter.EXPERIENCES, "j'ai  bossé\nchez Zara", {"position": "x"}
        )

        assert result.proposal is not None
        assert result.proposal["position"] == "Vendeuse"
        assert result.proposal["start_date"] == "2022-09"
        assert result.missing == ("start_date",)
        assert result.follow_up == "Tu y es encore ?"
        assert not result.off_topic
        assert interpreter.calls == [
            (Chapter.EXPERIENCES, "j'ai bossé chez Zara", {"position": "x"})
        ]

    async def test_une_relance_trop_longue_est_bornee(self) -> None:
        interpreter = FakeInterpreter(
            Interpretation(
                proposal={"summary": "Je."}, missing=(), follow_up="x" * 500, off_topic=False
            )
        )

        result = await InterpretAnswer(interpreter).execute(Chapter.SUMMARY, "je suis")

        assert result.follow_up is not None
        assert len(result.follow_up) == 300

    async def test_une_relance_blanche_vaut_none(self) -> None:
        interpreter = FakeInterpreter(
            Interpretation(
                proposal={"summary": "Je."}, missing=(), follow_up="   ", off_topic=False
            )
        )

        result = await InterpretAnswer(interpreter).execute(Chapter.SUMMARY, "je suis")

        assert result.follow_up is None

    async def test_hors_sujet_garde_la_relance_du_modele(self) -> None:
        interpreter = FakeInterpreter(Interpretation(None, (), "Et ton métier ?", True))

        result = await InterpretAnswer(interpreter).execute(Chapter.IDENTITY, "il fait beau")

        assert result == Interpretation(None, (), "Et ton métier ?", True)

    async def test_sans_proposition_ni_relance_une_relance_par_defaut_est_servie(self) -> None:
        interpreter = FakeInterpreter(Interpretation(None, (), None, False))

        result = await InterpretAnswer(interpreter).execute(Chapter.IDENTITY, "il fait beau")

        assert result == Interpretation(None, (), DEFAULT_FOLLOW_UP, True)

    async def test_une_proposition_hors_schema_est_retiree_sans_echouer(self) -> None:
        """Le modèle est une donnée : ce qui ne tient pas dans le schéma n'existe pas."""
        interpreter = FakeInterpreter(
            Interpretation(
                proposal={"email": None, "phone": None, "city": "Paris"},
                missing=("email", "bidon"),
                follow_up=None,
                off_topic=False,
            )
        )

        result = await InterpretAnswer(interpreter).execute(Chapter.CONTACT, "j'habite Paris")

        assert result.proposal is None
        assert result.missing == ("email",)
        assert result.follow_up == DEFAULT_FOLLOW_UP
        assert not result.off_topic

    async def test_une_panne_du_modele_remonte(self) -> None:
        interpreter = FakeInterpreter(ModelUnavailableError("panne"))

        with pytest.raises(ModelUnavailableError):
            await InterpretAnswer(interpreter).execute(Chapter.SUMMARY, "je suis")


class TestValidateChapter:
    async def test_sans_brouillon_cest_un_404(self) -> None:
        use_case = ValidateChapter(
            InMemoryDraftRepository(), InMemoryPhotoRepository(), FakeClock()
        )

        with pytest.raises(DraftNotFoundError):
            await use_case.execute(USER, Chapter.IDENTITY, ChapterSubmission(data=CV_IDENTITY))

    async def test_valide_puis_sauvegarde(self) -> None:
        drafts = InMemoryDraftRepository()
        await drafts.save(Draft.new(USER, now=CV_NOW))
        use_case = ValidateChapter(drafts, InMemoryPhotoRepository(), FakeClock())

        draft = await use_case.execute(USER, Chapter.IDENTITY, ChapterSubmission(data=CV_IDENTITY))

        assert draft.chapters[Chapter.IDENTITY].status is ChapterStatus.VALIDATED
        assert drafts.drafts[USER] is draft

    async def test_la_photo_doit_appartenir_a_la_personne(self) -> None:
        drafts = InMemoryDraftRepository()
        await drafts.save(Draft.new(USER, now=CV_NOW))
        photos = InMemoryPhotoRepository()
        other = CvPhoto(uuid4(), uuid4(), "users/autre/p.jpg", False, CV_NOW)
        await photos.add(other)
        use_case = ValidateChapter(drafts, photos, FakeClock())

        for image_id in (str(other.id), str(uuid4()), "pas-un-uuid"):
            with pytest.raises(ChapterValidationError) as excinfo:
                await use_case.execute(
                    USER, Chapter.PHOTO, ChapterSubmission(data={"image_id": image_id})
                )
            assert excinfo.value.details["fields"][0]["field"] == "image_id"

    async def test_une_photo_a_soi_ou_aucune_photo_passe(self) -> None:
        drafts = InMemoryDraftRepository()
        await drafts.save(Draft.new(USER, now=CV_NOW))
        photos = InMemoryPhotoRepository()
        mine = CvPhoto(uuid4(), USER, "users/moi/p.jpg", True, CV_NOW)
        await photos.add(mine)
        use_case = ValidateChapter(drafts, photos, FakeClock())

        draft = await use_case.execute(
            USER, Chapter.PHOTO, ChapterSubmission(data={"image_id": str(mine.id)})
        )
        assert draft.validated_data(Chapter.PHOTO) == {"image_id": str(mine.id)}

        draft = await use_case.execute(
            USER, Chapter.PHOTO, ChapterSubmission(data={"image_id": None})
        )
        assert draft.validated_data(Chapter.PHOTO) == {"image_id": None}

        draft = await use_case.execute(USER, Chapter.PHOTO, ChapterSubmission(data={}))
        assert draft.validated_data(Chapter.PHOTO) == {"image_id": None}

    async def test_passer_la_photo_ne_verifie_rien(self) -> None:
        drafts = InMemoryDraftRepository()
        await drafts.save(Draft.new(USER, now=CV_NOW))
        use_case = ValidateChapter(drafts, InMemoryPhotoRepository(), FakeClock())

        draft = await use_case.execute(USER, Chapter.PHOTO, ChapterSubmission(skipped=True))

        assert draft.chapters[Chapter.PHOTO].status is ChapterStatus.SKIPPED


class FailingDeleteStorage(InMemoryObjectStorage):
    """Un stockage dont la suppression tombe en panne."""

    async def delete(self, bucket: Bucket, key: str) -> None:
        raise StorageError(
            "panne", operation="delete_object", bucket=self.bucket_name(bucket), key=key
        )


class TestFinalizeDraft:
    def build(
        self,
        drafts: InMemoryDraftRepository,
        cvs: InMemoryCvRepository,
        photos: InMemoryPhotoRepository,
        store: InMemoryObjectStorage,
        events: CollectingBus,
    ) -> FinalizeDraft:
        return FinalizeDraft(
            drafts=drafts,
            cvs=cvs,
            photos=photos,
            storage=store,
            unit_of_work=NullUnitOfWork(),
            events=events,
            clock=FakeClock(datetime(2026, 10, 22, tzinfo=UTC)),
        )

    async def test_sans_brouillon_cest_un_404(self) -> None:
        use_case = self.build(
            InMemoryDraftRepository(),
            InMemoryCvRepository(),
            InMemoryPhotoRepository(),
            storage(),
            CollectingBus(),
        )
        with pytest.raises(DraftNotFoundError):
            await use_case.execute(USER)

    async def test_un_brouillon_incomplet_est_refuse_avec_la_liste(self) -> None:
        drafts = InMemoryDraftRepository()
        await drafts.save(Draft.new(USER, now=CV_NOW))
        use_case = self.build(
            drafts, InMemoryCvRepository(), InMemoryPhotoRepository(), storage(), CollectingBus()
        )

        with pytest.raises(DraftIncompleteError) as excinfo:
            await use_case.execute(USER)

        assert "identity" in excinfo.value.details["chapters"]

    async def test_le_brouillon_devient_le_cv_et_les_photos_non_retenues_sont_oubliees(
        self,
    ) -> None:
        store = storage()
        chosen_key = (
            await store.put(
                Bucket.MEDIA, "users/u/cv-photo/a.jpg", sample_image(), content_type="image/jpeg"
            )
        ).key
        other_key = (
            await store.put(
                Bucket.MEDIA, "users/u/cv-photo/b.jpg", sample_image(), content_type="image/jpeg"
            )
        ).key
        photos = InMemoryPhotoRepository()
        chosen = CvPhoto(uuid4(), USER, chosen_key, True, CV_NOW)
        await photos.add(chosen)
        await photos.add(CvPhoto(uuid4(), USER, other_key, False, CV_NOW))
        drafts = InMemoryDraftRepository()
        draft = complete_draft(USER, image_id=chosen.id)
        await drafts.save(draft)
        cvs = InMemoryCvRepository()
        events = CollectingBus()

        cv = await self.build(drafts, cvs, photos, store, events).execute(USER)

        assert cvs.cvs[USER] is cv
        assert cv.image_key == chosen_key
        assert cv.identity is not None
        assert cv.identity.first_name == "Awa"
        assert drafts.drafts == {}
        assert photos.photos == {}
        assert await store.exists(Bucket.MEDIA, chosen_key)
        assert not await store.exists(Bucket.MEDIA, other_key)
        [event] = events.of_type(CvFinalized)
        assert event.user_id == USER
        assert event.cv_id == cv.id
        assert event.first_name == "Awa"
        assert event.email == "awa@example.com"

    async def test_refaire_son_cv_garde_lidentifiant_et_remplace_lancienne_photo(self) -> None:
        store = storage()
        old_key = (
            await store.put(
                Bucket.MEDIA, "users/u/cv-photo/old.jpg", sample_image(), content_type="image/jpeg"
            )
        ).key
        cvs = InMemoryCvRepository()
        created = datetime(2025, 1, 1, tzinfo=UTC)
        existing = CurriculumVitae(
            id=uuid4(), user_id=USER, created_at=created, updated_at=created, image_key=old_key
        )
        await cvs.save(existing)
        drafts = InMemoryDraftRepository()
        await drafts.save(complete_draft(USER))

        cv = await self.build(
            drafts, cvs, InMemoryPhotoRepository(), store, CollectingBus()
        ).execute(USER)

        assert cv.id == existing.id
        assert cv.created_at == created
        assert cv.image_key is None
        assert not await store.exists(Bucket.MEDIA, old_key)

    async def test_une_panne_du_bucket_au_nettoyage_ne_perd_pas_le_cv(self) -> None:
        store = FailingDeleteStorage(StorageSettings(), Environment.TEST.value)
        photos = InMemoryPhotoRepository()
        await photos.add(CvPhoto(uuid4(), USER, "users/u/cv-photo/x.jpg", False, CV_NOW))
        drafts = InMemoryDraftRepository()
        await drafts.save(complete_draft(USER))
        cvs = InMemoryCvRepository()

        cv = await self.build(drafts, cvs, photos, store, CollectingBus()).execute(USER)

        assert cvs.cvs[USER] is cv

    async def test_une_photo_retenue_puis_disparue_bloque(self) -> None:
        drafts = InMemoryDraftRepository()
        await drafts.save(complete_draft(USER, image_id=uuid4()))
        use_case = self.build(
            drafts, InMemoryCvRepository(), InMemoryPhotoRepository(), storage(), CollectingBus()
        )

        with pytest.raises(ChapterValidationError):
            await use_case.execute(USER)


class TestTranscribeVoice:
    async def test_controle_le_fichier_puis_transcrit(self) -> None:
        transcriber = FakeTranscriber(text="  Bonjour,\n je  m'appelle Awa. ")
        use_case = TranscribeVoice(transcriber, FakeInspector(duration=12.0))

        text = await use_case.execute(b"x" * 2048, "audio/m4a; codecs=mp4a")

        assert text == "Bonjour, je m'appelle Awa."
        assert transcriber.calls == [(2048, "audio/m4a")]

    async def test_refuse_type_taille_et_duree(self) -> None:
        use_case = TranscribeVoice(FakeTranscriber(), FakeInspector(duration=301.0))

        with pytest.raises(UnsupportedUploadError):
            await use_case.execute(b"x" * 2048, "video/mp4")
        with pytest.raises(UnsupportedUploadError):
            await use_case.execute(b"x", "audio/m4a")
        with pytest.raises(UnsupportedUploadError) as excinfo:
            await use_case.execute(b"x" * 2048, "audio/m4a")
        assert excinfo.value.details["reason"] == "audio_too_long"


class TestUploadPhoto:
    async def test_range_la_photo_normalisee_et_lenregistre(self) -> None:
        store = storage()
        photos = InMemoryPhotoRepository()
        enhancer = FakeEnhancer()
        use_case = UploadPhoto(enhancer, store, photos, FakeClock())

        photo = await use_case.execute(
            USER, sample_image(300, 200, fmt="PNG"), "image/png", enhance=False
        )

        assert photos.photos[photo.id] is photo
        assert photo.user_id == USER
        assert not photo.enhanced
        assert photo.image_key.startswith(f"users/{USER}/cv-photo/")
        assert photo.image_key.endswith(".jpg")
        assert await store.exists(Bucket.MEDIA, photo.image_key)
        assert enhancer.calls == []

    async def test_lamelioration_passe_par_le_generateur_et_normalise_sa_sortie(self) -> None:
        store = storage()
        generated = GeneratedImage(sample_image(2_500, 3_000, fmt="PNG"), "image/png")
        enhancer = FakeEnhancer(result=generated)
        use_case = UploadPhoto(enhancer, store, InMemoryPhotoRepository(), FakeClock())

        photo = await use_case.execute(USER, sample_image(640, 480), "image/jpeg", enhance=True)

        assert photo.enhanced
        [(size, content_type)] = enhancer.calls
        assert content_type == "image/jpeg"
        assert size > 0
        stored = await store.get(Bucket.MEDIA, photo.image_key)
        assert len(stored) <= 5 * 1024 * 1024

    async def test_un_refus_du_generateur_remonte_tel_quel(self) -> None:
        use_case = UploadPhoto(
            FakeEnhancer(error=PhotoEnhanceRefusedError("SAFETY")),
            storage(),
            InMemoryPhotoRepository(),
            FakeClock(),
        )

        with pytest.raises(PhotoEnhanceRefusedError) as excinfo:
            await use_case.execute(USER, sample_image(), "image/jpeg", enhance=True)

        assert excinfo.value.details == {
            "reason": "photo_enhance_refused",
            "finish_reason": "SAFETY",
        }

    async def test_ce_qui_nest_pas_une_image_est_refuse(self) -> None:
        use_case = UploadPhoto(FakeEnhancer(), storage(), InMemoryPhotoRepository(), FakeClock())

        with pytest.raises(PayloadRejectedError):
            await use_case.execute(USER, b"%PDF-1.4", "image/jpeg", enhance=False)


EXTRACTED = {
    Chapter.IDENTITY: {"first_name": "Awa", "last_name": "Diallo", "headline": None},
    Chapter.EXPERIENCES: [
        {"position": "Vendeuse", "company": "Zara", "start_date": "2022-09"},
        {"position": "sans entreprise"},
    ],
    Chapter.EDUCATIONS: "pas une liste",
    Chapter.SKILLS: {"skills": [{"name": "Vente", "level": "Expert"}], "languages": []},
}


class TestImportCv:
    async def test_remplit_un_brouillon_neuf_de_propositions(self) -> None:
        drafts = InMemoryDraftRepository()
        extractor = FakeExtractor(EXTRACTED)
        use_case = ImportCv(extractor, FakeInspector(pages=2), drafts, FakeClock())

        draft = await use_case.execute(USER, b"%PDF-1.4 cv", "application/pdf")

        assert drafts.drafts[USER] is draft
        assert extractor.calls == [(b"%PDF-1.4 cv", "application/pdf")]
        assert draft.chapters[Chapter.IDENTITY].status is ChapterStatus.IN_PROGRESS
        assert draft.chapters[Chapter.IDENTITY].data == EXTRACTED[Chapter.IDENTITY]
        assert len(draft.chapters[Chapter.EXPERIENCES].items) == 1
        assert draft.chapters[Chapter.EDUCATIONS].status is ChapterStatus.PENDING
        assert draft.chapters[Chapter.SKILLS].status is ChapterStatus.IN_PROGRESS
        assert not draft.is_complete

    async def test_un_docx_part_en_texte_et_le_brouillon_existant_est_respecte(self) -> None:
        drafts = InMemoryDraftRepository()
        existing = Draft.new(USER, now=CV_NOW)
        existing.submit(
            Chapter.IDENTITY,
            ChapterSubmission(data={**CV_IDENTITY, "first_name": "Aminata"}),
            now=CV_NOW,
        )
        await drafts.save(existing)
        extractor = FakeExtractor(EXTRACTED)
        docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        use_case = ImportCv(extractor, FakeInspector(pages=None, as_text=True), drafts, FakeClock())

        draft = await use_case.execute(USER, b"texte du cv", docx)

        assert draft is existing
        assert extractor.calls == [(b"texte du cv", "text/plain")]
        assert draft.validated_data(Chapter.IDENTITY) == {**CV_IDENTITY, "first_name": "Aminata"}

    async def test_les_regles_sur_le_fichier_sappliquent_avant_lextraction(self) -> None:
        extractor = FakeExtractor(EXTRACTED)
        use_case = ImportCv(
            extractor, FakeInspector(pages=11), InMemoryDraftRepository(), FakeClock()
        )

        with pytest.raises(UnsupportedUploadError):
            await use_case.execute(USER, b"x", "application/zip")
        with pytest.raises(UnsupportedUploadError):
            await use_case.execute(USER, b"", "application/pdf")
        with pytest.raises(UnsupportedUploadError) as excinfo:
            await use_case.execute(USER, b"%PDF", "application/pdf")
        assert excinfo.value.details["reason"] == "document_too_many_pages"
        assert extractor.calls == []

    async def test_un_fichier_illisible_remonte_depuis_linspecteur(self) -> None:
        error = UnsupportedUploadError("illisible", reason="document_unreadable", field="file")
        use_case = ImportCv(
            FakeExtractor(), FakeInspector(error=error), InMemoryDraftRepository(), FakeClock()
        )

        with pytest.raises(UnsupportedUploadError) as excinfo:
            await use_case.execute(USER, b"%PDF", "application/pdf")

        assert excinfo.value is error


def test_les_identifiants_de_photo_sont_des_uuid() -> None:
    """Garde-fou de lecture : le contrat parle d'`image_id`, et c'est un UUID."""
    assert isinstance(CvPhoto(uuid4(), USER, "k", False, CV_NOW).id, UUID)
