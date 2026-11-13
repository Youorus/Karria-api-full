"""Schémas HTTP du module `documents` — ceux de `karria_app/docs/contrat-api-modeles.md`.

Les URL sont **signées** à chaque lecture (1 h) à partir des clés gardées en
base (ADR-0008) ; les noms de champs de la V1 sont gardés là où l'app les lit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from karria.modules.documents.domain.palette import parse_hex_color
from karria.modules.documents.domain.plain_letter import LETTER_STYLE_PLAIN, LETTER_STYLE_TEMPLATE
from karria.modules.documents.domain.sends import MAX_DOCUMENTS_PER_SEND, DocumentSend
from karria.modules.documents.domain.templates import (
    FORMATS,
    GeneratedCv,
    Ownership,
    Selection,
    Template,
    TemplateVersion,
    ats_safe_helps,
    gallery_traits,
)
from karria.shared.storage import Bucket, ObjectStorage


def _normalized_color(value: str | None) -> str | None:
    """`#1a2b3c`, `1a2b3c` → `#1A2B3C` ; `None` reste `None` ; sinon `422`."""
    return None if value is None else parse_hex_color(value)


class AtsReadiness(BaseModel):
    """Ce que le dessin du modèle coûte aux ATS, avant tout contenu."""

    readiness: str
    warnings: list[str]


LetterStyle = Literal["template", "plain"]


class PageRead(BaseModel):
    """Une page du carrousel du détail."""

    kind: str
    label: str
    signed_thumbnail_url: str | None
    signed_full_url: str | None
    style: LetterStyle | None = Field(
        default=None,
        description="Page `letter` : `template` (lettre assortie) ou `plain` (lettre sobre).",
    )


class CoverLetterRead(BaseModel):
    """La lettre qu'un modèle sert (`cover_letters`, forme V1) : assortie, sinon la lettre sobre (ADR-0036)."""

    id: str = Field(description="`template` ou `plain`.")
    label: str
    style: LetterStyle
    signed_thumbnail_url: str | None
    signed_preview_url: str | None
    signed_full_url: str | None
    thumbnail_blurhash: str | None = None


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
    has_letter: bool = Field(
        default=False, description="Le modèle a une lettre assortie, mesurée et publiée avec lui."
    )
    cover_letters: list[CoverLetterRead] = Field(
        default_factory=list,
        description=(
            "La lettre que ce modèle sert : la lettre assortie (`template`) s'il en a une, "
            "sinon la lettre sobre (`plain`) — exactement ce que l'atelier rend."
        ),
    )
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
        plain_letter: dict[str, Any] | None = None,
    ) -> TemplateRead:
        """Le modèle et sa version publiée ; les aperçus deviennent des URL signées.

        `plain_letter` : les clés des aperçus de la lettre sobre (`plain_letter_pages`),
        servie quand le modèle n'a pas de lettre assortie ; `None` tant que la
        commande `plain-letter` n'a pas tourné (aucune page de lettre alors).
        """

        async def sign(key: str | None) -> str | None:
            return await storage.presigned_get_url(Bucket.TEMPLATES, key) if key else None

        traits = gallery_traits(version.spec) if version and version.spec else None
        thumb = await sign(version.thumbnail_key if version else None)
        full = await sign(version.full_key if version else None)
        letter: CoverLetterRead | None = None
        if version is not None and version.has_letter:
            letter = CoverLetterRead(
                id=LETTER_STYLE_TEMPLATE,
                label="Lettre assortie",
                style=LETTER_STYLE_TEMPLATE,
                signed_thumbnail_url=await sign(version.letter_thumbnail_key),
                signed_preview_url=await sign(version.letter_preview_key),
                signed_full_url=await sign(version.letter_full_key),
                thumbnail_blurhash=version.letter_thumbnail_blurhash,
            )
        elif version is not None and plain_letter is not None:
            letter = CoverLetterRead(
                id=LETTER_STYLE_PLAIN,
                label="Lettre sobre",
                style=LETTER_STYLE_PLAIN,
                signed_thumbnail_url=await sign(plain_letter["thumbnail_key"]),
                signed_preview_url=await sign(plain_letter["preview_key"]),
                signed_full_url=await sign(plain_letter["full_key"]),
                thumbnail_blurhash=plain_letter.get("blurhash"),
            )
        pages = [PageRead(kind="cv", label="CV", signed_thumbnail_url=thumb, signed_full_url=full)]
        if letter is not None:
            pages.append(
                PageRead(
                    kind="letter",
                    label=letter.label,
                    signed_thumbnail_url=letter.signed_thumbnail_url,
                    signed_full_url=letter.signed_full_url,
                    style=letter.style,
                )
            )
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
            has_letter=bool(version and version.has_letter),
            cover_letters=[letter] if letter is not None else [],
            version=template.published_version,
            columns=template.columns,
            formats=list(FORMATS),
            fields_added=traits["fields_added"] if traits else [],
            ats_safe_available=bool(version and version.spec and ats_safe_helps(version.spec)),
            ats=AtsReadiness(**traits["ats"])
            if traits
            else AtsReadiness(readiness="good", warnings=[]),
            pages=pages if detail else None,
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
    primary_color: str | None = Field(
        default=None,
        description="La couleur principale choisie (`#rrggbb`) ; `null` : celle du modèle (ADR-0034).",
    )

    _color = field_validator("primary_color")(_normalized_color)


