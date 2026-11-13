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
- **Lettre** (`ExportLetter`) : PDF et DOCX depuis une **spécification de
  lettre** — la lettre appariée du modèle ou la lettre sobre (ADR-0036,
  `letter_spec_for`) —, export `kind: letter` qui dit le style servi, sans
  score ATS. L'ingestion d'une lettre appariée (`ingest_paired_letter`) suit
  la même chaîne et la même porte que le CV ; sous le seuil, la version
  entière reste en revue.
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
    PortraitGenerator,
    RenderedHtml,
    SendRepository,
    TemplateJobs,
    TemplateRepository,
    UserTemplateRepository,
)
from karria.modules.documents.domain.ats import analyse
from karria.modules.documents.domain.derive import MAX_SSIM_TO_SOURCE, derive_spec
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
from karria.modules.documents.domain.letter import (
    from_letter,
    letter_warnings,
    recolor_letter_spec,
)
from karria.modules.documents.domain.palette import (
    parse_hex_color,
    primary_color_of,
    recolor_spec,
    tinted_ink,
)
from karria.modules.documents.domain.plain_letter import (
    LETTER_STYLE_TEMPLATE,
    PLAIN_LETTER_SAMPLE,
    PLAIN_LETTER_VERSION,
    letter_style_served,
    plain_letter_spec,
)
from karria.modules.documents.domain.portrait import persona_of
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
from karria.shared.images import resize_to_png, single_ink, tint_ink
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


#: Les pictogrammes teintés, par (empreinte du PNG, couleur d'origine, couleur
#: choisie) : la mesure et la teinte se font une fois par processus, pas à
#: chaque frappe de l'atelier. Bornée.
ICON_TINT_CACHE_SIZE: Final = 512
_tinted_icons: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()


def tint_icons(
    assets: dict[str, bytes], spec: dict[str, Any], primary: str | None
) -> dict[str, bytes]:
    """Les pictogrammes (`icon_*.png`) à la couleur choisie, quand ils sont à celle du décor.

    Un pictogramme rastérisé depuis le PDF du modèle n'est pas dans la
    spécification : `recolor_spec` ne le voit pas. S'il est **monochrome**
    (une encre sur le fond, bords anticrénelés compris) et que cette encre est
    la couleur du décor ou l'une de ses teintes, il prend la couleur qu'elle
    prendrait (`tinted_ink`), alpha et bords gardés. Sinon il reste tel quel.
    `spec` est la spécification **d'origine** (celle du modèle, avant
    recoloration) ; sans couleur choisie, ou avec celle d'origine, rien ne change.
    """
    if primary is None:
        return assets
    origin = primary_color_of(spec)
    if origin is None:
        return assets
    chosen = parse_hex_color(primary)
    if chosen == origin:
        return assets
    out = dict(assets)
    for name, data in assets.items():
        if not name.startswith("icon_"):
            continue
        key = (hashlib.sha256(data).hexdigest(), origin, chosen)
        cached = _tinted_icons.get(key)
        if cached is None:
            ink = single_ink(data)
            new = tinted_ink(ink, origin, chosen) if ink else None
            cached = tint_ink(data, ink, new) if ink and new else data
            _tinted_icons[key] = cached
            while len(_tinted_icons) > ICON_TINT_CACHE_SIZE:
                _tinted_icons.popitem(last=False)
        _tinted_icons.move_to_end(key)
        out[name] = cached
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
        assets = await asyncio.to_thread(tint_icons, assets, version.spec or {}, primary_color)
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
        # Ce que la personne a finalisé dans l'atelier est ce qu'elle obtient :
        # aucun resserrement silencieux. Deux pages sont permises et propres
        # (`fit_script`) ; tenir sur une page se décide dans l'atelier.
        fit = rendered.fit
        docx = None
        if "docx" in wanted:
            docx = await self._engine.render_docx(spec, view, assets=assets, photo=photo, fit=fit)
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
        preview_keys: list[str] = []
        for n, (png, _width, _height) in enumerate(
            await self._engine.page_images(rendered.data), 1
        ):
            key = f"{prefix}/apercu-{n}.png"
            await self._storage.put(Bucket.DOCUMENTS, key, png, content_type="image/png")
            preview_keys.append(key)
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
            preview_keys=tuple(preview_keys),
            fit=fit,
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
        for key in (found.pdf_key, found.docx_key, *found.preview_keys):
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
            for key in (export.pdf_key, export.docx_key, *export.preview_keys):
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
        letter: bytes | None = None,
    ) -> TemplateVersion:
        """`letter` : le PDF de la lettre appariée, déposé avec le CV (ADR-0036) ; `None` sans lettre."""
        if not pdf.startswith(b"%PDF"):
            raise TemplateIngestionError("le fichier n'est pas un PDF")
        if letter is not None and not letter.startswith(b"%PDF"):
            raise TemplateIngestionError("la lettre n'est pas un PDF")
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
            letter_key = None
            if letter is not None:
                letter_key = f"{version_prefix(template.id, number)}/letter_source.pdf"
                await self._storage.put(Bucket.TEMPLATES, letter_key, letter, content_type=PDF)
            version = TemplateVersion(
                id=uuid4(),
                template_id=template.id,
                version=number,
                status=VersionStatus.PROCESSING,
                source_key=key,
                created_at=now,
                letter_source_key=letter_key,
            )
            await self._templates.save_version(version)
        if defer:
            await self._jobs.defer_ingestion(version.id)
        logger.info(
            "documents.template_submitted",
            template=slug,
            version=number,
            letter=letter is not None,
        )
        return version


