"""Dépôts Tortoise du module `documents` : modèles de persistance ↔ objets du domaine."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from tortoise.expressions import F

from karria.modules.documents.domain.templates import (
    ExplanationStatus,
    Fit,
    GeneratedCv,
    Ownership,
    Selection,
    Template,
    TemplateVersion,
    VersionStatus,
)
from karria.modules.documents.infrastructure.models import (
    GeneratedCvModel,
    SelectionModel,
    TemplateModel,
    TemplateVersionModel,
    UserTemplateModel,
)


def _template(row: TemplateModel) -> Template:
    return Template(
        id=row.id,
        name=row.name,
        label=row.label,
        category=row.category,
        description=row.description,
        tags=tuple(row.tags or ()),
        is_premium=row.is_premium,
        price=row.price,
        primary_color=row.primary_color,
        font_family=row.font_family,
        has_photo=row.has_photo,
        columns=row.columns,
        published_version_id=row.published_version_id,
        published_version=row.published_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        views_count=row.views_count,
        uses_count=row.uses_count,
        rating_avg=row.rating_avg,
        rating_count=row.rating_count,
    )


def _version(row: TemplateVersionModel) -> TemplateVersion:
    return TemplateVersion(
        id=row.id,
        template_id=row.template_id,
        version=row.version,
        status=VersionStatus(row.status),
        source_key=row.source_key,
        created_at=row.created_at,
        spec=row.spec,
        sample=row.sample,
        labels=row.labels,
        report=dict(row.report or {}),
        assets=dict(row.assets or {}),
        fonts=tuple(row.fonts or ()),
        thumbnail_key=row.thumbnail_key,
        thumbnail_blurhash=row.thumbnail_blurhash,
        preview_key=row.preview_key,
        full_key=row.full_key,
        error=row.error,
        published_at=row.published_at,
    )


class TortoiseTemplateRepository:
    """Implémente `TemplateRepository`."""

    async def list_published(self, *, category: str | None, premium: bool | None) -> list[Template]:
        query = TemplateModel.filter(published_version_id__isnull=False)
        if category:
            query = query.filter(category=category)
        if premium is not None:
            query = query.filter(is_premium=premium)
        return [_template(r) for r in await query.order_by("-uses_count", "label")]

    async def list_all(self) -> list[Template]:
        return [_template(r) for r in await TemplateModel.all().order_by("name")]

    async def get(self, template_id: UUID) -> Template | None:
        row = await TemplateModel.get_or_none(id=template_id)
        return None if row is None else _template(row)

    async def get_by_name(self, name: str) -> Template | None:
        row = await TemplateModel.get_or_none(name=name)
        return None if row is None else _template(row)

    async def save(self, template: Template) -> None:
        values: dict[str, Any] = {
            "name": template.name,
            "label": template.label,
            "category": template.category,
            "description": template.description,
            "tags": list(template.tags),
            "is_premium": template.is_premium,
            "price": template.price,
            "primary_color": template.primary_color,
            "font_family": template.font_family,
            "has_photo": template.has_photo,
            "columns": template.columns,
            "published_version_id": template.published_version_id,
            "published_version": template.published_version,
        }
        await TemplateModel.update_or_create(defaults=values, id=template.id)

    async def next_version_number(self, template_id: UUID) -> int:
        last = (
            await TemplateVersionModel.filter(template_id=template_id).order_by("-version").first()
        )
        return 1 if last is None else last.version + 1

    async def save_version(self, version: TemplateVersion) -> None:
        values: dict[str, Any] = {
            "template_id": version.template_id,
            "version": version.version,
            "status": version.status.value,
            "source_key": version.source_key,
            "spec": version.spec,
            "sample": version.sample,
            "labels": version.labels,
            "report": version.report,
            "assets": version.assets,
            "fonts": list(version.fonts),
            "thumbnail_key": version.thumbnail_key,
            "thumbnail_blurhash": version.thumbnail_blurhash,
            "preview_key": version.preview_key,
            "full_key": version.full_key,
            "error": version.error,
            "published_at": version.published_at,
        }
        await TemplateVersionModel.update_or_create(defaults=values, id=version.id)

    async def get_version(self, version_id: UUID) -> TemplateVersion | None:
        row = await TemplateVersionModel.get_or_none(id=version_id)
        return None if row is None else _version(row)

    async def versions_of(self, template_id: UUID) -> list[TemplateVersion]:
        rows = await TemplateVersionModel.filter(template_id=template_id).order_by("-version")
        return [_version(r) for r in rows]

    async def increment_views(self, template_id: UUID) -> None:
        await TemplateModel.filter(id=template_id).update(views_count=F("views_count") + 1)

    async def increment_uses(self, template_id: UUID) -> None:
        await TemplateModel.filter(id=template_id).update(uses_count=F("uses_count") + 1)


class TortoiseUserTemplateRepository:
    """Implémente `UserTemplateRepository`."""

    async def owned(self, user_id: UUID) -> list[Ownership]:
        rows = await UserTemplateModel.filter(user_id=user_id).order_by("-created_at")
        return [
            Ownership(
                id=r.id,
                user_id=r.user_id,
                template_id=r.template_id,
                source=r.source,
                acquired_at=r.created_at,
            )
            for r in rows
        ]

    async def owns(self, user_id: UUID, template_id: UUID) -> bool:
        return await UserTemplateModel.filter(user_id=user_id, template_id=template_id).exists()

    async def add(self, ownership: Ownership) -> Ownership:
        row, _ = await UserTemplateModel.get_or_create(
            user_id=ownership.user_id,
            template_id=ownership.template_id,
            defaults={"id": ownership.id, "source": ownership.source},
        )
        return Ownership(
            id=row.id,
            user_id=row.user_id,
            template_id=row.template_id,
            source=row.source,
            acquired_at=row.created_at,
        )

    async def selection(self, user_id: UUID) -> Selection | None:
        row = await SelectionModel.get_or_none(user_id=user_id)
        if row is None:
            return None
        return Selection(
            user_id=row.user_id,
            template_id=row.template_id,
            ats_safe=row.ats_safe,
            updated_at=row.updated_at,
        )

    async def save_selection(self, selection: Selection) -> None:
        await SelectionModel.update_or_create(
            defaults={"template_id": selection.template_id, "ats_safe": selection.ats_safe},
            user_id=selection.user_id,
        )

    async def forget(self, user_id: UUID) -> None:
        await UserTemplateModel.filter(user_id=user_id).delete()
        await SelectionModel.filter(user_id=user_id).delete()


def _generated(row: GeneratedCvModel) -> GeneratedCv:
    fit = row.fit or {}
    return GeneratedCv(
        id=row.id,
        user_id=row.user_id,
        template_id=row.template_id,
        template_version_id=row.template_version_id,
        template_label=row.template_label,
        template_version=row.template_version,
        ats_safe=row.ats_safe,
        created_at=row.created_at,
        pdf_key=row.pdf_key,
        docx_key=row.docx_key,
        fit=Fit(
            spacing=float(fit.get("spacing", 1.0)),
            font_scale=float(fit.get("font_scale", 1.0)),
            pages=int(fit.get("pages", 1)),
        ),
        ats=dict(row.ats or {}),
        ats_docx=row.ats_docx,
        explanation=row.explanation,
        explanation_status=ExplanationStatus(row.explanation_status),
        offer_id=row.offer_id,
        full_name=row.full_name,
    )


class TortoiseGeneratedCvRepository:
    """Implémente `GeneratedCvRepository`."""

    async def add(self, generated: GeneratedCv) -> None:
        await GeneratedCvModel.create(
            id=generated.id,
            user_id=generated.user_id,
            template_id=generated.template_id,
            template_version_id=generated.template_version_id,
            template_label=generated.template_label,
            template_version=generated.template_version,
            ats_safe=generated.ats_safe,
            offer_id=generated.offer_id,
            pdf_key=generated.pdf_key,
            docx_key=generated.docx_key,
            fit=generated.fit.to_json(),
            ats=generated.ats,
            ats_docx=generated.ats_docx,
            explanation=generated.explanation,
            explanation_status=generated.explanation_status.value,
            full_name=generated.full_name,
        )

    async def get(self, generated_id: UUID) -> GeneratedCv | None:
        row = await GeneratedCvModel.get_or_none(id=generated_id)
        return None if row is None else _generated(row)

    async def history(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[GeneratedCv], int]:
        query = GeneratedCvModel.filter(user_id=user_id)
        total = await query.count()
        rows = await query.order_by("-created_at").offset(offset).limit(limit)
        return [_generated(r) for r in rows], total

    async def delete(self, generated_id: UUID) -> None:
        await GeneratedCvModel.filter(id=generated_id).delete()

    async def set_explanation(
        self, generated_id: UUID, explanation: dict[str, Any] | None, status: ExplanationStatus
    ) -> None:
        await GeneratedCvModel.filter(id=generated_id).update(
            explanation=explanation, explanation_status=status.value
        )

    async def of_user(self, user_id: UUID) -> list[GeneratedCv]:
        return [_generated(r) for r in await GeneratedCvModel.filter(user_id=user_id)]

    async def forget(self, user_id: UUID) -> None:
        await GeneratedCvModel.filter(user_id=user_id).delete()