class SelectionRead(BaseModel):
    """Le modèle choisi."""

    template: TemplateRef
    ats_safe: bool
    updated_at: datetime
    primary_color: str | None = Field(
        default=None, description="La couleur posée par la personne, ou `null`."
    )
    default_primary_color: str | None = Field(
        default=None,
        description="La couleur d'origine du modèle choisi : celle de son décor, mesurée (ADR-0034 § 2026-11-06).",
    )

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
            primary_color=selection.primary_color,
            default_primary_color=template.primary_color if template else None,
        )


class PreviewRequest(BaseModel):
    """`POST /templates/{id}/preview`."""

    ats_safe: bool = False


class FitRead(BaseModel):
    """Ce que la mise en page a dû faire pour tenir."""

    spacing: float
    font_scale: float
    pages: int
    condensed: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Ce que l'export a replié pour tenir sur une page, par nature "
            "(`missions`, `skills`, `languages`, `interests`, `projects`, "
            "`projects_detail`, `educations_detail`, `summary`) ; vide sinon. "
            "Tout reste dans le CV de la personne."
        ),
    )


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
    dossier_id: UUID | None = Field(
        default=None, description="Dossier de candidature (UUID libre) auquel rattacher l'export."
    )
    cv: CvBody | None = Field(
        default=None,
        description=(
            "Un CV à exporter **à la place** du CV de base (la copie adaptée d'un dossier, "
            "même forme et mêmes bornes que le rendu ancré). Absent : le CV de la personne."
        ),
    )
    primary_color: str | None = Field(
        default=None,
        description=(
            "La couleur principale choisie (`#rrggbb`), appliquée après la variante ATS ; "
            "`null` : celle du modèle (ou du choix, sans `template_id`). ADR-0034."
        ),
    )

    _color = field_validator("primary_color")(_normalized_color)


# ---------------------------------------------------------------------- atelier (ADR-0029)


class Loose(BaseModel):
    """Un sous-objet du CV reçu : les champs utiles, le reste toléré (le brouillon de l'app évolue)."""

    model_config = ConfigDict(extra="allow")


class CvIdentityBody(Loose):
    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    headline: str | None = Field(default=None, max_length=200)


class CvLinkBody(Loose):
    url: str = Field(default="", max_length=500)


class CvContactBody(Loose):
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=120)
    links: list[CvLinkBody] = Field(default_factory=list, max_length=10)


class CvExperienceBody(Loose):
    position: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    location: str | None = Field(default=None, max_length=120)
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    is_current: bool = False
    missions: list[str] = Field(default_factory=list, max_length=20)
    description: str | None = Field(default=None, max_length=3000)


class CvEducationBody(Loose):
    degree: str = Field(default="", max_length=200)
    school: str = Field(default="", max_length=200)
    location: str | None = Field(default=None, max_length=120)
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    is_in_progress: bool = False
    description: str | None = Field(default=None, max_length=3000)


class CvSkillBody(Loose):
    name: str = Field(default="", max_length=100)
    category: str | None = Field(default=None, max_length=60)


class CvLanguageBody(Loose):
    language: str = Field(default="", max_length=100)
    level: str | None = Field(default=None, max_length=40)


class CvInterestBody(Loose):
    name: str = Field(default="", max_length=100)


class CvProjectBody(Loose):
    title: str = Field(default="", max_length=200)
    description: str | None = Field(default=None, max_length=3000)
    technologies: list[str] = Field(default_factory=list, max_length=20)


