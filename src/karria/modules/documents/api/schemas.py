"""Schémas HTTP du module `documents` — ceux de `karria_app/docs/contrat-api-modeles.md`.

Les URL sont **signées** à chaque lecture (1 h) à partir des clés gardées en
base (ADR-0008) ; les noms de champs de la V1 sont gardés là où l'app les lit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from karria.modules.documents.domain.templates import (
    FORMATS,
    GeneratedCv,
    Ownership,
    Selection,
    Template,
    TemplateVersion,
    ats_safe_helps,
    export_file_name,
    gallery_traits,
)
from karria.shared.storage import Bucket, ObjectStorage


class AtsReadiness(BaseModel):
    """Ce que le dessin du modèle coûte aux ATS, avant tout contenu."""

    readiness: str
    warnings: list[str]


class PageRead(BaseModel):
    """Une page du carrousel du détail."""

    kind: str
    label: str
    signed_thumbnail_url: str | None
    signed_full_url: str | None


class TemplateRead(BaseModel):
    """Un modèle de la galerie (`Modele`)."""

    id: UUID
    name: str
    label: str
    category: str | None
    description: str | None
    is_premium: bool
    price: float | None
    tags: list[str]
    signed_thumbnail_url: str | None
    signed_preview_url: str | None
    signed_full_url: str | None
    thumbnail_blurhash: str | None = None
    views_count: int
    uses_count: int
    rating_avg: float
    rating_count: int
    primary_color: str | None
    font_family: str | None
    has_photo: bool
    cover_letters: list[dict[str, Any]] = Field(default_factory=list)
    version: int | None
    columns: int
    formats: list[str]
    fields_added: list[str]
    ats_safe_available: bool
    ats: AtsReadiness
    pages: list[PageRead] | None = None

    @classmethod
    async def present(
        cls,
        template: Template,
        version: TemplateVersion | None,
        storage: ObjectStorage,
        *,
        detail: bool = False,
    ) -> TemplateRead:
        """Le modèle et sa version publiée ; les aperçus deviennent des URL signées."""

        async def sign(key: str | None) -> str | None:
            return await storage.presigned_get_url(Bucket.TEMPLATES, key) if key else None

        traits = gallery_traits(version.spec) if version and version.spec else None
        thumb = await sign(version.thumbnail_key if version else None)
        full = await sign(version.full_key if version else None)
        return cls(
            id=template.id,
            name=template.name,
            label=template.label,
            category=template.category or None,
            description=template.description or None,
            is_premium=template.is_premium,
            price=float(template.price) if template.price is not None else None,
            tags=list(template.tags),
            signed_thumbnail_url=thumb,
            signed_preview_url=await sign(version.preview_key if version else None),
            signed_full_url=full,
            thumbnail_blurhash=version.thumbnail_blurhash if version else None,
            views_count=template.views_count,
            uses_count=template.uses_count,
            rating_avg=template.rating_avg,
            rating_count=template.rating_count,
            primary_color=template.primary_color,
            font_family=template.font_family,
            has_photo=template.has_photo,
            version=template.published_version,
            columns=template.columns,
            formats=list(FORMATS),
            fields_added=traits["fields_added"] if traits else [],
            ats_safe_available=bool(version and version.spec and ats_safe_helps(version.spec)),
            ats=AtsReadiness(**traits["ats"])
            if traits
            else AtsReadiness(readiness="good", warnings=[]),
            pages=[
                PageRead(kind="cv", label="CV", signed_thumbnail_url=thumb, signed_full_url=full)
            ]
            if detail
            else None,
        )


class ReviewListRead(BaseModel):
    """Les avis d'un modèle (forme V1) — aucun avis n'est encore recueilli par la v2."""

    items: list[dict[str, Any]]
    total: int
    rating_avg: float
    rating_count: int


class TemplateRef(BaseModel):
    """Référence courte à un modèle."""

    id: UUID
    label: str | None = None
    version: int | None = None


class OwnershipRead(BaseModel):
    """Un modèle possédé (`Possession`, forme V1)."""

    id: UUID
    template: TemplateRef
    is_active: bool
    source: str
    acquired_at: datetime

    @classmethod
    def present(cls, ownership: Ownership) -> OwnershipRead:
        return cls(
            id=ownership.id,
            template=TemplateRef(id=ownership.template_id),
            is_active=True,
            source=ownership.source,
            acquired_at=ownership.acquired_at,
        )


class AcquireRequest(BaseModel):
    """`POST /user-templates/me`."""

    template_id: UUID


class SelectRequest(BaseModel):
    """`PUT /user-templates/me/selected`."""

    template_id: UUID
    ats_safe: bool = False


class SelectionRead(BaseModel):
    """Le modèle choisi."""

    template: TemplateRef
    ats_safe: bool
    updated_at: datetime

    @classmethod
    def present(cls, selection: Selection, template: Template | None) -> SelectionRead:
        return cls(
            template=TemplateRef(
                id=selection.template_id,
                label=template.label if template else None,
                version=template.published_version if template else None,
            ),
            ats_safe=selection.ats_safe,
            updated_at=selection.updated_at,
        )


class PreviewRequest(BaseModel):
    """`POST /templates/{id}/preview`."""

    ats_safe: bool = False


class FitRead(BaseModel):
    """Ce que la mise en page a dû faire pour tenir."""

    spacing: float
    font_scale: float
    pages: int


class PreviewPage(BaseModel):
    """Une page d'aperçu."""

    signed_url: str
    width: int
    height: int


