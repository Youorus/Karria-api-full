"""Branchements entre modules.

Le problème que ce fichier résout
---------------------------------
Un module ne peut pas en importer un autre (règle 4) : le fil d'offres
n'a pas le droit de connaître les comptes. Le bus d'événements règle la
plupart des échanges, mais pas ceux qui sont **synchrones et bloquants** —
« qui est l'appelant de cette requête ? » conditionne l'acceptation de la
requête en cours et ne peut pas attendre un message.

Pour ces cas, le module demandeur déclare un **port** exprimant son besoin
sans nommer qui y répond, et `bootstrap/` — seule couche autorisée à voir
plusieurs modules — substitue l'implémentation réelle.

Pourquoi `dependency_overrides` et non un import direct
--------------------------------------------------------
C'est le mécanisme que FastAPI prévoit déjà, celui-là même que les tests
utilisent. Le réutiliser évite d'inventer un conteneur d'injection maison,
et rend le branchement visible en un seul endroit plutôt que dispersé dans
des `import` que le contrat d'architecture refuserait.

Ce fichier grandira à chaque nouveau branchement. C'est voulu : sa
longueur est la mesure honnête du couplage synchrone entre modules, et une
raison de préférer le bus quand c'est possible.

La règle de chaque branchement
------------------------------
Le port garde une implémentation par défaut qui **refuse** : un
branchement oublié produit un 401 ou un 403 bruyant plutôt qu'une faille
silencieuse. Chaque ligne ajoutée ici porte, en commentaire, ce qui
casserait sans elle.

Les branchements d'aujourd'hui
------------------------------
- `resolve_principal` ← `identity.ResolveSession`. Sans lui, toute route
  protégée répond 401 : c'est le refus par défaut de l'ADR-0007.
- `identity.get_main_device_lookup` ← `devices.GetMainDevice`. Sans lui,
  la trace de départ ignore la plateforme et la version de l'app
  (ADR-0015) : dégradé, pas cassé.
- `offers.get_user_notifier` ← `devices.SendPushToUser`. Sans lui, les
  alertes d'offres enrichissent le fil mais aucune notification ne part
  (`offers.notifier_unwired`) : dégradé, pas cassé.
- `documents.get_cv_source` ← `cv.GetMyCv`. Sans lui, aperçus et exports
  répondent `cv_missing` (le refus par défaut de `documents`).

Les ponts du bus
----------------
Un module ne peut pas s'abonner à l'événement d'un autre : il faudrait
qu'il importe son type. Les abonnements **entre** modules se font donc ici,
dans `subscribe_bridges`, appelé par le `lifespan` une fois les ressources
ouvertes. Aujourd'hui : `identity.AccountDeleted` → `devices` oublie les
appareils, `cv` oublie brouillon, CV et photos, `offers` oublie critères,
fil, états et conversations, `documents` oublie exports (fichiers compris),
modèles possédés et choix (ADR-0015).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends, Request

from karria.modules.cv import CurriculumVitae, GetMyCv, build_forget_user, get_get_my_cv
from karria.modules.devices import (
    GetMainDevice,
    SendPushToUser,
    build_forget_user_devices,
    get_get_main_device,
    get_send_push_to_user,
)
from karria.modules.documents import CvSnapshot, CvSource, get_cv_source
from karria.modules.documents import build_forget_user as build_forget_user_documents
from karria.modules.identity import (
    AccountDeleted,
    MainDeviceLookup,
    MainDeviceSnapshot,
    ResolveSession,
    get_main_device_lookup,
    get_resolve_session,
)
from karria.modules.offers import (
    UserNotifier,
    get_user_notifier,
)
from karria.modules.offers import (
    build_forget_user as build_forget_user_offers,
)
from karria.shared.auth import Principal, resolve_principal
from karria.shared.logging import get_logger
from karria.shared.push import PushMessage

if TYPE_CHECKING:
    from fastapi import FastAPI

    from karria.shared.events import EventBus
    from karria.shared.storage import ObjectStorage

logger = get_logger(__name__)


async def resolve_principal_from_session(
    request: Request,
    resolver: Annotated[ResolveSession, Depends(get_resolve_session)],
) -> Principal | None:
    """Reconnaît l'appelant à partir de son en-tête `Authorization: Bearer`.

    C'est **la** couture prévue par l'ADR-0007 : la brancher authentifie
    d'un coup toutes les routes protégées, sans qu'aucune ne change. Elle
    vit dans `bootstrap/` parce qu'elle relie `shared/auth` — qui définit
    `Principal` — à `identity`, qui sait résoudre une session. `shared/`
    ne connaît toujours pas le métier (règle 1).

    Rend `None` plutôt que de lever : un jeton absent ou périmé n'est pas
    une erreur sur une route publique. C'est `CurrentPrincipalDep` qui
    décide qu'il en faut un.

    Un seul type d'utilisateur, aucune permission d'administration pour
    l'instant (ADR-0007) : le `Principal` ne porte que le sujet.
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None

    caller = await resolver.execute(token.strip())
    if caller is None:
        return None

    # La session est attachée à la requête et non au `Principal` : celui-ci
    # est le contrat de `shared/auth`, qui n'a pas à connaître la notion de
    # session.
    request.state.session_id = caller.session_id
    return Principal(subject_id=caller.user_id)


