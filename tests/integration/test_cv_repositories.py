"""Les dépôts du module `cv` sur une vraie base, sous le schéma des migrations."""

from __future__ import annotations

from uuid import uuid4

import pytest

from doubles import CV_NOW, complete_draft
from karria.modules.cv.domain import (
    Chapter,
    CurriculumVitae,
    CvPhoto,
    SuggestionKind,
    VocabularyEntry,
)
from karria.modules.cv.infrastructure import (
    TortoiseCvRepository,
    TortoiseDraftRepository,
    TortoisePhotoRepository,
    TortoiseSuggestionIndex,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]


class TestBrouillons:
    async def test_un_brouillon_se_sauve_se_relit_se_remplace_et_se_supprime(self) -> None:
        repository = TortoiseDraftRepository()
        draft = complete_draft()

        await repository.save(draft)
        loaded = await repository.find_by_user(draft.user_id)
        assert loaded is not None
        assert loaded.id == draft.id
        assert loaded.chapters == draft.chapters
        assert loaded.created_at.tzinfo is not None

        draft.chapters[Chapter.SUMMARY].data = {"summary": "Modifié."}
        await repository.save(draft)
        reloaded = await repository.find_by_user(draft.user_id)
        assert reloaded is not None
        assert reloaded.validated_data(Chapter.SUMMARY) == {"summary": "Modifié."}

        await repository.delete(draft.id)
        await repository.delete(draft.id)
        assert await repository.find_by_user(draft.user_id) is None


class TestCvs:
    async def test_un_cv_se_sauve_se_relit_et_se_remplace(self) -> None:
        repository = TortoiseCvRepository()
        cv = CurriculumVitae.from_draft(
            complete_draft(),
            cv_id=uuid4(),
            image_key="users/x/p.jpg",
            created_at=CV_NOW,
            now=CV_NOW,
        )

        assert await repository.find_by_user(cv.user_id) is None
        await repository.save(cv)
        loaded = await repository.find_by_user(cv.user_id)
        assert loaded is not None
        assert loaded.id == cv.id
        assert loaded.identity == cv.identity
        assert loaded.experiences == cv.experiences
        assert loaded.image_key == "users/x/p.jpg"

        replaced = CurriculumVitae(
            id=cv.id, user_id=cv.user_id, created_at=CV_NOW, updated_at=CV_NOW
        )
        await repository.save(replaced)
        reloaded = await repository.find_by_user(cv.user_id)
        assert reloaded is not None
        assert reloaded.is_blank
        assert reloaded.id == cv.id


class TestPhotos:
    async def test_les_photos_sont_isolees_par_personne(self) -> None:
        repository = TortoisePhotoRepository()
        user, other = uuid4(), uuid4()
        mine = CvPhoto(uuid4(), user, "users/a/p.jpg", False, CV_NOW)
        theirs = CvPhoto(uuid4(), other, "users/b/p.jpg", True, CV_NOW)
        await repository.add(mine)
        await repository.add(theirs)

        assert (await repository.find(mine.id, user_id=user)) is not None
        assert await repository.find(theirs.id, user_id=user) is None
        assert [p.id for p in await repository.list_by_user(user)] == [mine.id]

        await repository.delete([mine.id, theirs.id])
        await repository.delete([])
        assert await repository.list_by_user(user) == []
        assert await repository.list_by_user(other) == []


def entry(label: str, folded: str, weight: int) -> VocabularyEntry:
    return VocabularyEntry(label=label, folded=folded, weight=weight)


class TestSuggestions:
    async def test_le_vocabulaire_se_remplace_et_se_cherche_par_prefixe_ou_debut_de_mot(
        self,
    ) -> None:
        index = TortoiseSuggestionIndex()
        await index.replace(SuggestionKind.CITY, [entry("Ancienne", "ancienne", 1)])
        written = await index.replace(
            SuggestionKind.CITY,
            [
                entry("Paris", "paris", 900),
                entry("Pau", "pau", 30),
                entry("Le Parc", "le parc", 1),
                entry("Saint-Paul", "saint-paul", 2),
                entry("Lyon", "lyon", 400),
            ],
        )
        await index.replace(SuggestionKind.TITLE, [entry("Pâtissier", "patissier", 9)])

        assert written == 5
        assert await index.search(SuggestionKind.CITY, "pa", limit=8) == [
            "Paris",
            "Pau",
            "Le Parc",
        ]
        assert await index.search(SuggestionKind.CITY, "anc", limit=8) == []
        assert await index.search(SuggestionKind.CITY, "pa", limit=1) == ["Paris"]
        assert await index.search(SuggestionKind.TITLE, "pa", limit=8) == ["Pâtissier"]

    async def test_les_jokers_de_like_sont_des_caracteres_comme_les_autres(self) -> None:
        index = TortoiseSuggestionIndex()
        await index.replace(SuggestionKind.COMPANY, [entry("Carrefour", "carrefour", 5)])

        assert await index.search(SuggestionKind.COMPANY, "%", limit=8) == []
        assert await index.search(SuggestionKind.COMPANY, "c_r", limit=8) == []
