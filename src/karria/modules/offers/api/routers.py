"""Routes du module `offers` : le fil (`/job-matches`), Karria (`/assistant`), la recherche (`/search`).

Toutes exigent la session, sauf :

- la **lecture du fil** — `GET /job-matches/me`, `/me/filter-options`,
  `/{offer_id}` — ouverte aux lecteurs **anonymes** (ADR-0025) sous quota
  par origine : tout le corpus, les plus récentes d'abord, sans état ni
  critères ni journal, et jamais les filtres personnels (`saved_only`…).
  Les mêmes gestionnaires répondent aussi sous `/offers/feed`,
  `/offers/filter-options`, `/offers/{offer_id}` — un nom qui ne dit plus
  « me » ; `/job-matches/me` reste servi tel quel pour l'app installée ;
- `/internal/offers/*` : celles-là n'acceptent que le jeton de service du
  collecteur (`karria.collector`).

Karria (`POST /assistant/messages`) exige en plus le consentement à l'IA :
le message part chez un modèle tiers. Le contrat en prose fait foi :
`karria_app/docs/contrat-api-recherche.md`, `contrat-api-offres.md`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from karria.modules.offers.api.dependencies import (
    ApplyCriteriaDep,
    ClearCriteriaDep,
    ClockDep,
    ConverseSearchDep,
    GetCriteriaDep,
    GetFeedDep,
    GetFeedFiltersDep,
    GetMemoryDep,
    GetOfferDep,
    GuestPrincipalDep,
    LeaseTargetsDep,
    LogoUrlDep,
    MarkSeenDep,
    ReceiveBatchDep,
    ResetMemoryDep,
    SetAlertsEnabledDep,
    UpdateStateDep,
    require_collector,
)
from karria.modules.offers.api.schemas import (
    AlertsRequest,
    AlertsResponse,
    AssistantReplyResponse,
    BatchAcceptedResponse,
    CriteriaAppliedResponse,
    CriteriaEnvelopeResponse,
    CriteriaRequest,
    CriteriaResponse,
    FeedPageResponse,
    FilterOptionsResponse,
    InteractionRequest,
    JobMatchResponse,
    MemoryResponse,
    MessageRequest,
    SeenRequest,
    SeenResponse,
    TargetResponse,
    TargetsResponse,
    parse_csv,
)
from karria.modules.offers.application import FeedRequest, StateChanges
from karria.modules.offers.domain import (
    BATCH_MAX_BYTES,
    HANDOFF_VERSION,
    BatchRejectedError,
    CriteriaNotFoundError,
    FeedMode,
)
from karria.shared.auth import AiConsentedPrincipalDep, CurrentPrincipalDep, Principal
from karria.shared.errors import (
    AuthenticationError,
    ErrorCode,
    InvalidInputError,
    error_responses,
)

matches_router = APIRouter(prefix="/job-matches", tags=["Fil d'offres"])
#: Le même fil sous un nom public : `/offers/feed`, `/offers/filter-options`, `/offers/{id}`.
offers_router = APIRouter(prefix="/offers", tags=["Fil d'offres"])
assistant_router = APIRouter(prefix="/assistant", tags=["Karria"])
search_router = APIRouter(prefix="/search", tags=["Recherche"])
internal_router = APIRouter(
    prefix="/internal/offers",
    tags=["Interne — collecteur"],
    dependencies=[Depends(require_collector)],
)
TARGETS_MAX_LIMIT = 100

FEED_MAX_LIMIT = 50

#: La raison fine d'un filtre personnel demandé sans compte.
ACCOUNT_REQUIRED = "account_required"


def _require_account_for(principal: Principal | None, *flags: tuple[str, bool]) -> None:
    """Un filtre qui parle de la personne (`saved_only`…) n'a pas de sens sans compte."""
    if principal is not None:
        return
    asked = [name for name, value in flags if value]
    if asked:
        raise AuthenticationError(
            "Connecte-toi pour retrouver tes offres.",
            details={"reason": ACCOUNT_REQUIRED, "field": asked[0]},
        )