def transcribed_version(versions: list[TemplateVersion]) -> TemplateVersion | None:
    """La dernière version transcrite d'un PDF (spécification et exemple, sans dérivation)."""
    candidates = [
        v
        for v in versions
        if v.spec and v.sample and not (v.spec.get("source") or {}).get("derivation")
    ]
    return max(candidates, key=lambda v: v.version, default=None)


class DeriveTemplateVersion:
    """Publie, comme nouvelle version d'un modèle, une création Karria dérivée de la version publiée.

    La dérivation (`derive_spec`) change la composition, les polices, la
    couleur, les titres et les ornements ; la version garde son identifiant
    de modèle (les dossiers et les choix des personnes ne cassent pas). La
    **porte est inversée** : on mesure la ressemblance du rendu dérivé au PDF
    source et l'on refuse au-delà de `MAX_SSIM_TO_SOURCE`, ou si une police
    de la source est reprise. La lettre appariée de la source n'est pas
    dérivée : la version sert la lettre sobre.
    """

    def __init__(
        self,
        templates: TemplateRepository,
        engine: DocumentEngine,
        storage: ObjectStorage,
        clock: Clock,
        portraits: PortraitGenerator | None = None,
    ) -> None:
        self._templates, self._engine, self._storage, self._clock = (
            templates,
            engine,
            storage,
            clock,
        )
        self._portraits = portraits

    async def execute(
        self, name: str, *, seed: int = 1, dry_run: bool = False
    ) -> tuple[TemplateVersion, dict[str, Any]]:
        """Rend la version dérivée (non enregistrée si `dry_run`) et son compte rendu.

        Raises:
            TemplateNotFoundError: Modèle inconnu ou sans version publiée.
            TemplateIngestionError: Version sans exemple, polices introuvables, ou
                dérivation trop proche de sa source.
        """
        template = await self._templates.get_by_name(slugify(name))
        if template is None or template.published_version_id is None:
            raise TemplateNotFoundError()
        # On dérive toujours de la **transcription** (la dernière version qui n'est
        # pas elle-même une dérivation), jamais d'une dérivation : sinon le miroir
        # s'annule et la distance se mesure à un rendu de Karria, pas à la source.
        source_version = transcribed_version(await self._templates.versions_of(template.id))
        if source_version is None:
            raise TemplateIngestionError("aucune version transcrite avec spécification et exemple")
        source_pdf = await self._storage.get(Bucket.TEMPLATES, source_version.source_key)
        spec, derivation = derive_spec(
            source_version.spec, seed=seed, source_sha256=hashlib.sha256(source_pdf).hexdigest()
        )
        if derivation.shares_a_font:
            raise TemplateIngestionError("la dérivation reprend une police de la source")
        served = await self._engine.ensure_fonts(spec)
        missing = sorted({str(f["family"]) for f in spec["fonts"] if f["family"] not in served})
        if missing:
            raise TemplateIngestionError(f"polices introuvables : {', '.join(missing)}")

        # Les ressources de la source (pictogrammes, photo d'exemple), teintées à la nouvelle couleur.
        assets: dict[str, bytes] = {}
        for asset_name, key in source_version.assets.items():
            assets[asset_name] = await self._storage.get(Bucket.TEMPLATES, key)
        icons = {k: v for k, v in assets.items() if not k.startswith("sample_")}
        icons = await asyncio.to_thread(tint_icons, icons, source_version.spec, derivation.primary)
        number = await self._templates.next_version_number(template.id)
        await replace_sample_portrait(
            self._portraits,
            spec=spec,
            sample=source_version.sample,
            assets=assets,
            template_name=template.name,
            version=number,
        )
        photo_name = (spec["header"].get("photo") or {}).get("sample_file")
        photo = assets.get(photo_name) if photo_name else None
        view = from_sample(spec, source_version.sample)
        rendered = await self._engine.render_pdf(spec, view, assets=icons, photo=photo)
        distance = await self._engine.measure(source_pdf, rendered.data)
        if distance.ssim_tolerant > MAX_SSIM_TO_SOURCE:
            raise TemplateIngestionError(
                f"dérivation trop proche de la source (ressemblance {distance.ssim_tolerant:.2f} > "
                f"{MAX_SSIM_TO_SOURCE:.2f})"
            )
        report = {
            "derivation": derivation.to_json(),
            "derived_from": {
                "version": source_version.version,
                "version_id": str(source_version.id),
            },
            "distance": {
                "ssim": distance.ssim,
                "ssim_tolerant": distance.ssim_tolerant,
                "max_ssim": MAX_SSIM_TO_SOURCE,
                **distance.to_json(),
            },
            "fit": rendered.fit.to_json(),
            "fonts": [
                {"source": f["family"], "served": f["family"], "origin": "karria"}
                for f in spec["fonts"]
            ],
            "gate": {"passed": True, "failures": []},
        }
        prefix = version_prefix(template.id, number)
        now = self._clock.now()
        version = TemplateVersion(
            id=uuid4(),
            template_id=template.id,
            version=number,
            status=VersionStatus.PUBLISHED,
            source_key=f"{prefix}/source.pdf",
            created_at=now,
            spec=spec,
            sample=source_version.sample,
            labels=source_version.labels,
            report=report,
            fonts=tuple(dict(f) for f in spec["fonts"]),
            published_at=now,
        )
        if dry_run:
            return version, report
        previews = await self._engine.previews(rendered.data)
        keys = {
            "thumbnail_key": f"{prefix}/preview/vignette.webp",
            "preview_key": f"{prefix}/preview/apercu.png",
            "full_key": f"{prefix}/preview/apercu_pleine_page.png",
        }
        await self._storage.put(
            Bucket.TEMPLATES, version.source_key, rendered.data, content_type=PDF
        )
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
        asset_keys: dict[str, str] = {}
        for asset_name, data in {**assets, **icons}.items():
            key = f"{prefix}/assets/{asset_name}"
            await self._storage.put(Bucket.TEMPLATES, key, data, content_type="image/png")
            asset_keys[asset_name] = key
        done = replace(
            version,
            assets=asset_keys,
            thumbnail_blurhash=previews.blurhash,
            thumbnail_key=keys["thumbnail_key"],
            preview_key=keys["preview_key"],
            full_key=keys["full_key"],
        )
        await self._templates.save_version(done)
        traits = gallery_traits(spec)
        tags = tuple(template.tags) + (
            ("création Karria",) if "création Karria" not in template.tags else ()
        )
        updated = replace(
            template,
            tags=tags,
            primary_color=traits["primary_color"],
            font_family=traits["font_family"],
            has_photo=traits["has_photo"],
            columns=traits["columns"],
            published_version_id=done.id,
            published_version=done.version,
            updated_at=now,
        )
        await self._templates.save(updated)
        logger.info(
            "documents.template_derived",
            template=template.name,
            version=number,
            seed=seed,
            ssim=distance.ssim_tolerant,
            fonts=[derivation.display_font, derivation.body_font],
            primary=derivation.primary,
        )
        return done, report