class CvBody(Loose):
    """Le CV au format du module `cv` (`GET /cv/me`, le brouillon de l'app), borné."""

    identity: CvIdentityBody | None = None
    contact: CvContactBody | None = None
    summary: str | None = Field(default=None, max_length=3000)
    experiences: list[CvExperienceBody] = Field(default_factory=list, max_length=30)
    educations: list[CvEducationBody] = Field(default_factory=list, max_length=20)
    skills: list[CvSkillBody] = Field(default_factory=list, max_length=60)
    languages: list[CvLanguageBody] = Field(default_factory=list, max_length=15)
    interests: list[CvInterestBody] = Field(default_factory=list, max_length=20)
    projects: list[CvProjectBody] = Field(default_factory=list, max_length=20)


class LetterRecipientBody(BaseModel):
    company: str = Field(default="", max_length=200)
    logo_url: str | None = Field(
        default=None,
        max_length=500,
        description="Adresse publique du logo (celle du fil d'offres) ; ailleurs, ignorée.",
    )
    city: str | None = Field(default=None, max_length=120)


class LetterParagraphBody(BaseModel):
    kind: Literal["interet_entreprise", "interet_poste", "competences", "pourquoi_moi", "libre"] = (
        "libre"
    )
    text: str = Field(default="", max_length=2500)


class LetterSenderBody(BaseModel):
    """L'identité du CV, telle que l'app la tient."""

    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    headline: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=120)


class LetterBody(BaseModel):
    """Une lettre de motivation (ADR-0030) : structurée, jamais un texte libre."""

    recipient: LetterRecipientBody = Field(default_factory=LetterRecipientBody)
    subject: str = Field(default="", max_length=200)
    greeting: str | None = Field(default=None, max_length=120)
    paragraphs: list[LetterParagraphBody] = Field(default_factory=list, max_length=8)
    closing: str | None = Field(default=None, max_length=300)
    sender: LetterSenderBody = Field(default_factory=LetterSenderBody)


class RenderRequest(BaseModel):
    """`POST /documents/{template_id}/render` : un CV **ou** une lettre."""

    cv: CvBody | None = None
    letter: LetterBody | None = None
    ats_safe: bool = False
    primary_color: str | None = Field(
        default=None,
        description="La couleur principale choisie (`#rrggbb`) ; `null` : celle du modèle (ADR-0034).",
    )
    letter_style: LetterStyle | None = Field(
        default=None,
        description=(
            "Lettre : `template` (la lettre assortie du modèle) ou `plain` (la lettre sobre) ; "
            "`null` : la lettre assortie s'il y en a une, sinon la sobre (ADR-0036)."
        ),
    )

    _color = field_validator("primary_color")(_normalized_color)


class RenderRead(BaseModel):
    """Le HTML ancré de l'atelier."""

    html: str = Field(
        description="Le HTML que Gotenberg imprimera ; `data-chemin` sur chaque texte du CV."
    )
    pages: int = Field(
        description="Pages estimées par mesure ; la vérité est dans `window.karriaFit`."
    )
    warnings: list[str] = Field(
        description=(
            "`headline_missing`, `photo_missing`, `more_than_one_page`, `logo_unavailable`, "
            "`too_few_paragraphs`, `too_many_paragraphs`, `body_too_long`, "
            "`subject_not_explicit`, `closing_missing`, `company_missing`."
        )
    )
    letter_style_served: LetterStyle | None = Field(
        default=None,
        description="Lettre : le style réellement servi (`template` ou `plain`) ; `null` pour un CV.",
    )


class LetterExportRequest(BaseModel):
    """`POST /documents/letters/export`."""

    template_id: UUID | None = Field(default=None, description="À défaut, le modèle choisi.")
    letter: LetterBody
    formats: list[str] | None = None
    dossier_id: UUID | None = None
    primary_color: str | None = Field(
        default=None,
        description="La couleur principale du dossier, pour que la lettre suive le CV.",
    )
    letter_style: LetterStyle | None = Field(
        default=None,
        description="`template` ou `plain` ; `null` : la lettre assortie s'il y en a une, sinon la sobre.",
    )

    _color = field_validator("primary_color")(_normalized_color)


# ---------------------------------------------------------------------- envoi (ADR-0031)


class SendRequest(BaseModel):
    """`POST /documents/exports/{id}/send`."""

    to: str | None = Field(
        default=None, max_length=254, description="`null` : l'adresse du compte."
    )


