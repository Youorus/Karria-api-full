"""Cas d'usage du module `documents` : galerie, choix, aperçu, exports, ingestion.

Contrat
-------
- **Ingestion** (`SubmitTemplate` puis `IngestTemplateVersion`, dans le
  worker) : PDF → analyse → rendu de l'exemple → mesure → porte de qualité →
  DOCX de l'exemple mesuré → aperçus → version publiée **ou** en revue. Rien
  n'est publié sous le seuil (ADR-0020 § 4, § 5).
- **Export** (`ExportCv`) : le CV de la personne dans la version publiée du
  modèle, PDF (Chromium) et DOCX (mêmes facteurs d'ajustement), fichiers
  rangés par clé (ADR-0008), score ATS déterministe sur chaque format ;
  l'explication est différée dans la file.
- Un modèle Pro ne se rend que pour qui le possède ; un CV non finalisé ne
  se rend pas (`cv_missing`) ; `offer_id` est réservé (`tailoring_not_available`).
- **Atelier** (`RenderDocument`, ADR-0029) : le CV **reçu** (le brouillon de
  l'app) ou une lettre (ADR-0030), rendus en HTML ancré par le même moteur
  que le PDF ; l'exemple du modèle pour la galerie. Aucun fichier écrit.
- **Lettre** (`ExportLetter`) : PDF et DOCX dans la grammaire du modèle,
  export `kind: letter`, sans score ATS.
- **Envoi** (`SendDocuments` puis `DeliverDocuments` dans le worker,
  ADR-0031) : journal, quota, pièces jointes par le centre d'e-mails.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid4

from karria.modules.documents.application.ports import (
    AccountDirectory,
    AtsExplainer,
    CvSnapshot,
    CvSource,
    DocumentEngine,
    DocumentFile,
    DocumentMailer,
    GeneratedCvRepository,
    LogoFetcher,
    OriginThrottle,
    RenderedHtml,
    SendRepository,
    TemplateJobs,
    TemplateRepository,
    UserTemplateRepository,
)
from karria.modules.documents.domain.ats import analyse
from karria.modules.documents.domain.errors import (
    AccountEmailUnknownError,
    CvMissingError,
    DocumentFileMissingError,
    GeneratedCvNotFoundError,
    RenderingUnavailableError,
    RenderTooLargeError,
    SendNotFoundError,
    SendQuotaExceededError,
    TailoringNotAvailableError,
    TemplateIngestionError,
    TemplateNotFoundError,
    TemplateNotFreeError,
    TemplateNotOwnedError,
)
from karria.modules.documents.domain.fonts import substituted_families
from karria.modules.documents.domain.gate import DOCX_SCORE_TARGET, evaluate
from karria.modules.documents.domain.headline import ats_safe_spec
from karria.modules.documents.domain.letter import from_letter, letter_warnings
from karria.modules.documents.domain.palette import recolor_spec
from karria.modules.documents.domain.sends import (
    MAX_DOCUMENTS_PER_SEND,
    DocumentSend,
    SendStatus,
    canonical_address,
    day_bounds,
    ensure_quota,
    ensure_recipient_quota,
    normalize_address,
    safe_attachment_name,
    safe_display_name,
)
from karria.modules.documents.domain.templates import (
    RENDER_MAX_HTML_BYTES,
    DocumentKind,
    ExplanationStatus,
    Fit,
    GeneratedCv,
    Ownership,
    Selection,
    Template,
    TemplateVersion,
    VersionStatus,
    ats_safe_helps,
    check_formats,
    ensure_usable,
    gallery_traits,
    slugify,
)
from karria.modules.documents.domain.view import from_cv, from_sample
from karria.shared.clock import Clock
from karria.shared.database import NullUnitOfWork, UnitOfWork
from karria.shared.errors.exceptions import InvalidInputError
from karria.shared.images import resize_to_png
from karria.shared.logging import get_logger
from karria.shared.mail import MailDeliveryError
from karria.shared.storage import Bucket, ObjectNotFoundError, ObjectStorage

logger = get_logger(__name__)

#: Entre dans la clé des aperçus en cache : à incrémenter quand le rendu change
#: (sinon un déploiement sert d'anciens aperçux tant que le CV ne bouge pas).
PREVIEW_CACHE_VERSION = 2

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
#: Le fichier joint qui porte le logo d'une lettre (ADR-0030 § 3).
LOGO_FILE: Final = "logo.png"
#: Côté maximal du logo d'une lettre converti en PNG (px) : un en-tête à 14 mm.
LOGO_MAX_EDGE: Final = 256
#: Les URL signées de photos, gardées stables le temps de leur validité
#: (ADR-0029 § 3) : la WebView met l'image en cache au lieu de la retélécharger
#: à chaque rendu. Par processus, bornée.
PHOTO_URL_TTL_SECONDS: Final = 45 * 60
PHOTO_URL_CACHE_SIZE: Final = 2_000
_photo_urls: OrderedDict[str, tuple[str, float]] = OrderedDict()


def version_prefix(template_id: UUID, version: int) -> str:
    """Le préfixe des fichiers d'une version : `templates/<modèle>/v<n>`."""
    return f"templates/{template_id}/v{version}"


async def _published(
    templates: TemplateRepository, template_id: UUID
) -> tuple[Template, TemplateVersion]:
    template = await templates.get(template_id)
    if template is None or template.published_version_id is None:
        raise TemplateNotFoundError()
    version = await templates.get_version(template.published_version_id)
    if version is None or version.spec is None:
        raise TemplateNotFoundError()
    return template, version


async def _assets(
    storage: ObjectStorage, version: TemplateVersion, *, sample: bool
) -> dict[str, bytes]:
    """Les fichiers d'une version (pictogrammes ; la photo d'exemple si demandée)."""
    out: dict[str, bytes] = {}
    for name, key in version.assets.items():
        if name.startswith("sample_") and not sample:
            continue
        out[name] = await storage.get(Bucket.TEMPLATES, key)
    return out


# ---------------------------------------------------------------------- galerie


class ListTemplates:
    """La galerie : les modèles publiés."""

    def __init__(self, templates: TemplateRepository) -> None:
        self._templates = templates

    async def execute(
        self, *, category: str | None = None, premium: bool | None = None
    ) -> list[Template]:
        return await self._templates.list_published(category=category, premium=premium)


