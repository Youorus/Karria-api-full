"""Doublures en mémoire des ports du module `documents`, et petits constructeurs.

Chaque doublure implémente un port de `application/ports.py` structurellement
et garde la trace de ce qu'on lui a demandé : les tests vérifient un cas
d'usage par ce qu'il a fait faire à ses ports. Le moteur factice rend des
octets, des mesures et des aperçus programmés — aucun Gotenberg, aucun réseau.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from karria.modules.documents.application import Analysis, CvSnapshot, Previews, RenderedPdf
from karria.modules.documents.domain.ats import ExtractedDocument
from karria.modules.documents.domain.gate import Fidelity
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

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
PDF_BYTES = b"%PDF-1.7 rendu"
DOCX_BYTES = b"PK docx"
PNG_BYTES = b"\x89PNG page"

#: Le CV d'une personne, forme de `GET /cv/me`.
CV_DATA: dict[str, Any] = {
    "identity": {"first_name": "Awa", "last_name": "Diallo", "headline": "Vendeuse"},
    "contact": {
        "email": "awa@example.com",
        "phone": "06 12 34 56 78",
        "city": "Montreuil",
        "links": [{"url": "https://www.linkedin.com/in/awa/"}],
    },
    "summary": "Conseillère de vente souriante et organisée.",
    "experiences": [
        {
            "position": "Vendeuse",
            "company": "Zara",
            "start_date": "2021-01",
            "end_date": "2023-06",
            "missions": ["Conseiller 30 clients par jour", "Tenir la caisse"],
        }
    ],
    "educations": [{"degree": "Bac pro commerce", "school": "Lycée Jean Moulin"}],
    "skills": [{"name": "Vente", "category": "Métier"}],
    "languages": [{"language": "Anglais", "level": "C1"}],
    "interests": [{"name": "Danse"}],
    "projects": [],
}

#: Le texte qu'un ATS lit d'un rendu propre.
CLEAN_TEXT = (
    "Awa Diallo Vendeuse 06 12 34 56 78 awa@example.com Montreuil "
    "Profil Conseillère de vente souriante et organisée. "
    "Expériences professionnelles Vendeuse - Zara 2021 - 2023 "
    "Conseiller 30 clients par jour Tenir la caisse "
    "Formation Bac pro commerce Lycée Jean Moulin Compétences Vente Langues Anglais (C1)"
)
#: Le même, avec un intitulé lu lettre à lettre (interlettrage trop large).
SPACED_TEXT = CLEAN_TEXT.replace("Formation", "F O R M A T I O N")


def style(
    family: str = "Inter", size: float = 10.0, *, tracking: float = 0.0, color: str = "#111111"
) -> dict[str, Any]:
    """Un style de la spécification."""
    return {
        "family": family,
        "weight": 400,
        "size_pt": size,
        "letter_spacing_pt": tracking,
        "color": color,
    }


def make_spec(
    *,
    photo: bool = False,
    spaced: bool = False,
    columns: int = 1,
    bands: bool = True,
    synthesized_headline: bool = False,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Une spécification minimale, de la forme que produit l'analyse."""
    sections = [
        {
            "kind": kind,
            "title_sample": title,
            "title_sep": " - ",
            "slots": [{"roles": ["item_title", "item_dates"]}],
        }
        for kind, title in (
            ("contact", "Contact"),
            ("summary", "Profil"),
            ("experiences", "Expérience"),
            ("educations", "Formation"),
            ("skills", "Compétences"),
        )
    ]
    cols = [{"sections": sections}] + [{"sections": []} for _ in range(columns - 1)]
    return {
        "styles": {
            "name": style("Playfair", 24.0),
            "section_title": style(tracking=2.0 if spaced else 0.0, color="#1A5276"),
            "body": style(),
        },
        "bands": [{"columns": cols}] if bands else [],
        "header": {
            "photo": {"sample_file": "sample_photo.png"} if photo else None,
            "elements": [{"role": "headline", "synthesized": synthesized_headline}],
        },
        "meta": meta
        if meta is not None
        else {"category": "classique", "description_fr": "Sobre.", "tags": ["sobre"]},
    }


def make_sample() -> dict[str, Any]:
    """Le contenu d'exemple d'un modèle, tel que l'analyse le relève."""
    return {
        "identity": {"first_name": "Jean", "last_name": "Dupont", "headline": "Comptable"},
        "contact": {"email": "jean@example.com", "phone": "0102030405", "city": "Paris"},
        "summary": "Comptable rigoureux.",
        "experiences": [
            {
                "title_raw": "Comptable - Acme",
                "subtitle_raw": "",
                "dates_raw": "2020 - 2024",
                "location": "Paris",
                "bullets": ["Clôtures mensuelles"],
                "description": None,
            }
        ],
        "skills": [{"name": "Excel", "category": None}],
    }


