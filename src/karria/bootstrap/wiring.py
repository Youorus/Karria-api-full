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
- `documents.get_account_directory` ← `identity.GetCurrentUser`. Sans lui,
  envoyer ses documents « à mon adresse » (`to: null`) répond
  `account_email_unknown` (ADR-0031) : refus, pas faille.
- `payments.get_account_directory` ← `identity.GetCurrentUser`. Sans lui, le
  client Stripe naît sans adresse : les reçus ne partent pas (dégradé).
- `payments.get_template_store` ← `documents.TemplateShop`. Sans lui, aucun
  modèle n'est en vente et un modèle payé ne peut pas être remis : le webhook
  répond 503 et Stripe réémet jusqu'au branchement (refus, pas faille).
- `shared.auth.get_ai_consent_gate` ← `identity.EnsureAiConsent`. Sans lui,
  toute route d'IA (Karria, dictée, CV en conversation, import, photo,
  explication ATS) répond 403 `ai_consent_required` : le refus par défaut
  de l'ADR-0025 — un oubli se voit, il ne laisse rien partir.
- `shared.auth.get_token_resolver` ← `identity.ResolveSession`. Sans lui, le
  flux WebSocket de « Parler à l'équipe » refuse tout jeton (`4401`) : le
  refus par défaut, comme pour `resolve_principal`.
- `support.get_user_notifier` ← `devices.SendPushToUser`. Sans lui, une
  réponse de l'équipe n'est pas notifiée (`support.notifier_unwired`) :
  dégradé, pas cassé — le direct et le non-lu restent.
- `payments.get_user_notifier` ← `devices.SendPushToUser`. Sans lui, le
  merci d'un paiement n'arrive que par courriel (`payments.notifier_unwired`) :
  dégradé, pas cassé — le webhook livre quand même (ADR-0024 § 2026-11-05).
- `support.get_account_directory` ← `identity.GetCurrentUser`. Sans lui, la
  console de l'équipe montre « compte inconnu » à la place des adresses.
- `applications.get_offer_reader` ← `offers.GetOfferForUser`,
  `applications.get_logo_url` ← `offers.get_logo_url`,
  `applications.get_base_cv_reader` ← `cv.GetMyCv`,
  `applications.get_cv_validator` ← les schémas de `cv`,
  `applications.get_template_checker` ← `documents.TemplateShop` (ADR-0033).
  Sans eux : `offer_not_found`, initiales, `cv_missing`, 503 à la retouche,
  `template_locked` — les refus par défaut du module.

