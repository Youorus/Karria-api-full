"""Routes du module `devices` — `docs/contrat-api-appareils.md` de l'app.

Toutes exigent la session. L'app appelle `PUT` à chaque démarrage et à
chaque changement de jeton ou de permission ; `DELETE` à la déconnexion.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from karria.modules.devices.api.dependencies import (
    InstallationIdPath,
    RegisterDeviceDep,
    RequestCountryDep,
    SendPushToUserDep,
    UnregisterDeviceDep,
)
from karria.modules.devices.api.schemas import (
    DeviceRegistrationRequest,
    DeviceResponse,
    TestNotificationResponse,
)
from karria.shared.auth import CurrentPrincipalDep
from karria.shared.errors import EntityNotFoundError, ErrorCode, error_responses
from karria.shared.http.dependencies import SettingsDep

router = APIRouter(prefix="/devices", tags=["Appareils"])


@router.put(
    "/{installation_id}",
    response_model=DeviceResponse,
    summary="Déclarer cet appareil",
    description=(
        "Crée ou met à jour l'installation pour le compte connecté — "
        "idempotent par `(compte, installation_id)`. À appeler à chaque "
        "démarrage et à chaque changement de jeton ou de permission.\n\n"
        "Le serveur retient aussi la première et la dernière déclaration, et le "
        "pays de la requête si le mandataire le transmet (`CF-IPCountry`). "
        "Aucune adresse IP n'est conservée.\n\n"
        "Validation stricte (longueurs, énumérations, forme `ExponentPushToken[…]`) : `422`."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT),
)
async def register_device(
    installation_id: InstallationIdPath,
    body: DeviceRegistrationRequest,
    principal: CurrentPrincipalDep,
    country: RequestCountryDep,
    register: RegisterDeviceDep,
) -> DeviceResponse:
    """Déclare l'appareil."""
    device = await register.execute(
        user_id=principal.subject_id,
        installation_id=installation_id,
        info=body.to_info(),
        country=country,
    )
    return DeviceResponse.from_domain(device)


@router.delete(
    "/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Détacher cet appareil",
    description=(
        "À appeler à la déconnexion : le jeton est détaché et plus rien n'est "
        "envoyé à cette installation. Les autres appareils du compte ne sont "
        "pas touchés. Silencieux si l'appareil n'existe pas."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def unregister_device(
    installation_id: InstallationIdPath,
    principal: CurrentPrincipalDep,
    unregister: UnregisterDeviceDep,
) -> None:
    """Détache l'appareil."""
    await unregister.execute(user_id=principal.subject_id, installation_id=installation_id)


@router.post(
    "/me/test-notification",
    response_model=TestNotificationResponse,
    summary="S'envoyer une notification d'essai (hors production)",
    description=(
        "Envoie « Karria vous entend » à **ses propres** appareils joignables. "
        "Sert à vérifier la chaîne de bout en bout pendant le développement.\n\n"
        "Inoffensive par construction : exige la session, ne vise que "
        "l'appelant, et répond `404` dans les environnements déployés."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.NOT_FOUND),
)
async def send_test_notification(
    principal: CurrentPrincipalDep, settings: SettingsDep, send: SendPushToUserDep
) -> TestNotificationResponse:
    """Envoie une notification d'essai à soi-même."""
    if settings.app.environment.is_deployed:
        # Inexistante en recette et en production : ni 403, ni indice.
        raise EntityNotFoundError("Route", "test-notification")
    summary = await send.execute(
        user_id=principal.subject_id,
        title="Karria vous entend",
        body="Les notifications fonctionnent sur cet appareil.",
        data={"type": "TEST"},
    )
    return TestNotificationResponse(
        targeted=summary.targeted, accepted=summary.accepted, rejected=summary.rejected
    )