def make_template(
    *,
    name: str = "meridian",
    premium: bool = False,
    published_version_id: UUID | None = None,
    published_version: int | None = None,
    category: str = "",
    description: str = "",
    tags: tuple[str, ...] = (),
) -> Template:
    """Une fiche de modèle."""
    return Template(
        id=uuid4(),
        name=name,
        label=name.title(),
        category=category,
        description=description,
        tags=tags,
        is_premium=premium,
        price=None,
        primary_color=None,
        font_family=None,
        has_photo=False,
        columns=1,
        published_version_id=published_version_id,
        published_version=published_version,
        created_at=NOW,
        updated_at=NOW,
    )


def make_version(
    template_id: UUID,
    *,
    number: int = 1,
    status: VersionStatus = VersionStatus.PUBLISHED,
    spec: dict[str, Any] | None = None,
    assets: dict[str, str] | None = None,
) -> TemplateVersion:
    """Une version de modèle."""
    prefix = f"templates/{template_id}/v{number}"
    return TemplateVersion(
        id=uuid4(),
        template_id=template_id,
        version=number,
        status=status,
        source_key=f"{prefix}/source.pdf",
        created_at=NOW,
        spec=spec,
        sample=make_sample() if spec is not None else None,
        assets=assets or {},
        thumbnail_key=f"{prefix}/preview/vignette.webp" if spec is not None else None,
        preview_key=f"{prefix}/preview/apercu.png" if spec is not None else None,
        full_key=f"{prefix}/preview/apercu_pleine_page.png" if spec is not None else None,
    )


def good_fidelity() -> Fidelity:
    """Des mesures qui passent la porte de qualité."""
    return Fidelity(
        ssim=0.97,
        ssim_tolerant=0.98,
        words_within_2mm=0.97,
        words_matched=0.99,
        median_shift_mm=0.3,
        font_match=1.0,
        size_match=1.0,
        color_match=1.0,
        decoration=0.95,
        pages_source=1,
        pages_render=1,
    )


def bad_fidelity() -> Fidelity:
    """Des mesures sous le seuil."""
    return replace(good_fidelity(), ssim_tolerant=0.5, words_within_2mm=0.4)


# ---------------------------------------------------------------------- dépôts


class InMemoryTemplateRepository:
    """Implémente `TemplateRepository` dans des dictionnaires."""

    def __init__(self) -> None:
        self.templates: dict[UUID, Template] = {}
        self.versions: dict[UUID, TemplateVersion] = {}
        #: `False` : `list_published` rend aussi les modèles non publiés (cas limite).
        self.hide_unpublished = True

    async def list_published(self, *, category: str | None, premium: bool | None) -> list[Template]:
        return [
            t
            for t in self.templates.values()
            if (t.is_published or not self.hide_unpublished)
            and (category is None or t.category == category)
            and (premium is None or t.is_premium == premium)
        ]

    async def list_all(self) -> list[Template]:
        return list(self.templates.values())

    async def get(self, template_id: UUID) -> Template | None:
        return self.templates.get(template_id)

    async def get_by_name(self, name: str) -> Template | None:
        return next((t for t in self.templates.values() if t.name == name), None)

    async def save(self, template: Template) -> None:
        self.templates[template.id] = template

    async def next_version_number(self, template_id: UUID) -> int:
        return 1 + max(
            (v.version for v in self.versions.values() if v.template_id == template_id),
            default=0,
        )

    async def save_version(self, version: TemplateVersion) -> None:
        self.versions[version.id] = version

    async def get_version(self, version_id: UUID) -> TemplateVersion | None:
        return self.versions.get(version_id)

    async def versions_of(self, template_id: UUID) -> list[TemplateVersion]:
        return sorted(
            (v for v in self.versions.values() if v.template_id == template_id),
            key=lambda v: v.version,
        )

    async def increment_views(self, template_id: UUID) -> None:
        t = self.templates[template_id]
        self.templates[template_id] = replace(t, views_count=t.views_count + 1)

    async def increment_uses(self, template_id: UUID) -> None:
        t = self.templates[template_id]
        self.templates[template_id] = replace(t, uses_count=t.uses_count + 1)

    def publish(
        self,
        *,
        name: str = "meridian",
        premium: bool = False,
        spec: dict[str, Any] | None = None,
        assets: dict[str, str] | None = None,
    ) -> tuple[Template, TemplateVersion]:
        """Range un modèle publié et sa version publiée."""
        template = make_template(name=name, premium=premium)
        version = make_version(template.id, spec=spec or make_spec(), assets=assets)
        template = replace(template, published_version_id=version.id, published_version=1)
        self.templates[template.id] = template
        self.versions[version.id] = version
        return template, version