class GetTemplate:
    """Un modèle publié et sa version publiée."""

    def __init__(self, templates: TemplateRepository) -> None:
        self._templates = templates

    async def execute(self, template_id: UUID) -> tuple[Template, TemplateVersion]:
        return await _published(self._templates, template_id)


class RecordTemplateView:
    """Compte une vue du détail d'un modèle."""

    def __init__(self, templates: TemplateRepository) -> None:
        self._templates = templates

    async def execute(self, template_id: UUID) -> None:
        await _published(self._templates, template_id)
        await self._templates.increment_views(template_id)


# ---------------------------------------------------------------------- possession et choix


class ListOwnedTemplates:
    """Les modèles que la personne possède."""

    def __init__(self, owned: UserTemplateRepository) -> None:
        self._owned = owned

    async def execute(self, user_id: UUID) -> list[Ownership]:
        return await self._owned.owned(user_id)


class AcquireFreeTemplate:
    """Ajoute un modèle gratuit aux modèles de la personne (idempotent)."""

    def __init__(
        self, templates: TemplateRepository, owned: UserTemplateRepository, clock: Clock
    ) -> None:
        self._templates, self._owned, self._clock = templates, owned, clock

    async def execute(self, user_id: UUID, template_id: UUID) -> Ownership:
        template, _ = await _published(self._templates, template_id)
        if template.is_premium:
            raise TemplateNotFreeError()
        for existing in await self._owned.owned(user_id):
            if existing.template_id == template_id:
                return existing
        return await self._owned.add(
            Ownership(
                id=uuid4(),
                user_id=user_id,
                template_id=template_id,
                source="free",
                acquired_at=self._clock.now(),
            )
        )


class GetSelection:
    """Le modèle choisi par la personne, s'il y en a un."""

    def __init__(self, owned: UserTemplateRepository) -> None:
        self._owned = owned

    async def execute(self, user_id: UUID) -> Selection | None:
        return await self._owned.selection(user_id)


class SelectTemplate:
    """Choisit le modèle par défaut des exports (et la variante optimisée ATS)."""

    def __init__(
        self, templates: TemplateRepository, owned: UserTemplateRepository, clock: Clock
    ) -> None:
        self._templates, self._owned, self._clock = templates, owned, clock

    async def execute(
        self,
        user_id: UUID,
        template_id: UUID,
        *,
        ats_safe: bool,
        primary_color: str | None = None,
    ) -> Selection:
        """`primary_color` (`#RRGGBB`, déjà normalisée) : la couleur posée ; `None` : celle du modèle."""
        template, _ = await _published(self._templates, template_id)
        ensure_usable(template, owned=await self._owned.owns(user_id, template_id))
        selection = Selection(
            user_id=user_id,
            template_id=template_id,
            ats_safe=ats_safe,
            updated_at=self._clock.now(),
            primary_color=primary_color,
        )
        await self._owned.save_selection(selection)
        return selection


# ---------------------------------------------------------------------- vente


@dataclass(frozen=True, slots=True)
class PremiumTemplate:
    """Un modèle Premium en vente : ce que le module `payments` a besoin de savoir."""

    template_id: UUID
    label: str
    price: Decimal


class TemplateShop:
    """La vitrine des modèles Premium, et leur remise une fois payés.

    Le prix vient d'ici (`Template.price`) : `documents` est la source de
    vérité, `payments` encaisse. `grant` est **idempotent** : le prestataire
    rejoue ses notifications, et un modèle remis deux fois n'est qu'une ligne.
    """

    def __init__(
        self, templates: TemplateRepository, owned: UserTemplateRepository, clock: Clock
    ) -> None:
        self._templates, self._owned, self._clock = templates, owned, clock

    async def premium_offers(self) -> list[PremiumTemplate]:
        """Les modèles publiés, payants et dotés d'un prix."""
        return [
            PremiumTemplate(t.id, t.label, t.price)
            for t in await self._templates.list_published(category=None, premium=True)
            if t.price is not None
        ]

    async def premium_offer(self, template_id: UUID) -> PremiumTemplate | None:
        """Ce modèle, s'il est publié, payant et doté d'un prix ; `None` sinon."""
        template = await self._templates.get(template_id)
        if (
            template is None
            or not template.is_published
            or not template.is_premium
            or template.price is None
        ):
            return None
        return PremiumTemplate(template.id, template.label, template.price)

    async def owns(self, user_id: UUID, template_id: UUID) -> bool:
        return await self._owned.owns(user_id, template_id)

    async def grant(self, user_id: UUID, template_id: UUID) -> Ownership:
        """Remet un modèle payé. Le modèle doit exister ; publié ou non, il a été payé."""
        if await self._templates.get(template_id) is None:
            raise TemplateNotFoundError()
        return await self._owned.add(
            Ownership(
                id=uuid4(),
                user_id=user_id,
                template_id=template_id,
                source="purchase",
                acquired_at=self._clock.now(),
            )
        )


# ---------------------------------------------------------------------- rendu du CV


class CvRenderer:
    """Le rendu commun à l'aperçu et à l'export."""

    def __init__(
        self,
        templates: TemplateRepository,
        owned: UserTemplateRepository,
        cvs: CvSource,
        engine: DocumentEngine,
        storage: ObjectStorage,
    ) -> None:
        self._templates, self._owned, self._cvs = templates, owned, cvs
        self._engine, self._storage = engine, storage

    async def prepare(
        self,
        user_id: UUID,
        template_id: UUID,
        *,
        ats_safe: bool,
        cv: dict[str, Any] | None = None,
        primary_color: str | None = None,
    ) -> tuple[
        Template,
        TemplateVersion,
        dict[str, Any],
        dict[str, Any],
        Any,
        bytes | None,
        dict[str, bytes],
    ]:
        """
        Args:
            cv: Un CV **reçu** (la copie adaptée d'un dossier de candidature, même forme
                que `GET /cv/me`) rendu à la place du CV de base de la personne ; la photo
                reste celle de son CV. `None` : le CV de base, qui doit exister (`cv_missing`).
            primary_color: La couleur principale choisie (`#RRGGBB`), appliquée après la
                variante ATS (ADR-0034) ; `None` : celle du modèle.
        """
        template, version = await _published(self._templates, template_id)
        ensure_usable(template, owned=await self._owned.owns(user_id, template_id))
        snapshot = await self._cvs.snapshot(user_id)
        if cv is not None:
            snapshot = CvSnapshot(
                data=cv,
                image_key=snapshot.image_key if snapshot else None,
                updated_at=snapshot.updated_at if snapshot else datetime.min.replace(tzinfo=UTC),
            )
        if snapshot is None:
            raise CvMissingError()
        spec = version.spec or {}
        if ats_safe:
            spec = ats_safe_spec(spec)
        spec = recolor_spec(spec, primary_color)
        view = from_cv(spec, snapshot.data)
        photo = None
        if spec["header"].get("photo") and snapshot.image_key:
            try:
                photo = await self._storage.get(Bucket.MEDIA, snapshot.image_key)
            except ObjectNotFoundError:
                logger.warning("documents.cv_photo_missing", user_id=str(user_id))
        assets = await _assets(self._storage, version, sample=False)
        return template, version, spec, view, snapshot, photo, assets


