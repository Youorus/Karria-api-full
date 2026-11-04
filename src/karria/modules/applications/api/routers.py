"""Routes HTTP du module `applications` — `docs/contrat-api-candidatures.md`, en exécutable.

Toutes exigent la session : un dossier est *le sien*. Celles qui confient
le CV ou l'offre à un modèle (créer, relancer l'adaptation, ébaucher la
lettre, réécrire un paragraphe) passent par la porte du consentement à
l'IA (ADR-0025). Une route reçoit, délègue, projette.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from karria.modules.applications.api.dependencies import (
    CreateDossierDep,
    DecidePropositionDep,
    DeleteDossierDep,
    DraftLetterDep,
    GetDossierDep,
    ListDossiersDep,
    LogoUrlDep,
    MarkAppliedDep,
    RegenerateParagraphDep,
    RetryAdaptationDep,
    UnmarkAppliedDep,
    UpdateDossierCvDep,
    UpdateLetterDep,
)
from karria.modules.applications.api.schemas import (
    CreateDossierRequest,
    CvUpdateRequest,
    DecisionRequest,
    DossierCardRead,
    DossierListRead,
    DossierRead,
    LetterWriteRequest,
    RegenerateRequest,
)
from karria.modules.applications.domain import Dossier
from karria.shared.auth import AiConsentedPrincipalDep, CurrentPrincipalDep, require_authentication
from karria.shared.errors import ErrorCode, error_responses

router = APIRouter(
    prefix="/applications",
    tags=["Candidatures"],
    dependencies=[Depends(require_authentication)],
)

_MUTATION_ERRORS = (ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.RULE_VIOLATION)
_AI_ERRORS = (ErrorCode.FORBIDDEN, ErrorCode.DEPENDENCY_FAILURE)


def _present(dossier: Dossier, logo_url: LogoUrlDep, *, base_changed: bool = False) -> DossierRead:
    return DossierRead.present(dossier, base_changed=base_changed, logo_url=logo_url)


@router.post(
    "",
    summary="Adapter mon CV à cette offre : ouvrir un dossier de candidature",
    response_model=DossierRead,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.NOT_FOUND,
        ErrorCode.RULE_VIOLATION,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def create_dossier(
    body: CreateDossierRequest,
    response: Response,
    principal: AiConsentedPrincipalDep,
    create: CreateDossierDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """Copie le CV de base, fige l'offre, lance l'adaptation par Karria.

    `201` avec le dossier, adapté si le modèle a répondu en moins de dix
    secondes (`adaptation: faite`), sinon `en_cours` : relire `GET /{id}`
    jusqu'à `faite` ou `echouee`. Un dossier existe déjà pour cette offre :
    `200` avec l'existant, rien n'est relancé. `409 cv_missing` sans CV de
    base ; `404 offer_not_found` ; `403 template_locked` pour un modèle
    Premium non possédé ; `403 ai_consent_required` sans consentement.
    """
    result = await create.execute(principal.subject_id, body.offer_id, body.template_id)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return _present(result.dossier, logo_url)


@router.get(
    "",
    summary="Mes dossiers de candidature",
    response_model=DossierListRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def list_dossiers(
    principal: CurrentPrincipalDep,
    list_dossiers_: ListDossiersDep,
    logo_url: LogoUrlDep,
    applied: Annotated[
        bool, Query(description="Vrai : seulement les dossiers où j'ai candidaté.")
    ] = False,
) -> DossierListRead:
    """Du plus récemment modifié au plus ancien, sans le CV ni la lettre (cartes)."""
    dossiers = await list_dossiers_.execute(principal.subject_id, applied_only=applied)
    return DossierListRead(items=[DossierCardRead.present(d, logo_url) for d in dossiers])


@router.get(
    "/{dossier_id}",
    summary="Un dossier",
    response_model=DossierRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def read_dossier(
    dossier_id: UUID, principal: CurrentPrincipalDep, get: GetDossierDep, logo_url: LogoUrlDep
) -> DossierRead:
    """Le dossier entier, avec `base_changed` : le CV de base a bougé depuis la copie."""
    view = await get.execute(principal.subject_id, dossier_id)
    return _present(view.dossier, logo_url, base_changed=view.base_changed)


@router.delete(
    "/{dossier_id}",
    summary="Supprimer un dossier",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def delete_dossier(
    dossier_id: UUID, principal: CurrentPrincipalDep, delete: DeleteDossierDep
) -> None:
    """Le CV de base n'est pas touché. Les exports déjà faits restent chez `documents`."""
    await delete.execute(principal.subject_id, dossier_id)