class DevicesMainDeviceLookup:
    """Implémente le port `MainDeviceLookup` d'`identity` avec `devices.GetMainDevice`.

    Vit ici parce qu'il voit les deux modules : il traduit la réponse de l'un
    dans le vocabulaire de l'autre, et c'est tout ce qu'il fait.
    """

    def __init__(self, main_device: GetMainDevice) -> None:
        self._main_device = main_device

    async def main_device_of(self, user_id: UUID) -> MainDeviceSnapshot | None:
        """L'appareil vu en dernier, dans les termes d'`identity`."""
        summary = await self._main_device.execute(user_id)
        if summary is None:
            return None
        return MainDeviceSnapshot(platform=summary.platform, app_version=summary.app_version)


def main_device_lookup_from_devices(
    main_device: Annotated[GetMainDevice, Depends(get_get_main_device)],
) -> MainDeviceLookup:
    """La réponse du module `devices` au besoin d'`identity`."""
    return DevicesMainDeviceLookup(main_device)


class DevicesUserNotifier:
    """Implémente le port `UserNotifier` d'`offers` avec `devices.SendPushToUser`."""

    def __init__(self, send: SendPushToUser) -> None:
        self._send = send

    async def notify(self, user_id: UUID, message: PushMessage) -> int:
        """Achemine, et rend le nombre d'appareils qui ont accepté."""
        summary = await self._send.execute(user_id=user_id, message=message)
        return summary.accepted


def user_notifier_from_devices(
    send: Annotated[SendPushToUser, Depends(get_send_push_to_user)],
) -> UserNotifier:
    """La réponse du module `devices` au besoin d'`offers`."""
    return DevicesUserNotifier(send)


class CvModuleSource:
    """Implémente le port `CvSource` de `documents` avec `cv.GetMyCv`.

    Rend le CV sous la forme de `GET /cv/me` (JSON), ou `None` s'il est encore
    vide : `documents` répond alors `cv_missing`.
    """

    def __init__(self, get_my_cv: GetMyCv) -> None:
        self._get_my_cv = get_my_cv

    async def snapshot(self, user_id: UUID) -> CvSnapshot | None:
        """Le CV de la personne, dans les termes de `documents`."""
        cv = await self._get_my_cv.execute(user_id)
        if cv.is_blank:
            return None
        return CvSnapshot(data=cv_as_json(cv), image_key=cv.image_key, updated_at=cv.updated_at)


def cv_as_json(cv: CurriculumVitae) -> dict[str, object]:
    """Le CV au format du contrat (`GET /cv/me`), sans la photo."""
    return {
        "identity": cv.identity.model_dump(mode="json") if cv.identity else None,
        "contact": cv.contact.model_dump(mode="json") if cv.contact else None,
        "summary": cv.summary,
        "experiences": [e.model_dump(mode="json") for e in cv.experiences],
        "educations": [e.model_dump(mode="json") for e in cv.educations],
        "skills": [s.model_dump(mode="json") for s in cv.skills],
        "languages": [lg.model_dump(mode="json") for lg in cv.languages],
        "interests": [i.model_dump(mode="json") for i in cv.interests],
        "projects": [p.model_dump(mode="json") for p in cv.projects],
    }


