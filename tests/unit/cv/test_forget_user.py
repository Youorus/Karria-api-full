"""Oublier une personne : les objets d'abord, puis seulement les lignes dont l'objet est parti."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from doubles import (
    CV_NOW,
    InMemoryCvRepository,
    InMemoryDraftRepository,
    InMemoryPhotoRepository,
    complete_draft,
    sample_image,
)
from karria.modules.cv.api.dependencies import build_forget_user
from karria.modules.cv.application import ForgetOutcome, ForgetUser
from karria.modules.cv.domain import CurriculumVitae, CvPhoto, Draft
from karria.modules.cv.infrastructure import TortoiseCvRepository
from karria.shared.database import NullUnitOfWork
from karria.shared.storage import Bucket, InMemoryObjectStorage, StorageError

pytestmark = pytest.mark.unit

USER = uuid4()


class SelectivelyFailingStorage(InMemoryObjectStorage):
    """Un stockage dont la suppression échoue pour certaines clés."""

    def __init__(self, inner: InMemoryObjectStorage, failing: set[str]) -> None:
        self.__dict__.update(inner.__dict__)
        self.failing = failing

    async def delete(self, bucket: Bucket, key: str) -> None:
        if key in self.failing:
            raise StorageError(
                "panne", operation="delete_object", bucket=self.bucket_name(bucket), key=key
            )
        await super().delete(bucket, key)


@pytest.fixture
def drafts() -> InMemoryDraftRepository:
    return InMemoryDraftRepository()


@pytest.fixture
def cvs() -> InMemoryCvRepository:
    return InMemoryCvRepository()


@pytest.fixture
def photos() -> InMemoryPhotoRepository:
    return InMemoryPhotoRepository()


def _forget(
    drafts: InMemoryDraftRepository,
    cvs: InMemoryCvRepository,
    photos: InMemoryPhotoRepository,
    storage: InMemoryObjectStorage,
) -> ForgetUser:
    return ForgetUser(
        drafts=drafts, cvs=cvs, photos=photos, storage=storage, unit_of_work=NullUnitOfWork()
    )


async def _photo(
    photos: InMemoryPhotoRepository, storage: InMemoryObjectStorage, key: str, user: UUID = USER
) -> CvPhoto:
    await storage.put(Bucket.MEDIA, key, sample_image(8, 8), content_type="image/jpeg")
    photo = CvPhoto(
        id=uuid4(),
        user_id=user,
        image_key=key,
        enhanced=False,
        created_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    await photos.add(photo)
    return photo


async def _cv(
    cvs: InMemoryCvRepository, storage: InMemoryObjectStorage, key: str | None
) -> CurriculumVitae:
    if key is not None:
        await storage.put(Bucket.MEDIA, key, sample_image(8, 8), content_type="image/jpeg")
    cv = CurriculumVitae.from_draft(
        complete_draft(USER), cv_id=uuid4(), image_key=key, created_at=CV_NOW, now=CV_NOW
    )
    await cvs.save(cv)
    return cv


class TestForgetUser:
    async def test_efface_brouillon_cv_photos_et_objets(
        self,
        drafts: InMemoryDraftRepository,
        cvs: InMemoryCvRepository,
        photos: InMemoryPhotoRepository,
        storage: InMemoryObjectStorage,
    ) -> None:
        await drafts.save(Draft.new(USER, now=CV_NOW))
        await _cv(cvs, storage, "users/x/cv-photo/retenue.jpg")
        await _photo(photos, storage, "users/x/cv-photo/attente.jpg")
        # Ce qui appartient à quelqu'un d'autre reste.
        autre = await _photo(photos, storage, "users/y/cv-photo/sienne.jpg", user=uuid4())

        outcome = await _forget(drafts, cvs, photos, storage).execute(USER)

        assert outcome == ForgetOutcome(
            draft_deleted=True, cv_deleted=True, photos_deleted=1, objects_deleted=2
        )
        assert outcome.complete
        assert await drafts.find_by_user(USER) is None
        assert await cvs.find_by_user(USER) is None
        assert await photos.list_by_user(USER) == []
        assert list(storage.objects) == [(Bucket.MEDIA, autre.image_key)]

    async def test_est_idempotent_sur_une_personne_inconnue(
        self,
        drafts: InMemoryDraftRepository,
        cvs: InMemoryCvRepository,
        photos: InMemoryPhotoRepository,
        storage: InMemoryObjectStorage,
    ) -> None:
        outcome = await _forget(drafts, cvs, photos, storage).execute(uuid4())

        assert outcome == ForgetOutcome(
            draft_deleted=False, cv_deleted=False, photos_deleted=0, objects_deleted=0
        )

    async def test_une_cle_qui_resiste_garde_sa_ligne_pour_le_rejeu(
        self,
        drafts: InMemoryDraftRepository,
        cvs: InMemoryCvRepository,
        photos: InMemoryPhotoRepository,
        storage: InMemoryObjectStorage,
    ) -> None:
        await drafts.save(Draft.new(USER, now=CV_NOW))
        cv = await _cv(cvs, storage, "users/x/cv-photo/retenue.jpg")
        tenace = await _photo(photos, storage, "users/x/cv-photo/tenace.jpg")
        docile = await _photo(photos, storage, "users/x/cv-photo/docile.jpg")
        assert cv.image_key is not None
        failing = SelectivelyFailingStorage(storage, {tenace.image_key, cv.image_key})

        outcome = await _forget(drafts, cvs, photos, failing).execute(USER)

        assert not outcome.complete
        assert outcome.objects_failed == (cv.image_key, tenace.image_key)
        assert outcome.objects_deleted == 1
        assert outcome.draft_deleted is True
        assert outcome.cv_deleted is False
        assert outcome.photos_deleted == 1
        # Les lignes dont l'objet a résisté restent : un rejeu les retrouve.
        assert await cvs.find_by_user(USER) is not None
        assert [p.id for p in await photos.list_by_user(USER)] == [tenace.id]
        assert (Bucket.MEDIA, docile.image_key) not in failing.objects

        # Le rejeu, une fois le stockage revenu, termine le travail.
        again = await _forget(drafts, cvs, photos, storage).execute(USER)
        assert again.complete
        assert again.cv_deleted is True
        assert await cvs.find_by_user(USER) is None
        assert await photos.list_by_user(USER) == []

    async def test_un_cv_sans_photo_part_meme_si_le_stockage_tombe(
        self,
        drafts: InMemoryDraftRepository,
        cvs: InMemoryCvRepository,
        photos: InMemoryPhotoRepository,
        storage: InMemoryObjectStorage,
    ) -> None:
        await _cv(cvs, storage, None)
        tenace = await _photo(photos, storage, "users/x/cv-photo/tenace.jpg")
        failing = SelectivelyFailingStorage(storage, {tenace.image_key})

        outcome = await _forget(drafts, cvs, photos, failing).execute(USER)

        assert outcome.cv_deleted is True
        assert outcome.objects_failed == (tenace.image_key,)
        assert await cvs.find_by_user(USER) is None


def test_loubli_hors_requete_recoit_les_adaptateurs_deployes(
    storage: InMemoryObjectStorage,
) -> None:
    forget = build_forget_user(storage)
    assert isinstance(forget, ForgetUser)
    assert isinstance(forget._cvs, TortoiseCvRepository)
    assert forget._storage is storage