class InMemoryUserTemplateRepository:
    """Implémente `UserTemplateRepository` dans des dictionnaires."""

    def __init__(self) -> None:
        self.ownerships: list[Ownership] = []
        self.selections: dict[UUID, Selection] = {}
        self.forgotten: list[UUID] = []

    async def owned(self, user_id: UUID) -> list[Ownership]:
        return [o for o in self.ownerships if o.user_id == user_id]

    async def owns(self, user_id: UUID, template_id: UUID) -> bool:
        return any(o.user_id == user_id and o.template_id == template_id for o in self.ownerships)

    async def add(self, ownership: Ownership) -> Ownership:
        self.ownerships.append(ownership)
        return ownership

    async def selection(self, user_id: UUID) -> Selection | None:
        return self.selections.get(user_id)

    async def save_selection(self, selection: Selection) -> None:
        self.selections[selection.user_id] = selection

    async def forget(self, user_id: UUID) -> None:
        self.forgotten.append(user_id)
        self.ownerships = [o for o in self.ownerships if o.user_id != user_id]
        self.selections.pop(user_id, None)

    def give(self, user_id: UUID, template_id: UUID, source: str = "purchase") -> Ownership:
        """Range une possession."""
        ownership = Ownership(
            id=uuid4(), user_id=user_id, template_id=template_id, source=source, acquired_at=NOW
        )
        self.ownerships.append(ownership)
        return ownership


class InMemoryGeneratedCvRepository:
    """Implémente `GeneratedCvRepository` dans un dictionnaire."""

    def __init__(self) -> None:
        self.items: dict[UUID, GeneratedCv] = {}
        self.forgotten: list[UUID] = []

    async def add(self, generated: GeneratedCv) -> None:
        self.items[generated.id] = generated

    async def get(self, generated_id: UUID) -> GeneratedCv | None:
        return self.items.get(generated_id)

    async def history(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[GeneratedCv], int]:
        mine = sorted(
            (g for g in self.items.values() if g.user_id == user_id),
            key=lambda g: g.created_at,
            reverse=True,
        )
        return mine[offset : offset + limit], len(mine)

    async def delete(self, generated_id: UUID) -> None:
        self.items.pop(generated_id, None)

    async def set_explanation(
        self, generated_id: UUID, explanation: dict[str, Any] | None, status: ExplanationStatus
    ) -> None:
        self.items[generated_id] = replace(
            self.items[generated_id], explanation=explanation, explanation_status=status
        )

    async def of_user(self, user_id: UUID) -> list[GeneratedCv]:
        return [g for g in self.items.values() if g.user_id == user_id]

    async def forget(self, user_id: UUID) -> None:
        self.forgotten.append(user_id)
        self.items = {k: g for k, g in self.items.items() if g.user_id != user_id}


def make_generated(
    user_id: UUID,
    *,
    pdf_key: str | None = "generated-cvs/u/g/cv.pdf",
    docx_key: str | None = None,
    status: ExplanationStatus = ExplanationStatus.PENDING,
    ats_docx: dict[str, Any] | None = None,
) -> GeneratedCv:
    """Un export."""
    return GeneratedCv(
        id=uuid4(),
        user_id=user_id,
        template_id=uuid4(),
        template_version_id=uuid4(),
        template_label="Meridian",
        template_version=1,
        ats_safe=False,
        created_at=NOW,
        pdf_key=pdf_key,
        docx_key=docx_key,
        ats={"score": 88, "level": "good", "format": "pdf", "checks": []},
        ats_docx=ats_docx,
        explanation_status=status,
        full_name="Awa Diallo",
    )


# ---------------------------------------------------------------------- autres ports


class FakeCvSource:
    """Implémente `CvSource` : les CV programmés par personne."""

    def __init__(self) -> None:
        self.snapshots: dict[UUID, CvSnapshot] = {}

    async def snapshot(self, user_id: UUID) -> CvSnapshot | None:
        return self.snapshots.get(user_id)

    def put(
        self, user_id: UUID, data: dict[str, Any] | None = None, *, image_key: str | None = None
    ) -> CvSnapshot:
        """Range le CV d'une personne."""
        snap = CvSnapshot(
            data=copy.deepcopy(CV_DATA if data is None else data),
            image_key=image_key,
            updated_at=NOW,
        )
        self.snapshots[user_id] = snap
        return snap


