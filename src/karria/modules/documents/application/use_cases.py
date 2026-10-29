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
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from karria.modules.documents.application.ports import (
    AtsExplainer,
    CvSource,
    DocumentEngine,
    GeneratedCvRepository,
    TemplateJobs,
    TemplateRepository,
    UserTemplateRepository,
)
from karria.modules.documents.domain.ats import analyse
from karria.modules.documents.domain.errors import (
    CvMissingError,
    GeneratedCvNotFoundError,
    RenderingUnavailableError,
    TailoringNotAvailableError,
    TemplateIngestionError,
    TemplateNotFoundError,
    TemplateNotFreeError,
)
from karria.modules.documents.domain.gate import DOCX_SCORE_TARGET, evaluate
from karria.modules.documents.domain.headline import ats_safe_spec
from karria.modules.documents.domain.templates import (
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
from karria.shared.database import UnitOfWork
from karria.shared.logging import get_logger
from karria.shared.storage import Bucket, ObjectNotFoundError, ObjectStorage

logger = get_logger(__name__)

#: Entre dans la clé des aperçus en cache : à incrémenter quand le rendu change
#: (sinon un déploiement sert d'anciens aperçux tant que le CV ne bouge pas).
PREVIEW_CACHE_VERSION = 2

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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

    async def execute(self, user_id: UUID, template_id: UUID, *, ats_safe: bool) -> Selection:
        template, _ = await _published(self._templates, template_id)
        ensure_usable(template, owned=await self._owned.owns(user_id, template_id))
        selection = Selection(
            user_id=user_id,
            template_id=template_id,
            ats_safe=ats_safe,
            updated_at=self._clock.now(),
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
        self, user_id: UUID, template_id: UUID, *, ats_safe: bool
    ) -> tuple[
        Template,
        TemplateVersion,
        dict[str, Any],
        dict[str, Any],
        Any,
        bytes | None,
        dict[str, bytes],
    ]:
        template, version = await _published(self._templates, template_id)
        ensure_usable(template, owned=await self._owned.owns(user_id, template_id))
        snapshot = await self._cvs.snapshot(user_id)
        if snapshot is None:
            raise CvMissingError()
        spec = version.spec or {}
        if ats_safe:
            spec = ats_safe_spec(spec)
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
    ) -> GeneratedCv:
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
        safe = bool(ats_safe)
        started = time.monotonic()
        template, version, spec, view, snapshot, photo, assets = await self._renderer.prepare(
            user_id, template_id, ats_safe=safe
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
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[GeneratedCv], int]:
        return await self._generated.history(user_id, limit=limit, offset=offset)


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
    ) -> None:
        self._generated, self._owned, self._storage = generated, owned, storage

    async def execute(self, user_id: UUID) -> int:
        exports = await self._generated.of_user(user_id)
        for export in exports:
            for key in (export.pdf_key, export.docx_key):
                if key:
                    await self._storage.delete(Bucket.DOCUMENTS, key)
        await self._generated.forget(user_id)
        await self._owned.forget(user_id)
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
        fidelity_pdf = await self._engine.measure(source, rendered.data)
        timings["measure_s"] = round(time.monotonic() - t, 1)
        decision = evaluate(fidelity_pdf)
        failures = list(decision.failures)
        if analysis.unlabeled:
            failures.append(f"lignes sans rôle : {', '.join(analysis.unlabeled)}")
        t = time.monotonic()
        docx = await self._engine.render_docx(
            spec, view, assets=icons, photo=photo, fit=rendered.fit
        )
        docx_pdf = await self._engine.docx_to_pdf(docx)
        fidelity_docx = await self._engine.measure(source, docx_pdf)
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