class PreviewTemplate:
    """Le CV de la personne dans un modèle, en images, sans rien enregistrer d'autre qu'un cache."""

    def __init__(
        self, renderer: CvRenderer, engine: DocumentEngine, storage: ObjectStorage
    ) -> None:
        self._renderer, self._engine, self._storage = renderer, engine, storage

    async def execute(
        self, user_id: UUID, template_id: UUID, *, ats_safe: bool
    ) -> tuple[list[dict[str, Any]], Fit]:
        _, version, spec, view, snapshot, photo, assets = await self._renderer.prepare(
            user_id, template_id, ats_safe=ats_safe
        )
        digest = hashlib.sha256(
            json.dumps(
                [
                    PREVIEW_CACHE_VERSION,
                    str(version.id),
                    snapshot.updated_at.isoformat(),
                    snapshot.image_key,
                    ats_safe,
                ],
                default=str,
            ).encode()
        ).hexdigest()[:24]
        prefix = f"previews/{user_id}/{digest}"
        try:
            cached = json.loads(await self._storage.get(Bucket.TEMP, f"{prefix}/fit.json"))
            return cached["pages"], Fit(**cached["fit"])
        except ObjectNotFoundError:
            pass
        rendered = await self._engine.render_pdf(spec, view, assets=assets, photo=photo)
        pages = []
        for n, (png, width, height) in enumerate(await self._engine.page_images(rendered.data), 1):
            key = f"{prefix}/page-{n}.png"
            await self._storage.put(Bucket.TEMP, key, png, content_type="image/png")
            pages.append({"key": key, "width": width, "height": height})
        await self._storage.put(
            Bucket.TEMP,
            f"{prefix}/fit.json",
            json.dumps({"pages": pages, "fit": rendered.fit.to_json()}).encode(),
            content_type="application/json",
        )
        return pages, rendered.fit


class ExportCv:
    """Exporte le CV de la personne en PDF et/ou DOCX, avec son score ATS."""

    def __init__(
        self,
        *,
        renderer: CvRenderer,
        templates: TemplateRepository,
        owned: UserTemplateRepository,
        generated: GeneratedCvRepository,
        engine: DocumentEngine,
        storage: ObjectStorage,
        jobs: TemplateJobs,
        clock: Clock,
    ) -> None:
        self._renderer, self._templates, self._owned = renderer, templates, owned
        self._generated, self._engine, self._storage = generated, engine, storage
        self._jobs, self._clock = jobs, clock

    async def execute(
        self,
        user_id: UUID,
        *,
        template_id: UUID | None,
        formats: list[str] | None,
        ats_safe: bool | None,
        offer_id: UUID | None,
        dossier_id: UUID | None = None,
        cv: dict[str, Any] | None = None,
        primary_color: str | None = None,
    ) -> GeneratedCv:
        """`cv` : la copie adaptée d'un dossier à exporter à la place du CV de base (ADR-0033).

        `primary_color` : la couleur principale choisie (`#RRGGBB`) ; sans
        `template_id`, celle du choix de la personne, comme `ats_safe` (ADR-0034).
        """
        if offer_id is not None:
            raise TailoringNotAvailableError()
        wanted = check_formats(formats)
        if template_id is None:
            selection = await self._owned.selection(user_id)
            if selection is None:
                raise TemplateNotFoundError()
            template_id = selection.template_id
            if ats_safe is None:
                ats_safe = selection.ats_safe
            if primary_color is None:
                primary_color = selection.primary_color
        safe = bool(ats_safe)
        started = time.monotonic()
        template, version, spec, view, snapshot, photo, assets = await self._renderer.prepare(
            user_id, template_id, ats_safe=safe, cv=cv, primary_color=primary_color
        )
        rendered = await self._engine.render_pdf(spec, view, assets=assets, photo=photo)
        docx = None
        if "docx" in wanted:
            docx = await self._engine.render_docx(
                spec, view, assets=assets, photo=photo, fit=rendered.fit
            )
        columns = max((len(b["columns"]) for b in spec["bands"]), default=1)
        # La lecture « comme un ATS » (pdfminer) coûte des centaines de ms de CPU : hors boucle.
        report_pdf = analyse(
            await asyncio.to_thread(self._engine.read, rendered.data, "pdf"),
            view,
            snapshot.data,
            columns=columns,
        )
        report_docx = (
            analyse(
                await asyncio.to_thread(self._engine.read, docx, "docx"),
                view,
                snapshot.data,
                columns=columns,
            )
            if docx
            else None
        )
        main = report_pdf if "pdf" in wanted or report_docx is None else report_docx
        suggest = (
            await self._variant_helps(
                version.spec or spec,
                view,
                assets=assets,
                photo=photo,
                cv=snapshot.data,
                columns=columns,
                score=main.score,
            )
            if not safe and ats_safe_helps(version.spec or spec) and main.spaced_titles()
            else False
        )
        generated_id = uuid4()
        prefix = f"generated-cvs/{user_id}/{generated_id}"
        pdf_key = docx_key = None
        if "pdf" in wanted:
            pdf_key = f"{prefix}/cv.pdf"
            await self._storage.put(Bucket.DOCUMENTS, pdf_key, rendered.data, content_type=PDF)
        if docx is not None:
            docx_key = f"{prefix}/cv.docx"
            await self._storage.put(Bucket.DOCUMENTS, docx_key, docx, content_type=DOCX)
        ident = snapshot.data.get("identity") or {}
        generated = GeneratedCv(
            id=generated_id,
            user_id=user_id,
            template_id=template.id,
            template_version_id=version.id,
            template_label=template.label,
            template_version=version.version,
            ats_safe=safe,
            created_at=self._clock.now(),
            pdf_key=pdf_key,
            docx_key=docx_key,
            fit=rendered.fit,
            ats=main.to_json(suggest_ats_safe=suggest),
            ats_docx=report_docx.to_json(suggest_ats_safe=suggest) if report_docx else None,
            explanation=None,
            explanation_status=ExplanationStatus.PENDING,
            full_name=" ".join(p for p in (ident.get("first_name"), ident.get("last_name")) if p),
            dossier_id=dossier_id,
            primary_color=primary_color,
        )
        await self._generated.add(generated)
        await self._templates.increment_uses(template.id)
        try:
            await self._jobs.defer_explanation(generated.id)
        except Exception:
            # L'export existe et ses fichiers sont là : une file en panne ne doit
            # pas le faire passer pour raté. L'explication est simplement absente.
            logger.exception("documents.explanation_not_deferred", generated_id=str(generated.id))
            generated = replace(generated, explanation_status=ExplanationStatus.UNAVAILABLE)
            await self._generated.set_explanation(generated.id, None, ExplanationStatus.UNAVAILABLE)
        logger.info(
            "documents.cv_exported",
            template=template.name,
            version=version.version,
            formats=",".join(wanted),
            ats=main.score,
            ats_docx=report_docx.score if report_docx else None,
            pages=rendered.fit.pages,
            ms=round((time.monotonic() - started) * 1000),
        )
        return generated

    async def _variant_helps(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo: bytes | None,
        cv: dict[str, Any],
        columns: int,
        score: int,
    ) -> bool:
        """La variante « optimisée ATS » n'est proposée que si, rendue et notée, elle fait mieux.

        Avant, elle était proposée sur un indice textuel (« lu lettre à lettre »)
        sans être mesurée ; sur Minimalist elle perdait 4 points (87 → 83, l'ordre
        de lecture de pdfminer). Un rendu de plus, seulement quand l'indice existe.
        """
        variant = await self._engine.render_pdf(
            ats_safe_spec(spec), view, assets=assets, photo=photo
        )
        report = analyse(
            await asyncio.to_thread(self._engine.read, variant.data, "pdf"),
            view,
            cv,
            columns=columns,
        )
        logger.info("documents.ats_variant_measured", main=score, variant=report.score)
        return report.score > score