@dataclass
class FakeEngine:
    """Implémente `DocumentEngine` avec des résultats programmés, et note ses appels."""

    analysis: Analysis | None = None
    analyze_error: Exception | None = None
    render_error: Exception | None = None
    fit: Fit = field(default_factory=lambda: Fit(spacing=0.95, font_scale=1.0, pages=1))
    fidelity_pdf: Fidelity = field(default_factory=good_fidelity)
    fidelity_docx: Fidelity = field(default_factory=good_fidelity)
    texts: dict[str, str] = field(default_factory=lambda: {"pdf": CLEAN_TEXT, "docx": CLEAN_TEXT})
    pages: int = 1
    calls: list[tuple[str, Any]] = field(default_factory=list)

    async def analyze(self, pdf: bytes) -> Analysis:
        self.calls.append(("analyze", pdf))
        if self.analyze_error is not None:
            raise self.analyze_error
        assert self.analysis is not None
        return self.analysis

    async def render_pdf(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo: bytes | None,
    ) -> RenderedPdf:
        self.calls.append(
            ("render_pdf", {"spec": spec, "view": view, "assets": assets, "photo": photo})
        )
        if self.render_error is not None:
            raise self.render_error
        return RenderedPdf(data=PDF_BYTES, fit=self.fit)

    async def render_docx(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo: bytes | None,
        fit: Fit,
    ) -> bytes:
        self.calls.append(("render_docx", {"fit": fit, "photo": photo}))
        return DOCX_BYTES

    async def docx_to_pdf(self, docx: bytes) -> bytes:
        self.calls.append(("docx_to_pdf", docx))
        return b"%PDF-1.7 docx"

    async def measure(self, source_pdf: bytes, rendered_pdf: bytes) -> Fidelity:
        self.calls.append(("measure", rendered_pdf))
        return self.fidelity_docx if rendered_pdf == b"%PDF-1.7 docx" else self.fidelity_pdf

    async def previews(self, pdf: bytes) -> Previews:
        self.calls.append(("previews", pdf))
        return Previews(
            full_png=PNG_BYTES,
            preview_png=PNG_BYTES,
            thumbnail_webp=b"RIFFwebp",
            blurhash="LKO2?U%2Tw=w",
        )

    async def page_images(self, pdf: bytes) -> list[tuple[bytes, int, int]]:
        self.calls.append(("page_images", pdf))
        return [(PNG_BYTES, 1240, 1754)] * self.pages

    def read(self, data: bytes, fmt: str) -> ExtractedDocument:
        self.calls.append(("read", fmt))
        return ExtractedDocument(
            text=self.texts[fmt],
            format=fmt,
            pages=1,
            font_types={"TrueType": 2},
            embedded=True,
            images=0,
            tables=0,
        )

    def called(self, name: str) -> list[Any]:
        """Les arguments des appels d'une méthode."""
        return [args for n, args in self.calls if n == name]


def make_analysis(
    *,
    spec: dict[str, Any] | None = None,
    unlabeled: list[str] | None = None,
    with_photo: bool = False,
) -> Analysis:
    """Le résultat d'analyse d'un PDF de modèle."""
    assets = {"icon_phone.png": PNG_BYTES}
    if with_photo:
        assets["sample_photo.png"] = PNG_BYTES
    return Analysis(
        spec=spec or make_spec(photo=with_photo),
        sample=make_sample(),
        labels={"0": "name"},
        assets=assets,
        fonts=[{"family": "Inter", "origin": "google"}],
        fonts_report=[
            {"family": "Inter", "origin": "google"},
            {"family": "Avenir", "origin": "substitute"},
        ],
        unlabeled=unlabeled or [],
    )


class FakeTemplateJobs:
    """Implémente `TemplateJobs` en notant ce qui a été différé."""

    def __init__(self) -> None:
        self.ingestions: list[UUID] = []
        self.explanations: list[UUID] = []

    async def defer_ingestion(self, version_id: UUID) -> None:
        self.ingestions.append(version_id)

    async def defer_explanation(self, generated_id: UUID) -> None:
        self.explanations.append(generated_id)


class FakeAtsExplainer:
    """Implémente `AtsExplainer` : une explication programmée (ou `None`)."""

    def __init__(self, result: dict[str, Any] | None) -> None:
        self.result = result
        self.reports: list[dict[str, Any]] = []

    async def explain(self, report: dict[str, Any]) -> dict[str, Any] | None:
        self.reports.append(report)
        return self.result