async def replace_sample_portrait(
    portraits: PortraitGenerator | None,
    *,
    spec: dict[str, Any],
    sample: dict[str, Any] | None,
    assets: dict[str, bytes],
    template_name: str,
    version: int,
) -> bool:
    """Remplace, dans `assets`, la photo d'exemple par un portrait généré ; vrai si fait.

    Rien ne change quand le modèle n'a pas de photo, sans générateur, ou
    quand la génération échoue (meilleur effort : la photo du PDF reste).
    """
    photo_name = (spec.get("header", {}).get("photo") or {}).get("sample_file")
    if not photo_name or portraits is None:
        return False
    persona = persona_of(spec, sample or {}, template_name=template_name, version=version)
    portrait = await portraits.generate(persona)
    if not portrait:
        return False
    assets[photo_name] = portrait
    return True


class RefreshSamplePortrait:
    """Donne à la version publiée d'un modèle son portrait d'exemple généré, et refait ses aperçus."""

    def __init__(
        self,
        templates: TemplateRepository,
        engine: DocumentEngine,
        storage: ObjectStorage,
        portraits: PortraitGenerator,
        clock: Clock,
    ) -> None:
        self._templates, self._engine, self._storage = templates, engine, storage
        self._portraits, self._clock = portraits, clock

    async def execute(self, name: str) -> TemplateVersion | None:
        """Rend la version mise à jour ; `None` si le modèle n'a pas de photo ou que rien n'a été généré.

        Raises:
            TemplateNotFoundError: Modèle inconnu ou sans version publiée.
        """
        template = await self._templates.get_by_name(slugify(name))
        if template is None or template.published_version_id is None:
            raise TemplateNotFoundError()
        version = await self._templates.get_version(template.published_version_id)
        if version is None or not version.spec or not version.sample:
            raise TemplateNotFoundError()
        spec = version.spec
        assets: dict[str, bytes] = {}
        for asset_name, key in version.assets.items():
            assets[asset_name] = await self._storage.get(Bucket.TEMPLATES, key)
        if not await replace_sample_portrait(
            self._portraits,
            spec=spec,
            sample=version.sample,
            assets=assets,
            template_name=template.name,
            version=version.version,
        ):
            return None
        photo_name = str(spec["header"]["photo"]["sample_file"])
        prefix = version_prefix(template.id, version.version)
        photo_key = f"{prefix}/assets/{photo_name}"
        await self._storage.put(
            Bucket.TEMPLATES, photo_key, assets[photo_name], content_type="image/png"
        )
        icons = {k: v for k, v in assets.items() if not k.startswith("sample_")}
        view = from_sample(spec, version.sample)
        rendered = await self._engine.render_pdf(spec, view, assets=icons, photo=assets[photo_name])
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
        done = replace(
            version,
            assets={**version.assets, photo_name: photo_key},
            thumbnail_blurhash=previews.blurhash,
            thumbnail_key=keys["thumbnail_key"],
            preview_key=keys["preview_key"],
            full_key=keys["full_key"],
            report={**version.report, "portrait": {"generated_at": self._clock.now().isoformat()}},
        )
        await self._templates.save_version(done)
        logger.info(
            "documents.sample_portrait_refreshed", template=template.name, version=version.version
        )
        return done