def cv_source_from_cv(get_my_cv: Annotated[GetMyCv, Depends(get_get_my_cv)]) -> CvSource:
    """La réponse du module `cv` au besoin de `documents`."""
    return CvModuleSource(get_my_cv)


def build_user_notifier() -> UserNotifier:
    """Le même branchement, hors requête, pour `scripts/offers_worker.py`."""
    from karria.modules.devices.api.dependencies import (  # noqa: PLC0415 - assemblage hors requête
        get_clock,
        get_device_repository,
        get_push_gateway,
        get_ticket_repository,
    )
    from karria.shared.config import get_settings  # noqa: PLC0415
    from karria.shared.database import TortoiseUnitOfWork  # noqa: PLC0415

    return DevicesUserNotifier(
        get_send_push_to_user(
            devices=get_device_repository(),
            tickets=get_ticket_repository(),
            gateway=get_push_gateway(get_settings()),
            clock=get_clock(),
            uow=TortoiseUnitOfWork(),
        )
    )


def wire_modules(app: FastAPI) -> None:
    """Branche les ports inter-modules sur leurs implémentations réelles.

    Appelé par `create_app`. Sans cet appel, les ports concernés gardent
    leur implémentation par défaut — qui **refuse**.
    """
    # Sans cette ligne : 401 sur toute route protégée, l'app ne dépasse pas
    # l'écran de connexion.
    app.dependency_overrides[resolve_principal] = resolve_principal_from_session
    # Sans cette ligne : la trace de départ ne sait ni la plateforme ni la
    # version de l'app. Rien ne casse ; l'équipe produit perd deux colonnes.
    app.dependency_overrides[get_main_device_lookup] = main_device_lookup_from_devices
    # Sans cette ligne : les alertes d'offres remplissent le fil mais
    # aucune notification ne part.
    app.dependency_overrides[get_user_notifier] = user_notifier_from_devices
    # Sans cette ligne : aucun CV n'est visible des modèles, tout aperçu et
    # tout export répondent `cv_missing`.
    app.dependency_overrides[get_cv_source] = cv_source_from_cv

    logger.debug("bootstrap.modules_wired", bindings=4)


def subscribe_bridges(bus: EventBus, *, storage: ObjectStorage) -> None:
    """Abonne les réactions d'un module aux événements d'un autre.

    Appelé par le `lifespan` **après** l'ouverture du stockage : l'oubli du
    CV efface des objets, il lui faut le client ouvert pour la durée du
    processus. Chaque abonné reçoit les adaptateurs réellement déployés,
    assemblés par son module (`build_*`) — `bootstrap` ne connaît pas leurs
    dépôts.

    Les deux abonnés sont **indépendants** : le bus absorbe l'échec de l'un
    sans priver l'autre (`event_bus.handler_failed`), et chacun est
    idempotent : rejouer l'événement termine ce qui aurait manqué.
    """
    forget_devices = build_forget_user_devices()
    forget_cv = build_forget_user(storage)
    forget_offers = build_forget_user_offers()
    forget_documents = build_forget_user_documents(storage)

    async def devices_forget(event: AccountDeleted) -> None:
        await forget_devices.execute(event.user_id)

    async def cv_forget(event: AccountDeleted) -> None:
        await forget_cv.execute(event.user_id)

    async def offers_forget(event: AccountDeleted) -> None:
        await forget_offers.execute(event.user_id)

    async def documents_forget(event: AccountDeleted) -> None:
        await forget_documents.execute(event.user_id)

    # Sans ces lignes : un compte supprimé laisse ses appareils, son CV, ses
    # photos, ses critères et ses conversations — c'est-à-dire exactement
    # ce que la personne a demandé d'effacer.
    bus.subscribe(AccountDeleted, devices_forget)
    bus.subscribe(AccountDeleted, cv_forget)
    bus.subscribe(AccountDeleted, offers_forget)
    bus.subscribe(AccountDeleted, documents_forget)
    logger.debug("bootstrap.bridges_subscribed", bridges=4)