class ListGeneratedCvs:
    """L'historique des exports, les plus récents d'abord."""

    def __init__(self, generated: GeneratedCvRepository) -> None:
        self._generated = generated

    async def execute(
        self,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
        kind: DocumentKind | None = None,
        dossier_id: UUID | None = None,
    ) -> tuple[list[GeneratedCv], int]:
        return await self._generated.history(
            user_id, limit=limit, offset=offset, kind=kind, dossier_id=dossier_id
        )


class GetGeneratedCv:
    """Un export de la personne."""

    def __init__(self, generated: GeneratedCvRepository) -> None:
        self._generated = generated

    async def execute(self, user_id: UUID, generated_id: UUID) -> GeneratedCv:
        found = await self._generated.get(generated_id)
        if found is None or found.user_id != user_id:
            raise GeneratedCvNotFoundError()
        return found


class DeleteGeneratedCv:
    """Supprime un export et ses fichiers."""

    def __init__(self, generated: GeneratedCvRepository, storage: ObjectStorage) -> None:
        self._generated, self._storage = generated, storage

    async def execute(self, user_id: UUID, generated_id: UUID) -> None:
        found = await GetGeneratedCv(self._generated).execute(user_id, generated_id)
        for key in (found.pdf_key, found.docx_key):
            if key:
                await self._storage.delete(Bucket.DOCUMENTS, key)
        await self._generated.delete(generated_id)


class ExplainAts:
    """Rédige l'explication du score d'un export (travail de file)."""

    def __init__(self, generated: GeneratedCvRepository, explainer: AtsExplainer | None) -> None:
        self._generated, self._explainer = generated, explainer

    async def execute(self, generated_id: UUID) -> ExplanationStatus | None:
        found = await self._generated.get(generated_id)
        if found is None or found.explanation_status is not ExplanationStatus.PENDING:
            return None
        explanation = await self._explainer.explain(found.ats) if self._explainer else None
        status = ExplanationStatus.READY if explanation else ExplanationStatus.UNAVAILABLE
        await self._generated.set_explanation(generated_id, explanation, status)
        return status


class ForgetUser:
    """Oublie les exports, possessions et choix d'un compte supprimé (ADR-0015)."""

    def __init__(
        self,
        generated: GeneratedCvRepository,
        owned: UserTemplateRepository,
        storage: ObjectStorage,
        sends: SendRepository | None = None,
    ) -> None:
        self._generated, self._owned, self._storage = generated, owned, storage
        self._sends = sends

    async def execute(self, user_id: UUID) -> int:
        exports = await self._generated.of_user(user_id)
        for export in exports:
            for key in (export.pdf_key, export.docx_key):
                if key:
                    await self._storage.delete(Bucket.DOCUMENTS, key)
        await self._generated.forget(user_id)
        await self._owned.forget(user_id)
        if self._sends is not None:
            # Le journal des envois porte des adresses : une donnée personnelle.
            await self._sends.forget(user_id)
        return len(exports)


# ---------------------------------------------------------------------- ingestion


