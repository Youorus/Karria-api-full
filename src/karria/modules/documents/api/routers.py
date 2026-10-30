"""Routes du module `documents` — `karria_app/docs/contrat-api-modeles.md`.

Toutes exigent la session, sauf :

- la **galerie** et le **détail** — `GET /templates/`, `GET /templates/{id}`,
  `GET /templates/{id}/reviews` — ouverts aux lecteurs anonymes sous quota
  par origine (ADR-0025) : un modèle de CV n'est pas un contenu de compte ;
- `/internal/templates/*`, qui n'accepte que le jeton d'administration
  (`DOCUMENTS__ADMIN_TOKEN`) : dépôt d'un PDF de modèle et revue des versions.

L'export (`POST /cv/me/generated-cvs`) exige en plus le consentement à l'IA :
le score ATS est **expliqué** par un modèle tiers.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

from karria.modules.documents.api.dependencies import (
    GuestPrincipalDep,
    TemplatesDep,
    get_acquire,
    get_delete_generated,
    get_export,
    get_get_generated,
    get_get_selection,
    get_get_template,
    get_list_generated,
    get_list_owned,
    get_list_templates,
    get_preview,
    get_record_view,
    get_select,
    get_submit,
    require_admin,
)
from karria.modules.documents.api.schemas import (
    AcquireRequest,
    ExportRequest,
    FitRead,
    GeneratedCvListRead,
    GeneratedCvRead,
    OwnershipRead,
    PreviewPage,
    PreviewRead,
    PreviewRequest,
    ReviewListRead,
    SelectionRead,
    SelectRequest,
    TemplateRead,
    VersionAdminRead,
)
from karria.modules.documents.application import (
    AcquireFreeTemplate,
    DeleteGeneratedCv,
    ExportCv,
    GetGeneratedCv,
    GetSelection,
    GetTemplate,
    ListGeneratedCvs,
    ListOwnedTemplates,
    ListTemplates,
    PreviewTemplate,
    RecordTemplateView,
    SelectTemplate,
    SubmitTemplate,
)
from karria.modules.documents.domain.errors import TemplateNotFoundError
from karria.shared.auth import (
    AiConsentedPrincipalDep,
    CurrentPrincipalDep,
    require_authentication,
)
from karria.shared.errors import ErrorCode, InvalidInputError, error_responses
from karria.shared.http.dependencies import ObjectStorageDep
from karria.shared.storage import Bucket

router = APIRouter()
# Pas de session au niveau du routeur : la galerie se lit sans compte, et
# chaque route qui écrit ou rend un CV la redemande explicitement.
templates_router = APIRouter(prefix="/templates", tags=["Modèles"])
user_router = APIRouter(
    prefix="/user-templates", tags=["Modèles"], dependencies=[Depends(require_authentication)]
)
exports_router = APIRouter(
    prefix="/cv/me/generated-cvs", tags=["Modèles"], dependencies=[Depends(require_authentication)]
)
internal_router = APIRouter(
    prefix="/internal/templates", tags=["Interne"], dependencies=[Depends(require_admin)]
)

#: Taille maximale d'un PDF de modèle déposé.
MAX_TEMPLATE_PDF = 20 * 1024 * 1024


# ---------------------------------------------------------------------- galerie


@templates_router.get(
    "/",
    summary="La galerie",
    response_model=list[TemplateRead],
    description=(
        "Les modèles publiés. Sans session aussi (ADR-0025), compté par origine : "
        "`429 anonymous_rate_limited` au-delà du quota."
    ),
    responses=error_responses(ErrorCode.RATE_LIMITED),
)
async def list_templates(
    _principal: GuestPrincipalDep,
    use_case: Annotated[ListTemplates, Depends(get_list_templates)],
    templates: TemplatesDep,
    storage: ObjectStorageDep,
    category: str | None = None,
    premium: bool | None = None,
) -> list[TemplateRead]:
    """Les modèles publiés (passés par la porte de qualité)."""
    out = []
    for template in await use_case.execute(category=category, premium=premium):
        version = (
            await templates.get_version(template.published_version_id)
            if template.published_version_id
            else None
        )
        out.append(await TemplateRead.present(template, version, storage))
    return out


@templates_router.get(
    "/{template_id}",
    summary="Un modèle",
    response_model=TemplateRead,
    description="Le modèle et ses pages. Sans session aussi, compté par origine.",
    responses=error_responses(ErrorCode.NOT_FOUND, ErrorCode.RATE_LIMITED),
)
async def get_template(
    template_id: UUID,
    _principal: GuestPrincipalDep,
    use_case: Annotated[GetTemplate, Depends(get_get_template)],
    storage: ObjectStorageDep,
) -> TemplateRead:
    """Le modèle et les pages de son carrousel."""
    template, version = await use_case.execute(template_id)
    return await TemplateRead.present(template, version, storage, detail=True)


@templates_router.post(
    "/{template_id}/view",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Compter une vue",
    dependencies=[Depends(require_authentication)],
)
async def view_template(
    template_id: UUID, use_case: Annotated[RecordTemplateView, Depends(get_record_view)]
) -> Response:
    await use_case.execute(template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@templates_router.get(
    "/{template_id}/reviews",
    summary="Les avis d'un modèle",
    response_model=ReviewListRead,
    description="Sans session aussi, compté par origine.",
    responses=error_responses(ErrorCode.NOT_FOUND, ErrorCode.RATE_LIMITED),
)
async def template_reviews(
    template_id: UUID,
    _principal: GuestPrincipalDep,
    use_case: Annotated[GetTemplate, Depends(get_get_template)],
) -> ReviewListRead:
    """Forme V1 ; la v2 ne recueille pas encore d'avis."""
    template, _ = await use_case.execute(template_id)
    return ReviewListRead(
        items=[], total=0, rating_avg=template.rating_avg, rating_count=template.rating_count
    )


