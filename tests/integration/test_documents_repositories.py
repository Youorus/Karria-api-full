"""Les dépôts du module `documents` sur une vraie base, sous le schéma des migrations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from karria.modules.documents.domain import (
    ExplanationStatus,
    Fit,
    GeneratedCv,
    Ownership,
    Selection,
    Template,
    TemplateVersion,
    VersionStatus,
)
from karria.modules.documents.infrastructure import (
    TortoiseGeneratedCvRepository,
    TortoiseTemplateRepository,
    TortoiseUserTemplateRepository,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_database")]

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)


def template(name: str = "meridian", *, published: bool = False, premium: bool = False) -> Template:
    return Template(
        id=uuid4(),
        name=name,
        label=name.title(),
        category="minimal",
        description="Sobre.",
        tags=("sobre",),
        is_premium=premium,
        price=Decimal("4.99") if premium else None,
        primary_color="#000000",
        font_family="Raleway",
        has_photo=True,
        columns=2,
        published_version_id=uuid4() if published else None,
        published_version=1 if published else None,
        created_at=NOW,
        updated_at=NOW,
    )


class TestModeles:
    async def test_un_modele_et_ses_versions_se_sauvent_et_se_relisent(self) -> None:
        repo = TortoiseTemplateRepository()
        draft = template("meridian")
        await repo.save(draft)
        assert await repo.next_version_number(draft.id) == 1
        version = TemplateVersion(
            id=uuid4(),
            template_id=draft.id,
            version=1,
            status=VersionStatus.PROCESSING,
            source_key="templates/x/v1/source.pdf",
            created_at=NOW,
        )
        await repo.save_version(version)
        assert await repo.next_version_number(draft.id) == 2
        done = version.with_status(
            VersionStatus.PUBLISHED,
            spec={"styles": {}},
            report={"gate": {"passed": True}},
            assets={"icon.png": "k"},
            fonts=({"family": "Raleway", "weight": 400, "italic": False},),
            thumbnail_blurhash="LEHV6nWB2yk8",
            published_at=NOW,
        )
        await repo.save_version(done)
        loaded = await repo.get_version(version.id)
        assert loaded is not None
        assert loaded.status is VersionStatus.PUBLISHED
        assert loaded.fonts == done.fonts
        assert loaded.thumbnail_blurhash == "LEHV6nWB2yk8"
        assert [v.id for v in await repo.versions_of(draft.id)] == [version.id]
        assert await repo.get_version(uuid4()) is None

    async def test_la_galerie_ne_montre_que_les_modeles_publies_et_filtre(self) -> None:
        repo = TortoiseTemplateRepository()
        hidden, free, paid = (
            template("brouillon"),
            template("libre", published=True),
            template("pro", published=True, premium=True),
        )
        for t in (hidden, free, paid):
            await repo.save(t)
        assert {t.name for t in await repo.list_published(category=None, premium=None)} == {
            "libre",
            "pro",
        }
        assert [t.name for t in await repo.list_published(category="minimal", premium=True)] == [
            "pro"
        ]
        assert await repo.list_published(category="autre", premium=None) == []
        assert len(await repo.list_all()) == 3
        loaded = await repo.get_by_name("pro")
        assert loaded is not None
        assert loaded.price == Decimal("4.99")
        assert await repo.get(uuid4()) is None
        assert await repo.get_by_name("absent") is None

    async def test_les_compteurs_s_incrementent_en_base(self) -> None:
        repo = TortoiseTemplateRepository()
        t = template()
        await repo.save(t)
        await repo.increment_views(t.id)
        await repo.increment_views(t.id)
        await repo.increment_uses(t.id)
        loaded = await repo.get(t.id)
        assert loaded is not None
        assert (loaded.views_count, loaded.uses_count) == (2, 1)


class TestPossessionEtChoix:
    async def test_possession_idempotente_choix_et_oubli(self) -> None:
        repo = TortoiseUserTemplateRepository()
        user, template_id = uuid4(), uuid4()
        first = await repo.add(
            Ownership(
                id=uuid4(), user_id=user, template_id=template_id, source="free", acquired_at=NOW
            )
        )
        again = await repo.add(
            Ownership(
                id=uuid4(), user_id=user, template_id=template_id, source="free", acquired_at=NOW
            )
        )
        assert again.id == first.id
        assert await repo.owns(user, template_id)
        assert [o.template_id for o in await repo.owned(user)] == [template_id]
        assert await repo.selection(user) is None
        await repo.save_selection(
            Selection(user_id=user, template_id=template_id, ats_safe=False, updated_at=NOW)
        )
        await repo.save_selection(
            Selection(user_id=user, template_id=template_id, ats_safe=True, updated_at=NOW)
        )
        selection = await repo.selection(user)
        assert selection is not None
        assert selection.ats_safe is True
        await repo.forget(user)
        assert await repo.owned(user) == []
        assert await repo.selection(user) is None


class TestExports:
    async def test_un_export_se_sauve_se_liste_s_explique_et_s_oublie(self) -> None:
        repo = TortoiseGeneratedCvRepository()
        user = uuid4()
        generated = GeneratedCv(
            id=uuid4(),
            user_id=user,
            template_id=uuid4(),
            template_version_id=uuid4(),
            template_label="Meridian",
            template_version=1,
            ats_safe=False,
            created_at=NOW,
            pdf_key="generated-cvs/u/x/cv.pdf",
            docx_key=None,
            fit=Fit(spacing=0.8, font_scale=1.0, pages=1),
            ats={"score": 86},
            full_name="Camille Laurent",
        )
        await repo.add(generated)
        items, total = await repo.history(user, limit=10, offset=0)
        assert total == 1
        assert items[0].fit == Fit(spacing=0.8, font_scale=1.0, pages=1)
        await repo.set_explanation(generated.id, {"summary": "Bien."}, ExplanationStatus.READY)
        loaded = await repo.get(generated.id)
        assert loaded is not None
        assert loaded.explanation_status is ExplanationStatus.READY
        assert [g.id for g in await repo.of_user(user)] == [generated.id]
        await repo.delete(generated.id)
        assert await repo.get(generated.id) is None
        await repo.add(generated)
        await repo.forget(user)
        assert await repo.of_user(user) == []