@router.post(
    "/{dossier_id}/adapt",
    summary="Relancer l'adaptation après un échec",
    response_model=DossierRead,
    responses=error_responses(*_MUTATION_ERRORS, *_AI_ERRORS),
)
async def retry_adaptation(
    dossier_id: UUID,
    principal: AiConsentedPrincipalDep,
    retry: RetryAdaptationDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """Seulement si `adaptation` vaut `echouee` (`409 adaptation_not_failed` sinon).

    Les anciennes propositions sont oubliées ; le CV du dossier, tel qu'il
    est, sert de base. Même borne de dix secondes, puis file.
    """
    return _present(await retry.execute(principal.subject_id, dossier_id), logo_url)


@router.put(
    "/{dossier_id}/cv",
    summary="Retoucher le CV du dossier",
    response_model=DossierRead,
    responses=error_responses(*_MUTATION_ERRORS, ErrorCode.INVALID_INPUT),
)
async def update_cv(
    dossier_id: UUID,
    body: CvUpdateRequest,
    principal: CurrentPrincipalDep,
    update: UpdateDossierCvDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """Le CV entier, revalidé par la forme d'un CV ; la couverture est recalculée.

    `409 adaptation_in_progress` tant que Karria travaille ; `422 cv_invalid`
    avec `fields`. Les propositions gardent leur état : accepter ou revenir
    s'applique toujours à l'élément visé s'il existe encore.
    """
    return _present(await update.execute(principal.subject_id, dossier_id, body.cv), logo_url)


@router.post(
    "/{dossier_id}/propositions/{proposition_id}",
    summary="Accepter, refuser ou revenir sur une proposition",
    response_model=DossierRead,
    responses=error_responses(*_MUTATION_ERRORS),
)
async def decide_proposition(  # noqa: PLR0917 - FastAPI injecte par position
    dossier_id: UUID,
    proposition_id: UUID,
    body: DecisionRequest,
    principal: CurrentPrincipalDep,
    decide: DecidePropositionDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """`accepter` applique `after` ; `refuser` et `revenir` remettent `before`.

    `404 proposition_not_found` ; `409 proposition_path_invalid` si l'élément
    visé a été retiré du CV à la main.
    """
    dossier = await decide.execute(principal.subject_id, dossier_id, proposition_id, body.decision)
    return _present(dossier, logo_url)


@router.post(
    "/{dossier_id}/letter",
    summary="Ébaucher la lettre de motivation",
    response_model=DossierRead,
    responses=error_responses(*_MUTATION_ERRORS, *_AI_ERRORS),
)
async def draft_letter(
    dossier_id: UUID,
    principal: AiConsentedPrincipalDep,
    draft: DraftLetterDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """Karria écrit la première ébauche à partir de l'offre et du CV adapté.

    Remplace la lettre existante s'il y en a une. `409 adaptation_in_progress`
    tant que Karria adapte le CV ; `503 ai_unavailable` si le modèle ne répond pas.
    """
    return _present(await draft.execute(principal.subject_id, dossier_id), logo_url)


@router.put(
    "/{dossier_id}/letter",
    summary="Modifier la lettre à la main",
    response_model=DossierRead,
    responses=error_responses(*_MUTATION_ERRORS, ErrorCode.INVALID_INPUT),
)
async def update_letter(
    dossier_id: UUID,
    body: LetterWriteRequest,
    principal: CurrentPrincipalDep,
    update: UpdateLetterDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """La lettre entière, revalidée (1 à 6 paragraphes, 1 200 caractères chacun au plus)."""
    dossier = await update.execute(principal.subject_id, dossier_id, body.model_dump(mode="json"))
    return _present(dossier, logo_url)


@router.post(
    "/{dossier_id}/letter/paragraphs/{index}/regenerate",
    summary="Réécrire un paragraphe de la lettre",
    response_model=DossierRead,
    responses=error_responses(*_MUTATION_ERRORS, ErrorCode.INVALID_INPUT, *_AI_ERRORS),
)
async def regenerate_paragraph(  # noqa: PLR0917 - FastAPI injecte par position
    dossier_id: UUID,
    index: int,
    body: RegenerateRequest,
    principal: AiConsentedPrincipalDep,
    regenerate: RegenerateParagraphDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """Un seul paragraphe, sa sorte conservée, avec une consigne courte facultative.

    `404 letter_missing` sans lettre ; `404 paragraph_not_found` si l'index dépasse.
    """
    dossier = await regenerate.execute(principal.subject_id, dossier_id, index, body.instruction)
    return _present(dossier, logo_url)


@router.post(
    "/{dossier_id}/applied",
    summary="J'ai candidaté",
    response_model=DossierRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def mark_applied(
    dossier_id: UUID, principal: CurrentPrincipalDep, mark: MarkAppliedDep, logo_url: LogoUrlDep
) -> DossierRead:
    """Le dossier passe en `candidate` avec `applied_at` ; idempotent."""
    return _present(await mark.execute(principal.subject_id, dossier_id), logo_url)


@router.delete(
    "/{dossier_id}/applied",
    summary="Je n'ai finalement pas candidaté",
    response_model=DossierRead,
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def unmark_applied(
    dossier_id: UUID,
    principal: CurrentPrincipalDep,
    unmark: UnmarkAppliedDep,
    logo_url: LogoUrlDep,
) -> DossierRead:
    """Retour à `brouillon` ou `pret` ; idempotent."""
    return _present(await unmark.execute(principal.subject_id, dossier_id), logo_url)