class IngestTemplateVersion:
    """Analyse une version, la mesure, et la publie si elle passe la porte de qualité."""

    def __init__(
        self,
        templates: TemplateRepository,
        engine: DocumentEngine,
        storage: ObjectStorage,
        clock: Clock,
        portraits: PortraitGenerator | None = None,
    ) -> None:
        """`portraits` : le générateur du portrait d'exemple ; sans lui, la photo du PDF reste."""
        self._templates, self._engine, self._storage, self._clock = (
            templates,
            engine,
            storage,
            clock,
        )
        self._portraits = portraits

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
        # Le portrait d'exemple : une personne fictive à Karria, accordée au modèle,
        # à la place de la photo de banque d'images du fournisseur.
        await replace_sample_portrait(
            self._portraits,
            spec=spec,
            sample=sample,
            assets=analysis.assets,
            template_name=template.name,
            version=version.version,
        )
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
        # Une substitution de police déclarée se juge dans la revue, pas dans la porte
        # (plancher SSIM dédié) ; la police servie se juge sur le rapport (ADR-0036).
        substituted = substituted_families(analysis.fonts_report)
        fidelity_pdf = await self._engine.measure(
            source, rendered.data, substituted=substituted, fonts_report=analysis.fonts_report
        )
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
        fidelity_docx = await self._engine.measure(
            source, docx_pdf, substituted=substituted, fonts_report=analysis.fonts_report
        )
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
        # La lettre appariée (ADR-0036) : même chaîne, même porte, dans la même version.
        letter = (
            await ingest_paired_letter(
                self._engine, self._storage, version, spec, prefix, timings, self._clock
            )
            if version.letter_source_key
            else None
        )
        if letter is not None:
            failures.extend(letter.failures)
        passed = not failures
        report = {
            "fidelity": {
                "pdf": fidelity_pdf.to_json(),
                "docx": {**fidelity_docx.to_json(), "target": DOCX_SCORE_TARGET},
                **(letter.fidelity if letter else {}),
            },
            "gate": {"passed": passed, "failures": failures},
            "fonts": analysis.fonts_report + (letter.fonts_report if letter else []),
            "substitutions": [
                f
                for f in analysis.fonts_report + (letter.fonts_report if letter else [])
                if f.get("origin") == "substitute"
            ],
            "unlabeled": analysis.unlabeled,
            "fit": rendered.fit.to_json(),
            "timings": timings,
            "passes": analysis.passes,
            **(letter.report if letter else {}),
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
            fonts=tuple(analysis.fonts) + (letter.fonts if letter else ()),
            published_at=now if passed else None,
            thumbnail_blurhash=previews.blurhash,
            error=None,
            **keys,
            **(letter.version_fields if letter else {}),
        )
        await self._templates.save_version(done)
        traits = gallery_traits(spec)
        meta = spec.get("meta") or {}
        # Le nom de galerie est le travail du modèle de vision (ADR-0037 § 1) :
        # il remplace le libellé par défaut (le nom technique mis en capitales),
        # jamais un libellé que l'administration a posé.
        default_label = template.name.strip().title()
        planned_label = str(meta.get("title") or "").strip()
        updated = replace(
            template,
            label=planned_label
            if template.label == default_label and planned_label
            else template.label,
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
            fidelity_letter=letter.score if letter else None,
            substitutions=sum(1 for f in analysis.fonts_report if f.get("origin") == "substitute"),
            failures=len(failures),
        )
        return done


