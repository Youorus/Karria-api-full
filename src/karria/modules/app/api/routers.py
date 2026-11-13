"""Route du module `app` : la version minimale, publique, comptée par origine."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from karria.modules.app.api.dependencies import GuestPrincipalDep, SettingsDep
from karria.modules.app.api.schemas import ReleaseRead
from karria.modules.app.domain import evaluate_release
from karria.shared.errors import ErrorCode, error_responses

router = APIRouter(prefix="/app", tags=["Application"])


@router.get(
    "/release",
    summary="La version installée est-elle à jour ?",
    response_model=ReleaseRead,
    description=(
        "Sans session : l'app demande avant de se connecter. Compté par origine "
        "(`429 anonymous_rate_limited`). Sans réglage (`APP_RELEASE__*` à `0`), toujours `ok`."
    ),
    responses=error_responses(ErrorCode.RATE_LIMITED),
)
async def release(
    _principal: GuestPrincipalDep,
    settings: SettingsDep,
    platform: Annotated[Literal["ios", "android"], Query()],
    build: Annotated[int, Query(ge=0, description="Le numéro de build natif installé.")],
) -> ReleaseRead:
    """Recevoir, déléguer à la règle, projeter."""
    thresholds = settings.app_release.for_platform(platform)
    verdict = evaluate_release(
        build, minimum_build=thresholds.minimum_build, latest_build=thresholds.latest_build
    )
    return ReleaseRead(
        status=verdict.status,
        latest_build=verdict.latest_build,
        minimum_build=verdict.minimum_build,
        latest_version=thresholds.latest_version,
        store_url=thresholds.store_url,
    )