@offers_router.get(
    "/feed",
    operation_id="offers_public_feed",
    response_model=FeedPageResponse,
    summary="Le fil (nom public)",
    description="Le même fil que `GET /job-matches/me`, sous un nom qui vaut aussi sans compte.",
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.RATE_LIMITED
    ),
)
@matches_router.get(
    "/me",
    response_model=FeedPageResponse,
    summary="Le fil",
    description=(
        "Sans recherche (`mode: all_offers`) : toutes les offres actives, les plus récentes "
        "d'abord. Avec une recherche (`mode: search`) : les offres retenues pour la personne, "
        "par pertinence (`sort=relevance`, défaut) ou par date (`sort=fresh`). Les offres "
        "écartées n'apparaissent jamais.\n\n"
        "`saved_only` : les sauvegardées, quelle que soit la recherche. `unseen_only` : jamais "
        "affichées. `unapplied_only` : sans candidature. `contract_types` et `cities` : les "
        "`value` de `GET /job-matches/me/filter-options`, séparées par des virgules. "
        "`published_after` : instant ISO 8601.\n\n"
        "`mode` (facultatif) : `all_offers` montre toutes les offres actives **sans toucher "
        "aux critères** — la recherche reste enregistrée et ses alertes actives ; `search` "
        "montre le fil de la recherche (sans critères, retombe sur `all_offers`). Absent : "
        "déduit des critères. La réponse rend toujours le `mode` réellement servi, et "
        "`criteria_summary` dès que des critères existent.\n\n"
        "**Sans session** (ADR-0025) : la même page, en `all_offers`, sans état ni critères ; "
        "`saved_only`, `unseen_only`, `unapplied_only` répondent `401 account_required`. "
        "Compté par origine : `429 anonymous_rate_limited` (`retry_after`) au-delà du quota."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.RATE_LIMITED
    ),
)
async def get_feed(
    *,
    principal: GuestPrincipalDep,
    feed: GetFeedDep,
    clock: ClockDep,
    logo_url: LogoUrlDep,
    limit: Annotated[int, Query(ge=1, le=FEED_MAX_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Literal["relevance", "fresh"] = "relevance",
    saved_only: bool = False,
    unseen_only: bool = False,
    unapplied_only: bool = False,
    contract_types: Annotated[str | None, Query(max_length=200)] = None,
    cities: Annotated[str | None, Query(max_length=1_000)] = None,
    published_after: datetime | None = None,
    mode: FeedMode | None = None,
) -> FeedPageResponse:
    """Une page du fil."""
    _require_account_for(
        principal,
        ("saved_only", saved_only),
        ("unseen_only", unseen_only),
        ("unapplied_only", unapplied_only),
    )
    page = await feed.execute(
        FeedRequest(
            user_id=principal.subject_id if principal else None,
            limit=limit,
            offset=offset,
            sort="recent" if sort == "fresh" else "relevance",
            saved_only=saved_only,
            unseen_only=unseen_only,
            unapplied_only=unapplied_only,
            contract_types=parse_csv(contract_types),
            cities=parse_csv(cities),
            published_after=published_after,
            mode=mode,
        )
    )
    return FeedPageResponse.from_page(
        page, limit=limit, offset=offset, now=clock.now(), logo_url=logo_url
    )


@offers_router.get(
    "/filter-options",
    operation_id="offers_public_filter_options",
    response_model=FilterOptionsResponse,
    summary="Les valeurs de filtre (nom public)",
    description="Les mêmes options que `GET /job-matches/me/filter-options`, sans compte aussi.",
    responses=error_responses(ErrorCode.INVALID_INPUT, ErrorCode.RATE_LIMITED),
)
@matches_router.get(
    "/me/filter-options",
    response_model=FilterOptionsResponse,
    summary="Les valeurs de filtre du fil",
    description=(
        "Contrats et villes présents dans le fil de la personne (dans tout le corpus actif "
        "sans recherche, ou avec `mode=all_offers`), avec leurs effectifs. Sans session : "
        "ceux de tout le corpus actif."
    ),
    responses=error_responses(ErrorCode.INVALID_INPUT, ErrorCode.RATE_LIMITED),
)
async def get_filter_options(
    principal: GuestPrincipalDep,
    filters: GetFeedFiltersDep,
    mode: FeedMode | None = None,
) -> FilterOptionsResponse:
    """Les options."""
    return FilterOptionsResponse.from_options(
        await filters.execute(principal.subject_id if principal else None, mode)
    )


@matches_router.post(
    "/me/seen",
    response_model=SeenResponse,
    summary="Marquer des offres comme affichées",
    description=(
        "L'app envoie par lots (100 au plus) les offres passées à l'écran : `offer_ids`, ou "
        "`impressions` avec la place de chaque carte (`offset + index`), ce qui nourrit "
        "l'apprentissage du classement. Idempotent sur le « vu »."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT),
)
async def mark_seen(
    body: SeenRequest, principal: CurrentPrincipalDep, mark: MarkSeenDep
) -> SeenResponse:
    """Marque."""
    return SeenResponse(updated=await mark.execute(principal.subject_id, body.to_impressions()))


@offers_router.get(
    "/{offer_id}",
    operation_id="offers_public_offer",
    response_model=JobMatchResponse,
    summary="Une offre (nom public)",
    description="La même offre que `GET /job-matches/{offer_id}`, sans compte aussi.",
    responses=error_responses(ErrorCode.NOT_FOUND, ErrorCode.RATE_LIMITED),
)
@matches_router.get(
    "/{offer_id}",
    response_model=JobMatchResponse,
    summary="Une offre",
    description=(
        "L'offre complète (`job_offer.description`), sa pertinence pour la personne si elle est "
        "dans son fil, et son état. Une offre expirée reste lisible (`is_active: false`). "
        "`404 offer_not_found` si elle n'existe pas. Sans session : l'offre seule, sans place "
        "ni état, et rien n'est consigné."
    ),
    responses=error_responses(ErrorCode.NOT_FOUND, ErrorCode.RATE_LIMITED),
)
async def get_match(
    offer_id: UUID,
    principal: GuestPrincipalDep,
    get: GetOfferDep,
    clock: ClockDep,
    logo_url: LogoUrlDep,
) -> JobMatchResponse:
    """Le détail."""
    item = await get.execute(principal.subject_id if principal else None, offer_id)
    return JobMatchResponse.from_item(item, now=clock.now(), detail=True, logo_url=logo_url)


@matches_router.patch(
    "/{offer_id}/interaction",
    response_model=JobMatchResponse,
    summary="Rapporter ce que la personne a fait de l'offre",
    description=(
        "Corps partiel : `saved`, `applied` et `dismissed` se posent et se retirent ; "
        "`detail_opened` et `link_clicked` ne valent que `true` et ne redescendent jamais. "
        "Une offre écartée quitte le fil, recherche après recherche. Rend l'offre à jour. "
        "`422 interaction_empty` si le corps ne change rien."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND, ErrorCode.INVALID_INPUT
    ),
)
async def update_interaction(
    *,
    offer_id: UUID,
    body: InteractionRequest,
    principal: CurrentPrincipalDep,
    update: UpdateStateDep,
    clock: ClockDep,
    logo_url: LogoUrlDep,
) -> JobMatchResponse:
    """Met à jour."""
    if not body.model_fields_set:
        raise InvalidInputError("Aucun changement.", details={"reason": "interaction_empty"})
    item = await update.execute(
        principal.subject_id,
        offer_id,
        StateChanges(
            opened=body.detail_opened,
            link_clicked=body.link_clicked,
            saved=body.saved,
            applied=body.applied,
            dismissed=body.dismissed,
            position=body.position,
            dwell_ms=body.dwell_ms,
        ),
    )
    return JobMatchResponse.from_item(item, now=clock.now(), logo_url=logo_url)


@assistant_router.post(
    "/messages",
    response_model=AssistantReplyResponse,
    summary="Parler à Karria",
    description=(
        "Un message de la personne, une réponse de Karria. `conversation_id: null` ouvre une "
        "conversation ; le serveur garde l'historique. `status` dit ce qui s'est passé : "
        "`search` — la demande est comprise, le fil vient d'être recalculé (`results_count`) ; "
        "`clarify` — Karria pose une question ; `chat` — rien n'a changé. `suggestions` : des "
        "réponses rapides à proposer.\n\n"
        "`404 conversation_not_found` ; `429 assistant_rate_limited` au-delà du quota "
        "journalier ; `503 ai_unavailable` ou `search_unavailable` si un service ne répond pas. "
        "`403 ai_consent_required` tant que la personne n'a pas accepté l'information sur "
        "l'IA (`GET /auth/me/ai-consent`, ADR-0025)."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.INVALID_INPUT,
        ErrorCode.NOT_FOUND,
        ErrorCode.RATE_LIMITED,
        ErrorCode.DEPENDENCY_FAILURE,
    ),
)
async def post_message(
    body: MessageRequest, principal: AiConsentedPrincipalDep, converse: ConverseSearchDep
) -> AssistantReplyResponse:
    """Un tour."""
    content = body.content.strip()
    if not content:
        raise InvalidInputError(
            "Le message est vide.",
            details={"reason": "assistant_message_empty", "field": "content"},
        )
    turn = await converse.execute(
        principal.subject_id, conversation_id=body.conversation_id, content=content
    )
    return AssistantReplyResponse.from_turn(turn)


@assistant_router.get(
    "/memory",
    response_model=MemoryResponse,
    summary="Ce que Karria a retenu de moi",
    description="Les faits stables que Karria garde pour mieux aider (métiers visés, mobilité, "
    "contraintes, préférences, situation). Vide si rien n'a été retenu.",
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def get_memory(principal: CurrentPrincipalDep, memory: GetMemoryDep) -> MemoryResponse:
    """La mémoire."""
    return MemoryResponse.from_memory(await memory.execute(principal.subject_id))


@assistant_router.delete(
    "/memory",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Effacer ce que Karria a retenu de moi",
    description="Idempotent : Karria repart de zéro sur la personne, sans toucher à la "
    "recherche en cours ni au fil.",
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def delete_memory(principal: CurrentPrincipalDep, reset: ResetMemoryDep) -> None:
    """L'oubli."""
    await reset.execute(principal.subject_id)


@search_router.get(
    "/criteria",
    response_model=CriteriaEnvelopeResponse,
    summary="La recherche en cours",
    description="`criteria: null` quand la personne n'a encore rien demandé : le fil montre alors toutes les offres.",
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def get_criteria(
    principal: CurrentPrincipalDep, get: GetCriteriaDep
) -> CriteriaEnvelopeResponse:
    """Les critères."""
    stored = await get.execute(principal.subject_id)
    return CriteriaEnvelopeResponse(
        criteria=CriteriaResponse.from_stored(stored) if stored else None
    )


@search_router.put(
    "/criteria",
    response_model=CriteriaAppliedResponse,
    summary="Modifier la recherche directement",
    description=(
        "Sans conversation : l'app envoie des critères structurés (au moins `job_titles`, et un "
        'lieu, `whole_france` ou `remote: ["teletravail"]`). Un lieu nommé sans coordonnées est '
        "géocodé. Le fil est recalculé. `422 criteria_incomplete` sinon."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def put_criteria(
    body: CriteriaRequest, principal: CurrentPrincipalDep, apply: ApplyCriteriaDep
) -> CriteriaAppliedResponse:
    """Pose et cherche."""
    applied = await apply.execute(principal.subject_id, body.to_domain())
    return CriteriaAppliedResponse(
        criteria=CriteriaResponse.from_stored(applied.stored),
        results_count=applied.results,
    )


@search_router.delete(
    "/criteria",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Oublier la recherche",
    description="Le fil redevient « toutes les offres » et les alertes cessent. Les offres sauvegardées restent.",
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def delete_criteria(principal: CurrentPrincipalDep, clear: ClearCriteriaDep) -> None:
    """Oublie."""
    await clear.execute(principal.subject_id)


@search_router.put(
    "/criteria/alerts",
    response_model=AlertsResponse,
    summary="Activer ou couper les alertes",
    description="`404 criteria_not_found` sans recherche en cours : il n'y a rien à surveiller.",
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def put_alerts(
    body: AlertsRequest, principal: CurrentPrincipalDep, toggle: SetAlertsEnabledDep
) -> AlertsResponse:
    """Bascule."""
    stored = await toggle.execute(principal.subject_id, enabled=body.enabled)
    if stored is None:
        raise CriteriaNotFoundError
    return AlertsResponse(enabled=stored.alerts_enabled)


@internal_router.get(
    "/targets",
    response_model=TargetsResponse,
    summary="Les cibles à collecter (collecteur seulement)",
    description=(
        "Les requêtes que le collecteur doit soumettre maintenant, dérivées des recherches "
        "des personnes et d'un socle. Les cibles rendues sont louées 30 minutes : un second "
        "appel ne les rend pas. Jeton de service `Authorization: Bearer <COLLECTOR__TOKEN>`."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def lease_targets(
    lease: LeaseTargetsDep, limit: Annotated[int, Query(ge=1, le=TARGETS_MAX_LIMIT)] = 30
) -> TargetsResponse:
    """Les cibles louées."""
    leased = await lease.execute(limit=limit)
    return TargetsResponse(
        handoff_version=HANDOFF_VERSION,
        targets=[
            TargetResponse(
                key=t.key,
                search_term=t.search_term,
                location=t.location,
                distance_km=t.distance_km,
                last_found=t.last_found,
            )
            for t in leased
        ],
    )


@internal_router.post(
    "/batches",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchAcceptedResponse,
    summary="Remettre un lot d'annonces brutes (collecteur seulement)",
    description=(
        "Corps : JSONL compressé gzip (`Content-Type: application/gzip`), une annonce par "
        "ligne au format de `docs/collecteur.md`. En-têtes : `X-Handoff-Version` (obligatoire), "
        "`X-Target-Key` (la cible servie, si le lot en sert une). Le lot est atterri tel quel, "
        "son ingestion est différée. `422 handoff_version_unsupported`, `batch_too_large`, "
        "`target_unknown`."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT),
)
async def receive_batch(
    request: Request,
    receive: ReceiveBatchDep,
    x_handoff_version: Annotated[int, Header()],
    x_target_key: Annotated[str | None, Header(max_length=512)] = None,
) -> BatchAcceptedResponse:
    """Atterrit et diffère."""
    if x_handoff_version != HANDOFF_VERSION:
        raise BatchRejectedError("handoff_version_unsupported")
    declared = int(request.headers.get("content-length") or 0)
    if declared > BATCH_MAX_BYTES:
        raise BatchRejectedError("batch_too_large")
    payload = await request.body()
    return BatchAcceptedResponse(key=await receive.execute(payload, target_key=x_target_key))


router = APIRouter()
router.include_router(matches_router)
router.include_router(offers_router)
router.include_router(assistant_router)
router.include_router(search_router)
router.include_router(internal_router)