class BatchSendRequest(BaseModel):
    """`POST /documents/exports/send` : plusieurs documents dans un seul e-mail."""

    generated_ids: list[UUID] = Field(min_length=1, max_length=MAX_DOCUMENTS_PER_SEND)
    to: str | None = Field(default=None, max_length=254)


class SendRead(BaseModel):
    """Un envoi et son état."""

    id: UUID
    status: str = Field(description="`queued`, `sent` ou `failed`.")
    to: str
    generated_ids: list[UUID]
    created_at: datetime
    sent_at: datetime | None

    @classmethod
    def present(cls, send: DocumentSend) -> SendRead:
        return cls(
            id=send.id,
            status=send.status.value,
            to=send.to,
            generated_ids=list(send.generated_ids),
            created_at=send.created_at,
            sent_at=send.sent_at,
        )


class GeneratedCvRead(BaseModel):
    """`GeneratedCv` : un export (CV ou lettre), ses URL signées et son score ATS."""

    id: UUID
    kind: str = Field(description="`cv` ou `letter` (ADR-0030).")
    dossier_id: UUID | None = None
    company_name: str | None = Field(
        default=None, description="Lettre : l'entreprise destinataire."
    )
    template: TemplateRef
    ats_safe: bool
    primary_color: str | None = Field(
        default=None, description="La couleur principale du rendu, si la personne en a posé une."
    )
    letter_style: LetterStyle | None = Field(
        default=None,
        description="Lettre : le style servi (`template` ou `plain`) ; `null` pour un CV.",
    )
    offer_id: UUID | None
    created_at: datetime
    pdf_url: str | None
    docx_url: str | None
    preview_urls: list[str] = Field(
        default_factory=list,
        description="Les pages du document en image (PNG), signées, dans l'ordre.",
    )
    fit: FitRead
    ats: dict[str, Any]

    @classmethod
    async def present(cls, generated: GeneratedCv, storage: ObjectStorage) -> GeneratedCvRead:
        """Re-signe les fichiers à chaque lecture ; ajoute l'explication et son état."""

        async def sign(key: str | None, ext: str) -> str | None:
            if not key:
                return None
            return await storage.presigned_get_url(
                Bucket.DOCUMENTS, key, download_as=generated.file_name(ext)
            )

        ats = dict(generated.ats)
        ats["explanation"] = generated.explanation
        ats["explanation_status"] = generated.explanation_status.value
        if generated.ats_docx is not None:
            ats["docx"] = {k: generated.ats_docx[k] for k in ("score", "level", "format", "checks")}
        return cls(
            id=generated.id,
            kind=generated.kind.value,
            dossier_id=generated.dossier_id,
            company_name=generated.company_name or None,
            template=TemplateRef(
                id=generated.template_id,
                label=generated.template_label,
                version=generated.template_version,
            ),
            ats_safe=generated.ats_safe,
            primary_color=generated.primary_color,
            letter_style=generated.letter_style,  # type: ignore[arg-type]
            offer_id=generated.offer_id,
            created_at=generated.created_at,
            pdf_url=await sign(generated.pdf_key, "pdf"),
            docx_url=await sign(generated.docx_key, "docx"),
            preview_urls=[
                await storage.presigned_get_url(Bucket.DOCUMENTS, key)
                for key in generated.preview_keys
            ],
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
    has_letter: bool = False
    signed_letter_preview_url: str | None = None
    signed_letter_sample_render_url: str | None = None
    signed_letter_source_url: str | None = None

    @classmethod
    async def present(
        cls, template: Template, version: TemplateVersion, storage: ObjectStorage
    ) -> VersionAdminRead:
        prefix = version.source_key.rsplit("/", 1)[0]

        async def sign(key: str | None) -> str | None:
            return await storage.presigned_get_url(Bucket.TEMPLATES, key) if key else None

        done = version.status.value in ("published", "review")
        with_letter = done and version.letter_source_key is not None
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
            has_letter=version.has_letter,
            signed_letter_preview_url=await sign(version.letter_full_key),
            signed_letter_sample_render_url=await sign(
                f"{prefix}/letter/sample_render.pdf" if with_letter else None
            ),
            signed_letter_source_url=await sign(version.letter_source_key),
        )


# `ExportRequest` nomme `CvBody`, défini plus bas : la référence se résout ici.
ExportRequest.model_rebuild()