class SubmitTemplate:
    """Dépose le PDF d'un modèle : nouvelle version en attente d'analyse (administration)."""

    def __init__(
        self,
        templates: TemplateRepository,
        storage: ObjectStorage,
        jobs: TemplateJobs,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._templates, self._storage, self._jobs = templates, storage, jobs
        self._clock, self._uow = clock, uow

    async def execute(
        self,
        pdf: bytes,
        *,
        name: str,
        label: str | None = None,
        is_premium: bool = False,
        price: Any = None,
        defer: bool = True,
    ) -> TemplateVersion:
        if not pdf.startswith(b"%PDF"):
            raise TemplateIngestionError("le fichier n'est pas un PDF")
        slug = slugify(name)
        now = self._clock.now()
        async with self._uow.atomic():
            template = await self._templates.get_by_name(slug)
            if template is None:
                template = Template(
                    id=uuid4(),
                    name=slug,
                    label=label or name.strip().title(),
                    category="",
                    description="",
                    tags=(),
                    is_premium=is_premium,
                    price=price,
                    primary_color=None,
                    font_family=None,
                    has_photo=False,
                    columns=1,
                    published_version_id=None,
                    published_version=None,
                    created_at=now,
                    updated_at=now,
                )
            else:
                template = replace(
                    template,
                    label=label or template.label,
                    is_premium=is_premium,
                    price=price,
                    updated_at=now,
                )
            await self._templates.save(template)
            number = await self._templates.next_version_number(template.id)
            key = f"{version_prefix(template.id, number)}/source.pdf"
            await self._storage.put(Bucket.TEMPLATES, key, pdf, content_type=PDF)
            version = TemplateVersion(
                id=uuid4(),
                template_id=template.id,
                version=number,
                status=VersionStatus.PROCESSING,
                source_key=key,
                created_at=now,
            )
            await self._templates.save_version(version)
        if defer:
            await self._jobs.defer_ingestion(version.id)
        logger.info("documents.template_submitted", template=slug, version=number)
        return version


class IngestTemplateVersion:
    """Analyse une version, la mesure, et la publie si elle passe la porte de qualité."""

    def __init__(
        self,
        templates: TemplateRepository,
        engine: DocumentEngine,
        storage: ObjectStorage,
        clock: Clock,
    ) -> None:
        self._templates, self._engine, self._storage, self._clock = (
            templates,
            engine,
            storage,
            clock,
        )

    async def execute(self, version_id: UUID) -> TemplateVersion | None:
        version = await self._templates.get_version(version_id)
        if version is None or version.status is not VersionStatus.PROCESSING:
            return version
        template = await self._templates.get(version.template_id)
        if template is None:
            return None
        try:
            return await self._ingest(template, version)
        except TemplateIngestionError as exc:
            failed = version.with_status(
                VersionStatus.FAILED, error=str(exc.details.get("detail") or exc)
            )
        except RenderingUnavailableError:
            # Gotenberg absent : rien n'est faux dans le modèle. La version reste
            # « en cours » et le travail de file se rejoue (avant : échec définitif).
            raise
        except Exception as exc:
            logger.exception("documents.ingestion_crashed", version_id=str(version_id))
            failed = version.with_status(
                VersionStatus.FAILED, error=f"{type(exc).__name__}: {exc}"[:500]
            )
        await self._templates.save_version(failed)
        return failed

    async def _ingest(  # noqa: PLR0915 - la chaîne d'ingestion se lit d'un trait, étape par étape
        self, template: Template, version: TemplateVersion
    ) -> TemplateVersion:
        timings: dict[str, float] = {}
        t = time.monotonic()
        source = await self._storage.get(Bucket.TEMPLATES, version.source_key)
        try:
            analysis = await self._engine.analyze(source)
        except ValueError as exc:
            raise TemplateIngestionError(str(exc)) from exc
        timings["analyze_s"] = round(time.monotonic() - t, 1)
        spec, sample = analysis.spec, analysis.sample
        prefix = version_prefix(template.id, version.version)
        asset_keys: dict[str, str] = {}
        for name, data in analysis.assets.items():
            key = f"{prefix}/assets/{name}"
            await self._storage.put(Bucket.TEMPLATES, key, data, content_type="image/png")
            asset_keys[name] = key
        view = from_sample(spec, sample)
        photo_name = (spec["header"].get("photo") or {}).get("sample_file")
        photo = analysis.assets.get(photo_name) if photo_name else None
        icons = {k: v for k, v in analysis.assets.items() if not k.startswith("sample_")}
        t = time.monotonic()
        rendered = await self._engine.render_pdf(spec, view, assets=icons, photo=photo)
        timings["render_pdf_s"] = round(time.monotonic() - t, 1)
        t = time.monotonic()
        # Une substitution de police déclarée se juge dans la revue, pas dans la porte.
        substituted = substituted_families(analysis.fonts_report)
        fidelity_pdf = await self._engine.measure(source, rendered.data, substituted=substituted)
        timings["measure_s"] = round(time.monotonic() - t, 1)
        decision = evaluate(fidelity_pdf)
        failures = list(decision.failures)
        if analysis.unlabeled:
            failures.append(f"lignes non placées : {', '.join(analysis.unlabeled)}")
        t = time.monotonic()
        docx = await self._engine.render_docx(
            spec, view, assets=icons, photo=photo, fit=rendered.fit
        )
        docx_pdf = await self._engine.docx_to_pdf(docx)
        fidelity_docx = await self._engine.measure(source, docx_pdf, substituted=substituted)
        timings["docx_s"] = round(time.monotonic() - t, 1)
        previews = await self._engine.previews(rendered.data)
        keys = {
            "thumbnail_key": f"{prefix}/preview/vignette.webp",
            "preview_key": f"{prefix}/preview/apercu.png",
            "full_key": f"{prefix}/preview/apercu_pleine_page.png",
        }
        await self._storage.put(
            Bucket.TEMPLATES,
            keys["thumbnail_key"],
            previews.thumbnail_webp,
            content_type="image/webp",
        )
        await self._storage.put(
            Bucket.TEMPLATES, keys["preview_key"], previews.preview_png, content_type="image/png"
        )
        await self._storage.put(
            Bucket.TEMPLATES, keys["full_key"], previews.full_png, content_type="image/png"
        )
        await self._storage.put(
            Bucket.TEMPLATES, f"{prefix}/sample_render.pdf", rendered.data, content_type=PDF
        )
        await self._storage.put(
            Bucket.TEMPLATES, f"{prefix}/sample_docx.pdf", docx_pdf, content_type=PDF
        )
        passed = not failures
        report = {
            "fidelity": {
                "pdf": fidelity_pdf.to_json(),
                "docx": {**fidelity_docx.to_json(), "target": DOCX_SCORE_TARGET},
            },
            "gate": {"passed": passed, "failures": failures},
            "fonts": analysis.fonts_report,
            "substitutions": [f for f in analysis.fonts_report if f.get("origin") == "substitute"],
            "unlabeled": analysis.unlabeled,
            "fit": rendered.fit.to_json(),
            "timings": timings,
            "passes": analysis.passes,
        }
        now = self._clock.now()
        status = VersionStatus.PUBLISHED if passed else VersionStatus.REVIEW
        done = version.with_status(
            status,
            spec=spec,
            sample=sample,
            labels=analysis.labels,
            report=report,
            assets=asset_keys,
            fonts=tuple(analysis.fonts),
            published_at=now if passed else None,
            thumbnail_blurhash=previews.blurhash,
            error=None,
            **keys,
        )
        await self._templates.save_version(done)
        traits = gallery_traits(spec)
        meta = spec.get("meta") or {}
        updated = replace(
            template,
            category=template.category or str(meta.get("category") or ""),
            description=template.description or str(meta.get("description_fr") or ""),
            tags=template.tags or tuple(str(t) for t in meta.get("tags") or ()),
            updated_at=now,
        )
        if passed:
            updated = replace(
                updated,
                primary_color=traits["primary_color"],
                font_family=traits["font_family"],
                has_photo=traits["has_photo"],
                columns=traits["columns"],
                published_version_id=done.id,
                published_version=done.version,
            )
        await self._templates.save(updated)
        logger.info(
            "documents.template_ingested",
            template=template.name,
            version=version.version,
            status=status.value,
            fidelity_pdf=fidelity_pdf.score,
            fidelity_docx=fidelity_docx.score,
            substitutions=sum(1 for f in analysis.fonts_report if f.get("origin") == "substitute"),
            failures=len(failures),
        )
        return done


# ---------------------------------------------------------------------- atelier (ADR-0029)


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """Ce que l'atelier affiche : le HTML ancré, les pages estimées, les avertissements."""

    html: str
    pages: int
    warnings: tuple[str, ...]


async def stable_photo_url(storage: ObjectStorage, key: str) -> str:
    """L'URL signée d'une photo, la même d'un rendu à l'autre tant qu'elle vaut."""
    now = time.monotonic()
    cached = _photo_urls.get(key)
    if cached is not None and cached[1] > now:
        _photo_urls.move_to_end(key)
        return cached[0]
    url = await storage.presigned_get_url(Bucket.MEDIA, key)
    _photo_urls[key] = (url, now + PHOTO_URL_TTL_SECONDS)
    while len(_photo_urls) > PHOTO_URL_CACHE_SIZE:
        _photo_urls.popitem(last=False)
    return url


async def fetch_logo(logos: LogoFetcher, url: str | None) -> bytes | None:
    """Le logo d'une lettre en PNG, ou `None` (absent, refusé, injoignable, illisible)."""
    if not url:
        return None
    data = await logos.fetch(url)
    if data is None:
        return None
    try:
        return await asyncio.to_thread(resize_to_png, data, max_edge=LOGO_MAX_EDGE)
    except Exception:
        logger.warning("documents.logo_unreadable")
        return None


class RenderDocument:
    """Le CV reçu (ou une lettre, ou l'exemple du modèle) dans un modèle, en HTML ancré.

    Réutilisable par un autre module (façade `documents.build_render_document`) :
    il reçoit le CV **en données**, il ne lit pas celui de la personne — sauf
    pour retrouver sa photo quand le modèle en a l'emplacement.
    """

    def __init__(
        self,
        *,
        templates: TemplateRepository,
        owned: UserTemplateRepository,
        cvs: CvSource,
        engine: DocumentEngine,
        storage: ObjectStorage,
        logos: LogoFetcher,
        clock: Clock,
    ) -> None:
        self._templates, self._owned, self._cvs = templates, owned, cvs
        self._engine, self._storage, self._logos, self._clock = engine, storage, logos, clock

    async def execute(
        self,
        user_id: UUID,
        template_id: UUID,
        *,
        cv: dict[str, Any] | None = None,
        letter: dict[str, Any] | None = None,
        sample: bool = False,
        ats_safe: bool = False,
        primary_color: str | None = None,
    ) -> RenderedDocument:
        """
        `primary_color` (`#RRGGBB`) : la couleur principale choisie, appliquée
        après la variante ATS, à l'exemple comme au CV ou à la lettre (ADR-0034).

        Raises:
            TemplateNotFoundError: Modèle inconnu ou non publié.
            TemplateNotOwnedError: Modèle Premium non possédé (l'exemple reste visible).
            RenderTooLargeError: Le HTML dépasse la taille admise.
            InvalidInputError: Ni CV, ni lettre, ni exemple demandé.
        """
        started = time.monotonic()
        template, version = await _published(self._templates, template_id)
        spec = ats_safe_spec(version.spec or {}) if ats_safe else (version.spec or {})
        spec = recolor_spec(spec, primary_color)
        if sample:
            kind = "sample"
            rendered, warnings = await self._sample(spec, version)
        else:
            if template.is_premium and not await self._owned.owns(user_id, template_id):
                raise TemplateNotOwnedError()
            if letter is not None:
                kind = "letter"
                rendered, warnings = await self._letter(spec, letter)
            elif cv is not None:
                kind = "cv"
                rendered, warnings = await self._cv(spec, version, user_id, cv)
            else:
                raise InvalidInputError(
                    "Il faut un CV, une lettre ou l'exemple du modèle.",
                    details={"reason": "nothing_to_render"},
                )
        size = len(rendered.html.encode("utf-8"))
        if size > RENDER_MAX_HTML_BYTES:
            raise RenderTooLargeError(size, RENDER_MAX_HTML_BYTES)
        if rendered.pages > 1:
            warnings.append("more_than_one_page")
        logger.info(
            "documents.rendered_html",
            template=template.name,
            kind=kind,
            pages=rendered.pages,
            bytes=size,
            ms=round((time.monotonic() - started) * 1000),
        )
        return RenderedDocument(html=rendered.html, pages=rendered.pages, warnings=tuple(warnings))

    async def _sample(
        self, spec: dict[str, Any], version: TemplateVersion
    ) -> tuple[RenderedHtml, list[str]]:
        """L'exemple du modèle, sans ancres, photo d'exemple comprise."""
        view = from_sample(spec, version.sample or {})
        assets = await _assets(self._storage, version, sample=True)
        photo_name = (spec["header"].get("photo") or {}).get("sample_file")
        photo = assets.pop(photo_name, None) if photo_name else None
        if photo is not None:
            assets["photo"] = photo
        rendered = await self._engine.render_html(
            spec, view, assets=assets, photo_url="photo" if photo else None, anchored=False
        )
        return rendered, []

    async def _letter(
        self, spec: dict[str, Any], letter: dict[str, Any]
    ) -> tuple[RenderedHtml, list[str]]:
        """Une lettre, ancrée, avec le logo s'il est joignable."""
        view = from_letter(spec, letter, today=self._clock.now().date())
        warnings = letter_warnings(letter)
        logo_url = (letter.get("recipient") or {}).get("logo_url")
        assets: dict[str, bytes] = {}
        logo = await fetch_logo(self._logos, logo_url)
        if logo is not None:
            assets[LOGO_FILE] = logo
        elif logo_url:
            warnings.append("logo_unavailable")
        rendered = await self._engine.render_html(
            spec, view, assets=assets, photo_url=None, anchored=True
        )
        return rendered, warnings

    async def _cv(
        self, spec: dict[str, Any], version: TemplateVersion, user_id: UUID, cv: dict[str, Any]
    ) -> tuple[RenderedHtml, list[str]]:
        """Le CV reçu, ancré, avec la photo de la personne si le modèle en a la place."""
        view = from_cv(spec, cv)
        warnings: list[str] = []
        if not view["header"]["headline"]:
            warnings.append("headline_missing")
        photo_url: str | None = None
        if spec["header"].get("photo"):
            snapshot = await self._cvs.snapshot(user_id)
            if snapshot is not None and snapshot.image_key:
                photo_url = await stable_photo_url(self._storage, snapshot.image_key)
            else:
                warnings.append("photo_missing")
        assets = await _assets(self._storage, version, sample=False)
        rendered = await self._engine.render_html(
            spec, view, assets=assets, photo_url=photo_url, anchored=True
        )
        return rendered, warnings


# ---------------------------------------------------------------------- lettre (ADR-0030)


class ExportLetter:
    """Exporte une lettre en PDF et/ou DOCX, dans la grammaire du modèle, sans score ATS."""

    def __init__(
        self,
        *,
        templates: TemplateRepository,
        owned: UserTemplateRepository,
        generated: GeneratedCvRepository,
        engine: DocumentEngine,
        storage: ObjectStorage,
        logos: LogoFetcher,
        clock: Clock,
    ) -> None:
        self._templates, self._owned, self._generated = templates, owned, generated
        self._engine, self._storage, self._logos, self._clock = engine, storage, logos, clock

    async def execute(
        self,
        user_id: UUID,
        *,
        template_id: UUID | None,
        letter: dict[str, Any],
        formats: list[str] | None,
        dossier_id: UUID | None,
        primary_color: str | None = None,
    ) -> GeneratedCv:
        """`primary_color` : la couleur principale du dossier, pour que la lettre suive le CV."""
        wanted = check_formats(formats)
        if template_id is None:
            selection = await self._owned.selection(user_id)
            if selection is None:
                raise TemplateNotFoundError()
            template_id = selection.template_id
            if primary_color is None:
                primary_color = selection.primary_color
        template, version = await _published(self._templates, template_id)
        ensure_usable(template, owned=await self._owned.owns(user_id, template_id))
        spec = recolor_spec(version.spec or {}, primary_color)
        view = from_letter(spec, letter, today=self._clock.now().date())
        assets: dict[str, bytes] = {}
        logo = await fetch_logo(self._logos, (letter.get("recipient") or {}).get("logo_url"))
        if logo is not None:
            assets[LOGO_FILE] = logo
        started = time.monotonic()
        rendered = await self._engine.render_pdf(spec, view, assets=assets, photo=None)
        docx = (
            await self._engine.render_docx(spec, view, assets=assets, photo=None, fit=rendered.fit)
            if "docx" in wanted
            else None
        )
        generated_id = uuid4()
        prefix = f"generated-letters/{user_id}/{generated_id}"
        pdf_key = docx_key = None
        if "pdf" in wanted:
            pdf_key = f"{prefix}/lettre.pdf"
            await self._storage.put(Bucket.DOCUMENTS, pdf_key, rendered.data, content_type=PDF)
        if docx is not None:
            docx_key = f"{prefix}/lettre.docx"
            await self._storage.put(Bucket.DOCUMENTS, docx_key, docx, content_type=DOCX)
        generated = GeneratedCv(
            id=generated_id,
            user_id=user_id,
            template_id=template.id,
            template_version_id=version.id,
            template_label=template.label,
            template_version=version.version,
            ats_safe=False,
            created_at=self._clock.now(),
            pdf_key=pdf_key,
            docx_key=docx_key,
            fit=rendered.fit,
            ats={},
            ats_docx=None,
            explanation=None,
            explanation_status=ExplanationStatus.UNAVAILABLE,
            full_name=view["header"]["full_name"],
            kind=DocumentKind.LETTER,
            dossier_id=dossier_id,
            company_name=view["recipient"]["company"],
            primary_color=primary_color,
        )
        await self._generated.add(generated)
        await self._templates.increment_uses(template.id)
        logger.info(
            "documents.letter_exported",
            template=template.name,
            formats=",".join(wanted),
            pages=rendered.fit.pages,
            logo=logo is not None,
            ms=round((time.monotonic() - started) * 1000),
        )
        return generated


# ---------------------------------------------------------------------- envoi (ADR-0031)


class SendDocuments:
    """Demande l'envoi d'un ou plusieurs exports à une adresse : journal, quotas, file.

    Trois quotas (ADR-0031, revue de sécurité) : cinq envois par jour et par
    compte ; vers une adresse **tierce** seulement, deux envois par jour vers
    la même adresse (tous comptes confondus) et un plafond par origine
    (`OriginThrottle`). Chaque refus est journalisé (`documents.send_refused`)
    : c'est le journal d'abus.
    """

    def __init__(
        self,
        *,
        generated: GeneratedCvRepository,
        sends: SendRepository,
        accounts: AccountDirectory,
        jobs: TemplateJobs,
        clock: Clock,
        origins: OriginThrottle | None = None,
        uow: UnitOfWork | None = None,
    ) -> None:
        self._generated, self._sends, self._accounts = generated, sends, accounts
        self._jobs, self._clock, self._origins = jobs, clock, origins
        self._uow = uow

    async def execute(
        self,
        user_id: UUID,
        *,
        generated_ids: list[UUID],
        to: str | None,
        origin: str | None = None,
    ) -> DocumentSend:
        """
        Args:
            origin: L'adresse IP de la demande, pour le plafond par origine (`None` : hors HTTP).

        Raises:
            InvalidInputError: Aucun document, trop de documents, ou un doublon.
            GeneratedCvNotFoundError: Un export inconnu ou d'une autre personne.
            DocumentFileMissingError: Un export sans fichier.
            RecipientInvalidError: Adresse mal formée.
            AccountEmailUnknownError: `to` absent et adresse du compte introuvable.
            SendQuotaExceededError: Un des trois quotas est atteint (`details.scope`).
        """
        unique = list(dict.fromkeys(generated_ids))
        if not unique or len(unique) > MAX_DOCUMENTS_PER_SEND:
            raise InvalidInputError(
                f"Entre 1 et {MAX_DOCUMENTS_PER_SEND} documents par envoi.",
                details={"reason": "documents_count_invalid", "field": "generated_ids"},
            )
        for generated_id in unique:
            found = await self._generated.get(generated_id)
            if found is None or found.user_id != user_id:
                raise GeneratedCvNotFoundError()
            if not (found.pdf_key or found.docx_key):
                raise DocumentFileMissingError(generated_id)
        account = await self._accounts.email_of(user_id)
        if to is None:
            if not account:
                raise AccountEmailUnknownError()
            to = account
        address = normalize_address(to)
        now = self._clock.now()
        own = account is not None and canonical_address(address) == canonical_address(account)
        send = DocumentSend(
            id=uuid4(),
            user_id=user_id,
            generated_ids=tuple(unique),
            to=address,
            status=SendStatus.QUEUED,
            created_at=now,
        )
        # Comptage et insertion dans la même transaction, sous un verrou par
        # compte : deux demandes simultanées ne passent pas toutes deux sous le plafond.
        async with (self._uow or NullUnitOfWork()).atomic():
            await self._sends.lock_account(user_id)
            await self._guard(user_id, address, now, own=own, origin=origin)
            await self._sends.add(send)
        try:
            await self._jobs.defer_send(send.id)
        except Exception as exc:
            # La file est en panne : l'envoi ne partira pas, autant le dire tout
            # de suite plutôt que de laisser un « en attente » éternel.
            logger.exception("documents.send_not_deferred", send_id=str(send.id))
            send = send.failed(f"file indisponible : {type(exc).__name__}")
            await self._sends.save(send)
        logger.info("documents.send_requested", send_id=str(send.id), documents=len(unique))
        return send

    async def _guard(
        self, user_id: UUID, address: str, now: datetime, *, own: bool, origin: str | None
    ) -> None:
        """Les plafonds ; un refus va au journal d'abus avec son détail, pas au client."""
        start, end = day_bounds(now)
        key = canonical_address(address)
        try:
            ensure_quota(await self._sends.count_between(user_id, start, end), now)
            if not own:
                # Une adresse tierce : les plafonds anti-relais s'appliquent.
                ensure_recipient_quota(
                    await self._sends.count_to_between(key, start, end, user_id=user_id),
                    await self._sends.count_to_between(key, start, end),
                    now,
                )
                # Une origine illisible partage un seau commun : jamais une exemption.
                retry_after = (
                    self._origins.hit(f"send|{origin or 'unknown'}") if self._origins else None
                )
                if retry_after is not None:
                    raise SendQuotaExceededError(retry_after=retry_after, scope="origin")
        except SendQuotaExceededError as exc:
            logger.warning(
                "documents.send_refused",
                user_id=str(user_id),
                scope=exc.scope,
                third_party=not own,
                origin=origin or "unknown",
            )
            raise


class GetSend:
    """L'état d'un envoi de la personne."""

    def __init__(self, sends: SendRepository) -> None:
        self._sends = sends

    async def execute(self, user_id: UUID, send_id: UUID) -> DocumentSend:
        found = await self._sends.get(send_id)
        if found is None or found.user_id != user_id:
            raise SendNotFoundError()
        return found


class DeliverDocuments:
    """Remet un envoi : lit les fichiers, compose, envoie, consigne (travail de file)."""

    def __init__(
        self,
        *,
        sends: SendRepository,
        generated: GeneratedCvRepository,
        storage: ObjectStorage,
        mailer: DocumentMailer,
        clock: Clock,
    ) -> None:
        self._sends, self._generated, self._storage = sends, generated, storage
        self._mailer, self._clock = mailer, clock

    async def execute(self, send_id: UUID) -> SendStatus | None:
        send = await self._sends.get(send_id)
        if send is None or send.status is not SendStatus.QUEUED:
            return None if send is None else send.status
        try:
            documents = await self._files(send)
            await self._mailer.send(
                to=send.to, sender_name=await self._sender_name(send), documents=documents
            )
        except (MailDeliveryError, ObjectNotFoundError, DocumentFileMissingError) as exc:
            done = send.failed(type(exc).__name__)
            await self._sends.save(done)
            logger.warning("documents.send_failed", send_id=str(send_id), error=type(exc).__name__)
            return done.status
        done = send.succeeded(self._clock.now())
        await self._sends.save(done)
        logger.info("documents.send_delivered", send_id=str(send_id), documents=len(documents))
        return done.status

    async def _files(self, send: DocumentSend) -> list[DocumentFile]:
        """Les pièces : le PDF quand il existe, sinon le DOCX, nommées comme au téléchargement."""
        out: list[DocumentFile] = []
        for generated_id in send.generated_ids:
            found = await self._generated.get(generated_id)
            if found is None or found.user_id != send.user_id:
                raise DocumentFileMissingError(generated_id)
            if found.pdf_key:
                key, extension, content_type = found.pdf_key, "pdf", PDF
            elif found.docx_key:
                key, extension, content_type = found.docx_key, "docx", DOCX
            else:
                raise DocumentFileMissingError(generated_id)
            base, _, _ = found.file_name(extension).rpartition(".")
            fallback = "Lettre de motivation" if found.kind is DocumentKind.LETTER else "CV"
            out.append(
                DocumentFile(
                    file_name=safe_attachment_name(base, extension, fallback),
                    content_type=content_type,
                    data=await self._storage.get(Bucket.DOCUMENTS, key),
                )
            )
        return out

    async def _sender_name(self, send: DocumentSend) -> str:
        """Le nom qui signe : celui du premier document qui en porte un de sûr."""
        for generated_id in send.generated_ids:
            found = await self._generated.get(generated_id)
            if found is not None and safe_display_name(found.full_name):
                return safe_display_name(found.full_name)
        return "Une personne"