Les ponts du bus
----------------
Un module ne peut pas s'abonner à l'événement d'un autre : il faudrait
qu'il importe son type. Les abonnements **entre** modules se font donc ici,
dans `subscribe_bridges`, appelé par le `lifespan` une fois les ressources
ouvertes. Aujourd'hui : `identity.AccountDeleted` → `devices` oublie les
appareils, `cv` oublie brouillon, CV et photos, `offers` oublie critères,
fil, états et conversations, `documents` oublie exports (fichiers compris),
modèles possédés et choix (ADR-0015), `payments` supprime le client Stripe (ce
qui résilie l'abonnement) et efface projection et achats (ADR-0024),
`support` efface le fil avec l'équipe, fichiers compris, `applications`
efface les dossiers de candidature ; `applications.DossierApplied` /
`DossierUnapplied` → `offers` pose ou retire « candidaté » sur l'offre du fil.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, Final
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ValidationError

from karria.modules.applications import (
    BaseCv,
    BaseCvReader,
    CvInvalidError,
    CvValidator,
    DossierApplied,
    DossierUnapplied,
    OfferCard,
    OfferReader,
    TemplateChecker,
    get_base_cv_reader,
    get_cv_validator,
    get_offer_reader,
    get_template_checker,
)
from karria.modules.applications import (
    LogoUrl as ApplicationsLogoUrl,
)
from karria.modules.applications import build_forget_user as build_forget_user_applications
from karria.modules.applications import (
    get_logo_url as get_applications_logo_url,
)
from karria.modules.cv import (
    Contact,
    CurriculumVitae,
    Education,
    Experience,
    GetMyCv,
    Identity,
    InterestsChapter,
    Project,
    SkillsChapter,
    Summary,
    build_forget_user,
    get_get_my_cv,
)
from karria.modules.devices import (
    GetMainDevice,
    SendPushToUser,
    build_forget_user_devices,
    get_get_main_device,
    get_send_push_to_user,
)
from karria.modules.documents import (
    AccountDirectory as DocumentsAccountDirectory,
)
from karria.modules.documents import (
    CvSnapshot,
    CvSource,
    TemplateShop,
    get_cv_source,
    get_template_shop,
)
from karria.modules.documents import build_forget_user as build_forget_user_documents
from karria.modules.documents import (
    get_account_directory as get_documents_account_directory,
)
from karria.modules.identity import (
    AccountDeleted,
    EnsureAiConsent,
    GetCurrentUser,
    MainDeviceLookup,
    MainDeviceSnapshot,
    ResolveSession,
    UserNotFoundError,
    get_current_user_reader,
    get_ensure_ai_consent,
    get_main_device_lookup,
    get_resolve_session,
)
from karria.modules.offers import (
    GetOfferForUser,
    LogoUrl,
    StateChanges,
    UserNotifier,
    build_update_state,
    get_get_offer,
    get_logo_url,
    get_user_notifier,
)
from karria.modules.offers import (
    build_forget_user as build_forget_user_offers,
)
from karria.modules.payments import (
    AccountDirectory,
    PremiumOffer,
    TemplateStore,
    ThankForPurchase,
    build_thank_for_purchase,
    get_account_directory,
    get_template_store,
)
from karria.modules.payments import UserNotifier as PaymentsUserNotifier
from karria.modules.payments import build_forget_user as build_forget_user_payments
from karria.modules.payments import get_user_notifier as get_payments_user_notifier
from karria.modules.support import (
    AccountDirectory as SupportAccountDirectory,
)
from karria.modules.support import (
    UserNotifier as SupportUserNotifier,
)
from karria.modules.support import (
    build_forget_user as build_forget_user_support,
)
from karria.modules.support import (
    get_account_directory as get_support_account_directory,
)
from karria.modules.support import (
    get_user_notifier as get_support_user_notifier,
)
from karria.shared.auth import (
    AiConsentGate,
    Principal,
    TokenResolver,
    get_ai_consent_gate,
    get_token_resolver,
    resolve_principal,
)
from karria.shared.config import get_settings
from karria.shared.errors import EntityNotFoundError
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


class IdentityTokenResolver:
    """Implémente `TokenResolver` du socle avec `identity.ResolveSession` — pour les WebSockets."""

    def __init__(self, resolver: ResolveSession) -> None:
        self._resolver = resolver

    async def resolve(self, token: str) -> Principal | None:
        """Le même jugement que `resolve_principal_from_session`, sans `Request`."""
        caller = await self._resolver.execute(token)
        return None if caller is None else Principal(subject_id=caller.user_id)


def token_resolver_from_identity(
    resolver: Annotated[ResolveSession, Depends(get_resolve_session)],
) -> TokenResolver:
    """La réponse du module `identity` au port de jeton nu de `shared/auth`."""
    return IdentityTokenResolver(resolver)


class IdentityAiConsentGate:
    """Implémente la porte d'IA du socle avec `identity.EnsureAiConsent` (ADR-0025)."""

    def __init__(self, ensure: EnsureAiConsent) -> None:
        self._ensure = ensure

    async def ensure(self, principal: Principal) -> None:
        """Laisse passer ou lève `AiConsentRequiredError` (403 `ai_consent_required`)."""
        await self._ensure.execute(principal.subject_id)


def ai_consent_gate_from_identity(
    ensure: Annotated[EnsureAiConsent, Depends(get_ensure_ai_consent)],
) -> AiConsentGate:
    """La réponse du module `identity` à la porte d'IA de `shared/auth`."""
    return IdentityAiConsentGate(ensure)


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


class IdentityAccountDirectory:
    """Implémente le port `AccountDirectory` de `payments` avec `identity.GetCurrentUser`."""

    def __init__(self, reader: GetCurrentUser) -> None:
        self._reader = reader

    async def email_of(self, user_id: UUID) -> str | None:
        """L'adresse du compte, ou `None` s'il n'existe plus.

        `User.email` est un `EmailAddress` ; c'est sa **valeur** qui part chez
        le prestataire — `str()` en donnerait la représentation, que Stripe
        refuse (`email_invalid`, constaté en production le 2026-11-04).
        """
        try:
            return (await self._reader.execute(user_id)).email.value
        except UserNotFoundError:
            return None


def account_directory_from_identity(
    reader: Annotated[GetCurrentUser, Depends(get_current_user_reader)],
) -> AccountDirectory:
    """La réponse du module `identity` au besoin de `payments`."""
    return IdentityAccountDirectory(reader)


class DocumentsTemplateStore:
    """Implémente le port `TemplateStore` de `payments` avec `documents.TemplateShop`.

    Le prix de `documents` est un `Decimal` en euros ; `payments` compte en
    centimes entiers. La conversion vit ici, à la couture, et nulle part ailleurs.
    """

    def __init__(self, shop: TemplateShop) -> None:
        self._shop = shop

    async def premium_offers(self) -> list[PremiumOffer]:
        return [
            PremiumOffer(o.template_id, o.label, round(o.price * 100))
            for o in await self._shop.premium_offers()
        ]

    async def premium_offer(self, template_id: UUID) -> PremiumOffer | None:
        offer = await self._shop.premium_offer(template_id)
        if offer is None:
            return None
        return PremiumOffer(offer.template_id, offer.label, round(offer.price * 100))

    async def owns(self, user_id: UUID, template_id: UUID) -> bool:
        return await self._shop.owns(user_id, template_id)

    async def grant(self, user_id: UUID, template_id: UUID) -> None:
        await self._shop.grant(user_id, template_id)


def template_store_from_documents(
    shop: Annotated[TemplateShop, Depends(get_template_shop)],
) -> TemplateStore:
    """La réponse du module `documents` au besoin de `payments`."""
    return DocumentsTemplateStore(shop)


def support_user_notifier_from_devices(
    send: Annotated[SendPushToUser, Depends(get_send_push_to_user)],
) -> SupportUserNotifier:
    """La réponse du module `devices` au besoin de `support` (même adaptateur qu'`offers`)."""
    return DevicesUserNotifier(send)


def payments_user_notifier_from_devices(
    send: Annotated[SendPushToUser, Depends(get_send_push_to_user)],
) -> PaymentsUserNotifier:
    """La réponse du module `devices` au besoin de `payments` (le merci d'un paiement)."""
    return DevicesUserNotifier(send)


def support_account_directory_from_identity(
    reader: Annotated[GetCurrentUser, Depends(get_current_user_reader)],
) -> SupportAccountDirectory:
    """La réponse du module `identity` au besoin de `support` (même adaptateur que `payments`)."""
    return IdentityAccountDirectory(reader)


def documents_account_directory_from_identity(
    reader: Annotated[GetCurrentUser, Depends(get_current_user_reader)],
) -> DocumentsAccountDirectory:
    """La réponse du module `identity` au besoin de `documents` (envoi à l'adresse du compte)."""
    return IdentityAccountDirectory(reader)


class OffersOfferReader:
    """Implémente le port `OfferReader` d'`applications` avec `offers.GetOfferForUser`.

    Lit l'offre seule (sans compte : ni place dans le fil ni état) et la
    traduit dans les termes d'`applications` — le condensat du logo, jamais
    l'adresse chez la source.
    """

    def __init__(self, get_offer: GetOfferForUser) -> None:
        self._get_offer = get_offer

    async def read(self, offer_id: UUID) -> OfferCard | None:
        """L'offre, même inactive, ou `None` si l'index ne la connaît pas."""
        try:
            item = await self._get_offer.execute(None, offer_id)
        except EntityNotFoundError:
            return None
        offer = item.offer
        location = offer.location
        return OfferCard(
            offer_id=offer.id,
            title=offer.title,
            description=offer.description,
            company=offer.company.name if offer.company else None,
            logo_hash=offer.company.logo_hash if offer.company else None,
            website=offer.company.website if offer.company else None,
            city=location.label if location else None,
            contract=offer.contract_type.value if offer.contract_type else None,
            contract_label=offer.contract_type.label if offer.contract_type else None,
            source_url=offer.apply_url or offer.origin_url,
            published_at=offer.published_at,
        )


def offer_reader_from_offers(
    get_offer: Annotated[GetOfferForUser, Depends(get_get_offer)],
) -> OfferReader:
    """La réponse du module `offers` au besoin d'`applications`."""
    return OffersOfferReader(get_offer)


def applications_logo_url_from_offers(
    logo_url: Annotated[LogoUrl, Depends(get_logo_url)],
) -> ApplicationsLogoUrl:
    """L'adresse du logo en 128 px, calculée par `offers`."""

    def url(logo_hash: str) -> str:
        return logo_url(logo_hash, 128)

    return url


class CvBaseCvReader:
    """Implémente le port `BaseCvReader` d'`applications` avec `cv.GetMyCv`."""

    def __init__(self, get_my_cv: GetMyCv) -> None:
        self._get_my_cv = get_my_cv

    async def read(self, user_id: UUID) -> BaseCv | None:
        """Le CV de base au format `GET /cv/me`, ou `None` s'il est encore vide."""
        cv = await self._get_my_cv.execute(user_id)
        if cv.is_blank:
            return None
        return BaseCv(data=cv_as_json(cv), updated_at=cv.updated_at)


def base_cv_reader_from_cv(
    get_my_cv: Annotated[GetMyCv, Depends(get_get_my_cv)],
) -> BaseCvReader:
    """La réponse du module `cv` au besoin d'`applications`."""
    return CvBaseCvReader(get_my_cv)


class CvModuleValidator:
    """Implémente le port `CvValidator` d'`applications` avec les schémas de `cv`.

    Une seule définition de ce qu'est un CV (`cv/domain/schemas.py`) : le CV
    d'un dossier, retouché par la personne ou par une proposition de Karria,
    est jugé par les mêmes règles que le CV de base — **par chapitre**, pour
    que les plafonds des listes (`SkillsChapter` : 40 compétences, 15 langues ;
    `InterestsChapter` : 20) s'appliquent ; les listes sans chapitre
    (expériences, formations, projets) ont les plafonds explicites ci-dessous.
    Les clés inconnues sont ignorées, pas refusées.
    """

    #: Plafonds des listes que `cv` ne borne pas par un chapitre : au-delà, ce
    #: n'est plus un CV d'une ou deux pages, c'est un corps à refuser.
    LIST_LIMITS: Final[dict[str, int]] = {"experiences": 30, "educations": 20, "projects": 20}
    _ITEMS: Final[dict[str, type[BaseModel]]] = {
        "experiences": Experience,
        "educations": Education,
        "projects": Project,
    }
    _CHAPTERS: Final[dict[str, tuple[type[BaseModel], tuple[str, ...]]]] = {
        "skills": (SkillsChapter, ("skills", "languages")),
        "interests": (InterestsChapter, ("interests",)),
    }

    def validate(self, cv: Mapping[str, Any]) -> dict[str, Any]:
        """Le CV normalisé par les schémas de `cv`.

        Raises:
            CvInvalidError: Une section ne respecte pas sa forme.
        """
        errors: list[dict[str, Any]] = []
        out: dict[str, Any] = {"identity": None, "contact": None, "summary": None}
        singles: dict[str, type[BaseModel]] = {"identity": Identity, "contact": Contact}
        for name, single in singles.items():
            value = cv.get(name)
            if value is not None:
                out[name] = self._validated(single, value, name, errors)
        summary = cv.get("summary")
        if summary is not None and str(summary).strip():
            validated = self._validated(Summary, {"summary": summary}, "", errors)
            out["summary"] = validated["summary"] if validated else None
        for name, (chapter, fields) in self._CHAPTERS.items():
            payload = {f: self._as_list(cv.get(f), f, errors) for f in fields}
            validated = self._validated(chapter, payload, name, errors)
            for f in fields:
                out[f] = validated[f] if validated else []
        for name, model in self._ITEMS.items():
            raw = self._as_list(cv.get(name), name, errors)
            if len(raw) > self.LIST_LIMITS[name]:
                errors.append(
                    {"field": name, "message": f"Au plus {self.LIST_LIMITS[name]} éléments."}
                )
                raw = []
            out[name] = [
                item
                for index, candidate in enumerate(raw)
                if (item := self._validated(model, candidate, f"{name}.{index}", errors))
            ]
        if errors:
            raise CvInvalidError(errors)
        return out

    @staticmethod
    def _as_list(value: Any, name: str, errors: list[dict[str, Any]]) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        errors.append({"field": name, "message": "Une liste est attendue."})
        return []

    @staticmethod
    def _validated(
        model: type[BaseModel], value: Any, prefix: str, errors: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        try:
            return model.model_validate(value).model_dump(mode="json")
        except ValidationError as exc:
            errors.extend(_fields_of(prefix, exc))
            return None


def _fields_of(prefix: str, exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join(part for part in [prefix, *(str(p) for p in e["loc"])] if part),
            "message": e["msg"],
        }
        for e in exc.errors()
    ]


def cv_validator_from_cv() -> CvValidator:
    """La réponse du module `cv` au besoin d'`applications` (sans dépendance : des règles pures)."""
    return CvModuleValidator()


class DocumentsTemplateChecker:
    """Implémente le port `TemplateChecker` d'`applications` avec `documents.TemplateShop`.

    Un modèle est utilisable s'il est possédé, ou s'il n'est pas en vente
    (gratuit). `documents` reste l'autorité au rendu : un identifiant
    inconnu y répond 404, pas ici.
    """

    def __init__(self, shop: TemplateShop) -> None:
        self._shop = shop

    async def usable(self, user_id: UUID, template_id: UUID) -> bool:
        if await self._shop.owns(user_id, template_id):
            return True
        return await self._shop.premium_offer(template_id) is None


def template_checker_from_documents(
    shop: Annotated[TemplateShop, Depends(get_template_shop)],
) -> TemplateChecker:
    """La réponse du module `documents` au besoin d'`applications`."""
    return DocumentsTemplateChecker(shop)


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


def build_thank_for_purchase_wired() -> ThankForPurchase:
    """Le merci d'un paiement, hors requête, pour `scripts/payments_thanks.py`.

    Les mêmes branchements qu'en requête : l'adresse par `identity`, le nom
    du modèle par `documents`, la notification par `devices`.
    """
    from karria.modules.documents.api.dependencies import (  # noqa: PLC0415 - assemblage hors requête
        get_clock as get_documents_clock,
    )
    from karria.modules.documents.api.dependencies import (  # noqa: PLC0415
        get_template_repository,
        get_user_template_repository,
    )
    from karria.modules.identity.api.dependencies import (  # noqa: PLC0415
        get_user_repository,
    )

    return build_thank_for_purchase(
        get_settings(),
        directory=IdentityAccountDirectory(get_current_user_reader(get_user_repository())),
        notifier=build_user_notifier(),
        templates=DocumentsTemplateStore(
            get_template_shop(
                get_template_repository(), get_user_template_repository(), get_documents_clock()
            )
        ),
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
    # Sans cette ligne : le client Stripe naît sans adresse, aucun reçu ne part.
    app.dependency_overrides[get_account_directory] = account_directory_from_identity
    # Sans cette ligne : aucun modèle Premium n'est en vente, et un modèle payé
    # ne peut pas être remis — le webhook refuse, Stripe réémet.
    app.dependency_overrides[get_template_store] = template_store_from_documents
    # Sans cette ligne : aucun outil d'IA ne sert — 403 `ai_consent_required`
    # partout, même pour qui a accepté. Le refus est voulu, pas l'oubli.
    app.dependency_overrides[get_ai_consent_gate] = ai_consent_gate_from_identity
    # Sans cette ligne : le flux en direct de « Parler à l'équipe » ferme
    # toute connexion en 4401, même avec une session valide.
    app.dependency_overrides[get_token_resolver] = token_resolver_from_identity
    # Sans cette ligne : une réponse de l'équipe n'est jamais notifiée.
    app.dependency_overrides[get_support_user_notifier] = support_user_notifier_from_devices
    # Sans cette ligne : le merci d'un paiement n'arrive que par courriel.
    app.dependency_overrides[get_payments_user_notifier] = payments_user_notifier_from_devices
    # Sans cette ligne : la console de l'équipe ne montre aucune adresse.
    app.dependency_overrides[get_support_account_directory] = (
        support_account_directory_from_identity
    )
    # Sans cette ligne : « m'envoyer mes documents » ne connaît pas l'adresse
    # du compte et répond `account_email_unknown` (ADR-0031).
    app.dependency_overrides[get_documents_account_directory] = (
        documents_account_directory_from_identity
    )
    # Sans ces lignes : aucun dossier de candidature ne s'ouvre (`offer_not_found`
    # ou `cv_missing`), les logos manquent, la retouche répond 503 et tout
    # modèle est verrouillé — les refus par défaut d'`applications`.
    app.dependency_overrides[get_offer_reader] = offer_reader_from_offers
    app.dependency_overrides[get_applications_logo_url] = applications_logo_url_from_offers
    app.dependency_overrides[get_base_cv_reader] = base_cv_reader_from_cv
    app.dependency_overrides[get_cv_validator] = cv_validator_from_cv
    app.dependency_overrides[get_template_checker] = template_checker_from_documents

    logger.debug("bootstrap.modules_wired", bindings=17)


def subscribe_bridges(bus: EventBus, *, storage: ObjectStorage) -> None:
    """Abonne les réactions d'un module aux événements d'un autre.

    Appelé par le `lifespan` **après** l'ouverture du stockage : l'oubli du
    CV efface des objets, il lui faut le client ouvert pour la durée du
    processus. Chaque abonné reçoit les adaptateurs réellement déployés,
    assemblés par son module (`build_*`) — `bootstrap` ne connaît pas leurs
    dépôts.

    Les abonnés sont **indépendants** : le bus absorbe l'échec de l'un
    sans priver l'autre (`event_bus.handler_failed`), et chacun est
    idempotent : rejouer l'événement termine ce qui aurait manqué.
    """
    forget_devices = build_forget_user_devices()
    forget_cv = build_forget_user(storage)
    forget_offers = build_forget_user_offers()
    forget_documents = build_forget_user_documents(storage)
    forget_payments = build_forget_user_payments(get_settings())
    forget_support = build_forget_user_support(storage)
    forget_applications = build_forget_user_applications()
    update_state = build_update_state()

    async def devices_forget(event: AccountDeleted) -> None:
        await forget_devices.execute(event.user_id)

    async def cv_forget(event: AccountDeleted) -> None:
        await forget_cv.execute(event.user_id)

    async def offers_forget(event: AccountDeleted) -> None:
        await forget_offers.execute(event.user_id)

    async def documents_forget(event: AccountDeleted) -> None:
        await forget_documents.execute(event.user_id)

    async def payments_forget(event: AccountDeleted) -> None:
        await forget_payments.execute(event.user_id)

    async def support_forget(event: AccountDeleted) -> None:
        await forget_support.execute(event.user_id)

    async def applications_forget(event: AccountDeleted) -> None:
        await forget_applications.execute(event.user_id)

    async def offer_applied(event: DossierApplied) -> None:
        await update_state.execute(event.user_id, event.offer_id, StateChanges(applied=True))

    async def offer_unapplied(event: DossierUnapplied) -> None:
        await update_state.execute(event.user_id, event.offer_id, StateChanges(applied=False))

    # Sans ces lignes : un compte supprimé laisse ses appareils, son CV, ses
    # photos, ses critères et ses conversations — c'est-à-dire exactement
    # ce que la personne a demandé d'effacer.
    bus.subscribe(AccountDeleted, devices_forget)
    bus.subscribe(AccountDeleted, cv_forget)
    bus.subscribe(AccountDeleted, offers_forget)
    bus.subscribe(AccountDeleted, documents_forget)
    # Sans cette ligne : un compte supprimé continue d'être prélevé chez Stripe.
    bus.subscribe(AccountDeleted, payments_forget)
    # Sans cette ligne : un compte supprimé laisse son fil avec l'équipe,
    # vocaux et captures compris.
    bus.subscribe(AccountDeleted, support_forget)
    # Sans cette ligne : un compte supprimé laisse ses dossiers de candidature.
    bus.subscribe(AccountDeleted, applications_forget)
    # Sans ces lignes : « j'ai candidaté » depuis un dossier ne se voit pas sur
    # la carte de l'offre dans le fil — dégradé, pas cassé.
    bus.subscribe(DossierApplied, offer_applied)
    bus.subscribe(DossierUnapplied, offer_unapplied)
    logger.debug("bootstrap.bridges_subscribed", bridges=9)