class PreviewRead(BaseModel):
    """`Apercu` : le CV de la personne dans le modèle, en images."""

    pages: list[PreviewPage]
    fit: FitRead


class ExportRequest(BaseModel):
    """`POST /cv/me/generated-cvs`."""

    template_id: UUID | None = None
    formats: list[str] | None = None
    ats_safe: bool | None = None
    offer_id: UUID | None = None


class GeneratedCvRead(BaseModel):
    """`GeneratedCv` : un export, ses URL signées et son score ATS."""

    id: UUID
    template: TemplateRef
    ats_safe: bool
    offer_id: UUID | None
    created_at: datetime
    pdf_url: str | None
    docx_url: str | None
    fit: FitRead
    ats: dict[str, Any]

    @classmethod
    async def present(cls, generated: GeneratedCv, storage: ObjectStorage) -> GeneratedCvRead:
        """Re-signe les fichiers à chaque lecture ; ajoute l'explication et son état."""
        first, _, last = generated.full_name.partition(" ")

        async def sign(key: str | None, ext: str) -> str | None:
            if not key:
                return None
            return await storage.presigned_get_url(
                Bucket.DOCUMENTS, key, download_as=export_file_name(first, last, ext)
            )

        ats = dict(generated.ats)
        ats["explanation"] = generated.explanation
        ats["explanation_status"] = generated.explanation_status.value
        if generated.ats_docx is not None:
            ats["docx"] = {k: generated.ats_docx[k] for k in ("score", "level", "format", "checks")}
        return cls(
            id=generated.id,
            template=TemplateRef(
                id=generated.template_id,
                label=generated.template_label,
                version=generated.template_version,
            ),
            ats_safe=generated.ats_safe,
            offer_id=generated.offer_id,
            created_at=generated.created_at,
            pdf_url=await sign(generated.pdf_key, "pdf"),
            docx_url=await sign(generated.docx_key, "docx"),
            fit=FitRead(**generated.fit.to_json()),
            ats=ats,
        )


class GeneratedCvListRead(BaseModel):
    """Historique des exports."""

    items: list[GeneratedCvRead]
    total: int


class VersionAdminRead(BaseModel):
    """Une version de modèle vue par l'administration (revue)."""

    id: UUID
    template_id: UUID
    template_name: str
    version: int
    status: str
    error: str | None
    report: dict[str, Any]
    created_at: datetime
    published_at: datetime | None
    signed_preview_url: str | None
    signed_sample_render_url: str | None
    signed_sample_docx_url: str | None
    signed_source_url: str | None

    @classmethod
    async def present(
        cls, template: Template, version: TemplateVersion, storage: ObjectStorage
    ) -> VersionAdminRead:
        prefix = version.source_key.rsplit("/", 1)[0]

        async def sign(key: str | None) -> str | None:
            return await storage.presigned_get_url(Bucket.TEMPLATES, key) if key else None

        done = version.status.value in ("published", "review")
        return cls(
            id=version.id,
            template_id=template.id,
            template_name=template.name,
            version=version.version,
            status=version.status.value,
            error=version.error,
            report=version.report,
            created_at=version.created_at,
            published_at=version.published_at,
            signed_preview_url=await sign(version.full_key),
            signed_sample_render_url=await sign(f"{prefix}/sample_render.pdf" if done else None),
            signed_sample_docx_url=await sign(f"{prefix}/sample_docx.pdf" if done else None),
            signed_source_url=await sign(version.source_key),
        )