@templates_router.post(
    "/{template_id}/preview",
    summary="Mon CV dans ce modèle",
    response_model=PreviewRead,
    responses=error_responses(
        ErrorCode.NOT_FOUND,
        ErrorCode.FORBIDDEN,
        ErrorCode.RULE_VIOLATION,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def preview_template(
    template_id: UUID,
    principal: CurrentPrincipalDep,
    use_case: Annotated[PreviewTemplate, Depends(get_preview)],
    storage: ObjectStorageDep,
    body: PreviewRequest | None = None,
) -> PreviewRead:
    """Rendu à la volée, mis en cache tant que ni le CV ni la version ne changent."""
    pages, fit = await use_case.execute(
        principal.subject_id, template_id, ats_safe=bool(body and body.ats_safe)
    )
    return PreviewRead(
        pages=[
            PreviewPage(
                signed_url=await storage.presigned_get_url(Bucket.TEMP, p["key"]),
                width=p["width"],
                height=p["height"],
            )
            for p in pages
        ],
        fit=FitRead(**fit.to_json()),
    )


# ---------------------------------------------------------------------- possession et choix


@user_router.get("/me", summary="Mes modèles", response_model=list[OwnershipRead])
async def owned_templates(
    principal: CurrentPrincipalDep, use_case: Annotated[ListOwnedTemplates, Depends(get_list_owned)]
) -> list[OwnershipRead]:
    return [OwnershipRead.present(o) for o in await use_case.execute(principal.subject_id)]


@user_router.post(
    "/me",
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un modèle gratuit",
    response_model=OwnershipRead,
    responses=error_responses(ErrorCode.NOT_FOUND, ErrorCode.INVALID_INPUT),
)
async def acquire_template(
    body: AcquireRequest,
    principal: CurrentPrincipalDep,
    use_case: Annotated[AcquireFreeTemplate, Depends(get_acquire)],
) -> OwnershipRead:
    return OwnershipRead.present(await use_case.execute(principal.subject_id, body.template_id))


@user_router.get("/me/selected", summary="Mon modèle choisi", response_model=SelectionRead | None)
async def selected_template(
    principal: CurrentPrincipalDep,
    use_case: Annotated[GetSelection, Depends(get_get_selection)],
    templates: TemplatesDep,
) -> SelectionRead | Response:
    selection = await use_case.execute(principal.subject_id)
    if selection is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return SelectionRead.present(selection, await templates.get(selection.template_id))


@user_router.put(
    "/me/selected",
    summary="Choisir mon modèle",
    response_model=SelectionRead,
    responses=error_responses(ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN),
)
async def select_template(
    body: SelectRequest,
    principal: CurrentPrincipalDep,
    use_case: Annotated[SelectTemplate, Depends(get_select)],
    templates: TemplatesDep,
) -> SelectionRead:
    selection = await use_case.execute(
        principal.subject_id, body.template_id, ats_safe=body.ats_safe
    )
    return SelectionRead.present(selection, await templates.get(selection.template_id))


# ---------------------------------------------------------------------- exports


@exports_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Exporter mon CV (PDF, DOCX) avec son score ATS",
    response_model=GeneratedCvRead,
    responses=error_responses(
        ErrorCode.NOT_FOUND,
        ErrorCode.FORBIDDEN,
        ErrorCode.RULE_VIOLATION,
        ErrorCode.INVALID_INPUT,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def export_cv(
    body: ExportRequest,
    principal: AiConsentedPrincipalDep,
    use_case: Annotated[ExportCv, Depends(get_export)],
    storage: ObjectStorageDep,
) -> GeneratedCvRead:
    """`403 ai_consent_required` sans consentement à l'IA : le score ATS est expliqué par un modèle."""
    generated = await use_case.execute(
        principal.subject_id,
        template_id=body.template_id,
        formats=body.formats,
        ats_safe=body.ats_safe,
        offer_id=body.offer_id,
    )
    return await GeneratedCvRead.present(generated, storage)


@exports_router.get("", summary="Mes CV exportés", response_model=GeneratedCvListRead)
async def list_exports(
    principal: CurrentPrincipalDep,
    use_case: Annotated[ListGeneratedCvs, Depends(get_list_generated)],
    storage: ObjectStorageDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> GeneratedCvListRead:
    items, total = await use_case.execute(principal.subject_id, limit=limit, offset=offset)
    return GeneratedCvListRead(
        items=[await GeneratedCvRead.present(g, storage) for g in items], total=total
    )


@exports_router.get(
    "/{generated_id}",
    summary="Un CV exporté",
    response_model=GeneratedCvRead,
    responses=error_responses(ErrorCode.NOT_FOUND),
)
async def read_export(
    generated_id: UUID,
    principal: CurrentPrincipalDep,
    use_case: Annotated[GetGeneratedCv, Depends(get_get_generated)],
    storage: ObjectStorageDep,
) -> GeneratedCvRead:
    return await GeneratedCvRead.present(
        await use_case.execute(principal.subject_id, generated_id), storage
    )


@exports_router.delete(
    "/{generated_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un export",
    responses=error_responses(ErrorCode.NOT_FOUND),
)
async def delete_export(
    generated_id: UUID,
    principal: CurrentPrincipalDep,
    use_case: Annotated[DeleteGeneratedCv, Depends(get_delete_generated)],
) -> Response:
    await use_case.execute(principal.subject_id, generated_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------- administration


@internal_router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Déposer le PDF d'un modèle (analyse en file)",
    response_model=VersionAdminRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT),
)
async def submit_template(
    *,
    use_case: Annotated[SubmitTemplate, Depends(get_submit)],
    templates: TemplatesDep,
    storage: ObjectStorageDep,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=2, max_length=80)],
    label: Annotated[str | None, Form(max_length=120)] = None,
    is_premium: Annotated[bool, Form()] = False,
) -> VersionAdminRead:
    """Nouvelle version d'un modèle (créé s'il n'existe pas) ; publiée seulement si la porte de qualité passe."""
    pdf = await file.read(MAX_TEMPLATE_PDF + 1)
    if len(pdf) > MAX_TEMPLATE_PDF:
        # Avant : un préfixe tronqué (donc corrompu) partait en ingestion.
        raise InvalidInputError(
            "Le PDF dépasse la taille admise.",
            details={
                "reason": "template_too_large",
                "field": "file",
                "max_bytes": MAX_TEMPLATE_PDF,
            },
        )
    version = await use_case.execute(pdf, name=name, label=label, is_premium=is_premium)
    template = await templates.get(version.template_id)
    if template is None:  # pragma: no cover - créé dans la même transaction
        raise TemplateNotFoundError()
    return await VersionAdminRead.present(template, version, storage)


@internal_router.get(
    "", summary="Tous les modèles et leurs versions (revue)", response_model=list[VersionAdminRead]
)
async def review_templates(
    templates: TemplatesDep, storage: ObjectStorageDep
) -> list[VersionAdminRead]:
    out: list[VersionAdminRead] = []
    for template in await templates.list_all():
        out.extend(
            [
                await VersionAdminRead.present(template, version, storage)
                for version in await templates.versions_of(template.id)
            ]
        )
    return out


router.include_router(templates_router)
router.include_router(user_router)
router.include_router(exports_router)
router.include_router(internal_router)