class RefreshTemplateColors:
    """Remesure la couleur de galerie des modèles publiés depuis leur version publiée.

    La définition de `primary_color` a changé (ADR-0034 § 2026-11-06 : la
    couleur du décor, plus celle des titres) ; les modèles déjà ingérés
    portent l'ancienne mesure. Commande `scripts/templates.py refresh-colors`,
    idempotente : un modèle déjà à jour n'est pas réécrit.
    """

    def __init__(self, templates: TemplateRepository, clock: Clock) -> None:
        self._templates, self._clock = templates, clock

    async def execute(self, *, apply: bool = True) -> list[tuple[Template, str | None]]:
        """Les modèles dont la couleur change, avec la nouvelle ; écrits si `apply`."""
        changes: list[tuple[Template, str | None]] = []
        for template in await self._templates.list_all():
            if template.published_version_id is None:
                continue
            version = await self._templates.get_version(template.published_version_id)
            if version is None or version.spec is None:
                continue
            color = gallery_traits(version.spec)["primary_color"]
            if color == template.primary_color:
                continue
            changes.append((template, color))
            if apply:
                await self._templates.save(
                    replace(template, primary_color=color, updated_at=self._clock.now())
                )
        return changes


# ---------------------------------------------------------------------- atelier (ADR-0029)


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """Ce que l'atelier affiche : le HTML ancré, les pages estimées, les avertissements.

    `letter_style` : pour une lettre, le style **réellement servi** (`template`
    ou `plain`, ADR-0036) ; `None` pour un CV ou l'exemple.
    """

    html: str
    pages: int
    warnings: tuple[str, ...]
    letter_style: str | None = None


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
        letter_style: str | None = None,
    ) -> RenderedDocument:
        """
        `primary_color` (`#RRGGBB`) : la couleur principale choisie, appliquée
        après la variante ATS, à l'exemple comme au CV ou à la lettre (ADR-0034).
        `letter_style` (`template` | `plain` | `None`) : la lettre du modèle ou la
        lettre sobre (ADR-0036) ; la réponse dit le style servi.

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
        served: str | None = None
        if sample:
            kind = "sample"
            rendered, warnings = await self._sample(spec, version, primary_color)
        else:
            if template.is_premium and not await self._owned.owns(user_id, template_id):
                raise TemplateNotOwnedError()
            if letter is not None:
                kind = "letter"
                letter_spec, served = letter_spec_for(
                    version, letter_style=letter_style, primary_color=primary_color
                )
                rendered, warnings = await self._letter(letter_spec, letter)
            elif cv is not None:
                kind = "cv"
                rendered, warnings = await self._cv(spec, version, user_id, cv, primary_color)
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
            letter_style=served,
            pages=rendered.pages,
            bytes=size,
            ms=round((time.monotonic() - started) * 1000),
        )
        return RenderedDocument(
            html=rendered.html,
            pages=rendered.pages,
            warnings=tuple(warnings),
            letter_style=served,
        )

    async def _sample(
        self, spec: dict[str, Any], version: TemplateVersion, primary_color: str | None
    ) -> tuple[RenderedHtml, list[str]]:
        """L'exemple du modèle, sans ancres, photo d'exemple comprise."""
        view = from_sample(spec, version.sample or {})
        assets = await _assets(self._storage, version, sample=True)
        assets = await asyncio.to_thread(tint_icons, assets, version.spec or {}, primary_color)
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
        """Une lettre, ancrée, depuis sa spécification de lettre, avec le logo s'il est joignable."""
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
        self,
        spec: dict[str, Any],
        version: TemplateVersion,
        user_id: UUID,
        cv: dict[str, Any],
        primary_color: str | None,
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
        assets = await asyncio.to_thread(tint_icons, assets, version.spec or {}, primary_color)
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
        letter_style: str | None = None,
    ) -> GeneratedCv:
        """`primary_color` : la couleur principale du dossier, pour que la lettre suive le CV.

        `letter_style` : `template` (la lettre appariée du modèle, s'il en a
        une) ou `plain` (la lettre sobre) ; l'export garde le style servi.
        """
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
        spec, served = letter_spec_for(
            version, letter_style=letter_style, primary_color=primary_color
        )
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
            letter_style=served,
        )
        await self._generated.add(generated)
        await self._templates.increment_uses(template.id)
        logger.info(
            "documents.letter_exported",
            template=template.name,
            letter_style=served,
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


# ---------------------------------------------------------------------- lettre appariée et lettre sobre (ADR-0036)


def letter_spec_for(
    version: TemplateVersion, *, letter_style: str | None, primary_color: str | None
) -> tuple[dict[str, Any], str]:
    """La spécification de lettre à rendre, et le style **réellement servi**.

    `template` → la lettre appariée de la version, à la couleur choisie pour le
    CV (`recolor_letter_spec` : son décor, et les textes qui portaient la couleur
    du décor) ; sans lettre appariée, ou `plain` → la lettre sobre, qui ne porte
    aucune couleur de modèle. Sans choix : `template` si le modèle a une lettre,
    sinon `plain`.
    """
    served = letter_style_served(letter_style, has_template_letter=version.has_letter)
    if served == LETTER_STYLE_TEMPLATE and version.letter_spec is not None:
        return recolor_letter_spec(version.letter_spec, primary_color), served
    return plain_letter_spec(), served


@dataclass(frozen=True, slots=True)
class PairedLetterResult:
    """Ce que l'ingestion d'une lettre appariée rapporte à la version."""

    failures: tuple[str, ...]
    fidelity: dict[str, Any]
    report: dict[str, Any]
    fonts_report: list[dict[str, Any]]
    fonts: tuple[dict[str, Any], ...]
    version_fields: dict[str, Any]
    score: float


async def ingest_paired_letter(  # noqa: PLR0917 - la chaîne d'ingestion, étape par étape
    engine: DocumentEngine,
    storage: ObjectStorage,
    version: TemplateVersion,
    cv_spec: dict[str, Any],
    prefix: str,
    timings: dict[str, float],
    clock: Clock,
) -> PairedLetterResult:
    """La lettre appariée : analyse, rendu de l'exemple, mesure, porte, aperçus (ADR-0036).

    Même chaîne que le CV, même mesure (`fidelity.py`), même porte
    (`gate.evaluate`) : ses échecs s'ajoutent à ceux du CV, et la version
    entière reste en revue sous le seuil. La spécification garde la couleur du
    décor du CV (`accent`, `primary_color_of`) et marque les textes qui la
    portent : `recolor_letter_spec` recolore ainsi les deux documents d'un
    dossier ensemble (ADR-0034 § 2026-11-06).

    Raises:
        TemplateIngestionError: PDF illisible, lettre sans paragraphe, étiquetage indisponible.
    """
    assert version.letter_source_key is not None  # noqa: S101 - garde d'appel
    t = time.monotonic()
    source = await storage.get(Bucket.TEMPLATES, version.letter_source_key)
    analysis = await engine.analyze_letter(source, accent=primary_color_of(cv_spec))
    spec, sample = analysis.spec, analysis.sample
    view = from_letter(spec, sample, today=clock.now().date())
    rendered = await engine.render_pdf(spec, view, assets={}, photo=None)
    substituted = substituted_families(analysis.fonts_report)
    fidelity = await engine.measure(
        source, rendered.data, fonts_report=analysis.fonts_report, substituted=substituted
    )
    decision = evaluate(fidelity, letter=True)
    failures = [f"lettre : {f}" for f in decision.failures]
    if analysis.unlabeled:
        failures.append(f"lettre : lignes sans rôle : {', '.join(analysis.unlabeled)}")
    docx = await engine.render_docx(spec, view, assets={}, photo=None, fit=rendered.fit)
    docx_pdf = await engine.docx_to_pdf(docx)
    fidelity_docx = await engine.measure(
        source, docx_pdf, fonts_report=analysis.fonts_report, substituted=substituted
    )
    previews = await engine.previews(rendered.data)
    keys = {
        "letter_thumbnail_key": f"{prefix}/letter/vignette.webp",
        "letter_preview_key": f"{prefix}/letter/apercu.png",
        "letter_full_key": f"{prefix}/letter/apercu_pleine_page.png",
    }
    await storage.put(
        Bucket.TEMPLATES,
        keys["letter_thumbnail_key"],
        previews.thumbnail_webp,
        content_type="image/webp",
    )
    await storage.put(
        Bucket.TEMPLATES, keys["letter_preview_key"], previews.preview_png, content_type="image/png"
    )
    await storage.put(
        Bucket.TEMPLATES, keys["letter_full_key"], previews.full_png, content_type="image/png"
    )
    await storage.put(
        Bucket.TEMPLATES, f"{prefix}/letter/sample_render.pdf", rendered.data, content_type=PDF
    )
    await storage.put(
        Bucket.TEMPLATES, f"{prefix}/letter/sample_docx.pdf", docx_pdf, content_type=PDF
    )
    timings["letter_s"] = round(time.monotonic() - t, 1)
    logger.info(
        "documents.letter_ingested",
        version_id=str(version.id),
        fidelity=fidelity.score,
        fidelity_docx=fidelity_docx.score,
        failures=len(failures),
    )
    return PairedLetterResult(
        failures=tuple(failures),
        fidelity={
            "letter": fidelity.to_json(),
            "letter_docx": {**fidelity_docx.to_json(), "target": DOCX_SCORE_TARGET},
        },
        report={
            "letter_gate": decision.to_json(),
            "fonts_letter": analysis.fonts_report,
            "unlabeled_letter": analysis.unlabeled,
            "letter_fit": rendered.fit.to_json(),
        },
        fonts_report=analysis.fonts_report,
        fonts=tuple(f for f in analysis.fonts if f not in version.fonts),
        version_fields={
            "letter_spec": spec,
            "letter_sample": sample,
            "letter_labels": analysis.labels,
            "letter_thumbnail_blurhash": previews.blurhash,
            **keys,
        },
        score=fidelity.score,
    )


#: Où vivent les aperçus de la lettre sobre (bucket `templates`), par version du dessin.
PLAIN_LETTER_PREFIX: Final = f"plain-letter/v{PLAIN_LETTER_VERSION}"
#: Un seul contrôle d'existence par processus et par dix minutes : la galerie
#: ne doit pas interroger le stockage à chaque lecture.
PLAIN_LETTER_CHECK_SECONDS: Final = 600.0
_plain_letter_cache: dict[str, Any] = {"until": 0.0, "pages": None}


def plain_letter_keys() -> dict[str, str]:
    """Les clés des aperçus de la lettre sobre : vignette, aperçu, pleine page, fiche."""
    return {
        "thumbnail_key": f"{PLAIN_LETTER_PREFIX}/vignette.webp",
        "preview_key": f"{PLAIN_LETTER_PREFIX}/apercu.png",
        "full_key": f"{PLAIN_LETTER_PREFIX}/apercu_pleine_page.png",
        "meta_key": f"{PLAIN_LETTER_PREFIX}/meta.json",
    }


async def plain_letter_pages(storage: ObjectStorage) -> dict[str, Any] | None:
    """Les aperçus de la lettre sobre pour la galerie, ou `None` tant que la commande n'a pas tourné.

    Lu une fois par processus (puis toutes les dix minutes) : `meta.json`,
    déposé en dernier par `PublishPlainLetterPreview`, atteste que les trois
    images sont là et porte le BlurHash.
    """
    now = time.monotonic()
    if _plain_letter_cache["until"] > now:
        pages: dict[str, Any] | None = _plain_letter_cache["pages"]
        return pages
    keys = plain_letter_keys()
    found: dict[str, Any] | None
    try:
        meta = json.loads(await storage.get(Bucket.TEMPLATES, keys["meta_key"]))
        found = {**keys, "blurhash": meta.get("blurhash")}
    except ObjectNotFoundError:
        found = None
    _plain_letter_cache["pages"] = found
    _plain_letter_cache["until"] = now + PLAIN_LETTER_CHECK_SECONDS
    return found


class PublishPlainLetterPreview:
    """Rend la lettre sobre avec son contenu d'exemple et dépose ses aperçus (commande `plain-letter`).

    Jamais au démarrage (AGENTS § 6.4) : une commande explicite, à relancer quand
    `PLAIN_LETTER_VERSION` change. Les polices doivent avoir été téléchargées
    avant (la commande s'en charge) : un rendu ne va jamais sur Internet.
    """

    def __init__(self, engine: DocumentEngine, storage: ObjectStorage, clock: Clock) -> None:
        self._engine, self._storage, self._clock = engine, storage, clock

    async def execute(self) -> dict[str, Any]:
        spec = plain_letter_spec()
        view = from_letter(spec, PLAIN_LETTER_SAMPLE, today=self._clock.now().date())
        rendered = await self._engine.render_pdf(spec, view, assets={}, photo=None)
        previews = await self._engine.previews(rendered.data)
        keys = plain_letter_keys()
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
            Bucket.TEMPLATES,
            f"{PLAIN_LETTER_PREFIX}/sample_render.pdf",
            rendered.data,
            content_type=PDF,
        )
        meta = {
            "version": PLAIN_LETTER_VERSION,
            "blurhash": previews.blurhash,
            "fit": rendered.fit.to_json(),
            "published_at": self._clock.now().isoformat(),
        }
        await self._storage.put(
            Bucket.TEMPLATES,
            keys["meta_key"],
            json.dumps(meta).encode("utf-8"),
            content_type="application/json",
        )
        _plain_letter_cache["until"] = 0.0
        logger.info(
            "documents.plain_letter_published",
            version=PLAIN_LETTER_VERSION,
            pages=rendered.fit.pages,
        )
        return meta
