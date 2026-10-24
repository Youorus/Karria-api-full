"""Tables du module `documents`.

- `documents_templates` : la fiche de galerie, et la version publiée.
- `documents_template_versions` : une version analysée (spécification,
  mesures, polices, clés des fichiers) — immuable une fois publiée.
- `documents_user_templates` : ce que chaque personne possède.
- `documents_selections` : le modèle choisi (un par personne).
- `documents_generated_cvs` : les exports (clés des fichiers, score ATS).

Aucune URL en base : des clés d'objets (ADR-0008). Aucune clé étrangère vers
les comptes : `user_id` seul, comme les autres modules (ADR-0015).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from tortoise import fields

from karria.shared.database import BaseDBModel
from karria.shared.storage import KEY_MAX_LENGTH


class TemplateModel(BaseDBModel):
    """Un modèle de CV (galerie)."""

    name = fields.CharField(max_length=80, unique=True, description="Nom technique (slug).")
    label = fields.CharField(max_length=120)
    category = fields.CharField(max_length=40, default="")
    description = fields.TextField(default="")
    tags: list[str] = fields.JSONField(default=list)
    is_premium = fields.BooleanField(default=False)
    price = fields.DecimalField(max_digits=8, decimal_places=2, null=True)
    primary_color = fields.CharField(max_length=9, null=True)
    font_family = fields.CharField(max_length=80, null=True)
    has_photo = fields.BooleanField(default=False)
    columns = fields.SmallIntField(default=1)
    published_version_id: UUID | None = fields.UUIDField(null=True, description="Version publiée.")
    published_version = fields.IntField(null=True)
    views_count = fields.IntField(default=0)
    uses_count = fields.IntField(default=0)
    rating_avg = fields.FloatField(default=0.0)
    rating_count = fields.IntField(default=0)

    class Meta:
        table = "documents_templates"


class TemplateVersionModel(BaseDBModel):
    """Une version analysée d'un modèle."""

    template_id: UUID = fields.UUIDField(db_index=True)
    version = fields.IntField()
    status = fields.CharField(max_length=16, db_index=True)
    source_key = fields.CharField(max_length=KEY_MAX_LENGTH)
    spec: dict[str, Any] | None = fields.JSONField(null=True)
    sample: dict[str, Any] | None = fields.JSONField(null=True)
    labels: dict[str, Any] | None = fields.JSONField(null=True)
    report: dict[str, Any] = fields.JSONField(default=dict)
    assets: dict[str, str] = fields.JSONField(default=dict)
    fonts: list[dict[str, Any]] = fields.JSONField(default=list)
    thumbnail_key = fields.CharField(max_length=KEY_MAX_LENGTH, null=True)
    thumbnail_blurhash = fields.CharField(max_length=64, null=True)
    preview_key = fields.CharField(max_length=KEY_MAX_LENGTH, null=True)
    full_key = fields.CharField(max_length=KEY_MAX_LENGTH, null=True)
    error = fields.TextField(null=True)
    published_at = fields.DatetimeField(null=True)

    class Meta:
        table = "documents_template_versions"
        unique_together = (("template_id", "version"),)


class UserTemplateModel(BaseDBModel):
    """Un modèle possédé par une personne."""

    user_id: UUID = fields.UUIDField(db_index=True)
    template_id: UUID = fields.UUIDField()
    source = fields.CharField(max_length=16, default="free")

    class Meta:
        table = "documents_user_templates"
        unique_together = (("user_id", "template_id"),)


class SelectionModel(BaseDBModel):
    """Le modèle choisi par une personne."""

    user_id: UUID = fields.UUIDField(unique=True)
    template_id: UUID = fields.UUIDField()
    ats_safe = fields.BooleanField(default=False)

    class Meta:
        table = "documents_selections"


class GeneratedCvModel(BaseDBModel):
    """Un CV exporté."""

    user_id: UUID = fields.UUIDField(db_index=True)
    template_id: UUID = fields.UUIDField()
    template_version_id: UUID = fields.UUIDField()
    template_label = fields.CharField(max_length=120)
    template_version = fields.IntField()
    ats_safe = fields.BooleanField(default=False)
    offer_id: UUID | None = fields.UUIDField(null=True)
    pdf_key = fields.CharField(max_length=KEY_MAX_LENGTH, null=True)
    docx_key = fields.CharField(max_length=KEY_MAX_LENGTH, null=True)
    fit: dict[str, Any] = fields.JSONField(default=dict)
    ats: dict[str, Any] = fields.JSONField(default=dict)
    ats_docx: dict[str, Any] | None = fields.JSONField(null=True)
    explanation: dict[str, Any] | None = fields.JSONField(null=True)
    explanation_status = fields.CharField(max_length=16, default="pending")
    full_name = fields.CharField(max_length=210, default="")

    class Meta:
        table = "documents_generated_cvs"
