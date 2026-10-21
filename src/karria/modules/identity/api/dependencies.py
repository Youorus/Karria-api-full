"""Câblage du module `identity` : ports → implémentations, et use cases.

Trois adaptateurs sont des **singletons de processus**, et ce n'est pas un
détail :

- le limiteur de tentatives : sa mémoire est justement ce qu'il compte.
  Reconstruit à chaque requête, il remettrait le compteur à zéro — un
  limiteur qui ne limite rien est pire qu'aucun ;
- le cache des clés publiques (JWKS) : reconstruit à chaque requête, il
  irait chercher les clés d'Apple à chaque connexion ;
- l'adaptateur d'envoi d'e-mails : il porte une configuration, pas d'état.

Un test substitue n'importe lequel par `app.dependency_overrides`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request

from karria.modules.identity.application import (
    DeleteAccount,
    DepartureFeedbackRepository,
    GetCurrentUser,
    JwksSource,
    LoginChallengeRepository,
    LoginCodeSender,
    LoginThrottle,
    Logout,
    MainDeviceLookup,
    ProviderIdentityRepository,
    RefreshSession,
    ResendEmailLoginCode,
    ResolveSession,
    SessionRepository,
    SocialLogin,
    StartEmailLogin,
    TokenFactory,
    UserRepository,
    VerifyEmailLoginCode,
)
from karria.modules.identity.domain import SocialProvider, SocialProviderUnavailableError
from karria.modules.identity.infrastructure import (
    AppleTokenVerifier,
    GoogleTokenVerifier,
    HttpJwksSource,
    InMemoryLoginThrottle,
    MailLoginCodeSender,
    Sha256TokenFactory,
    TortoiseDepartureFeedbackRepository,
    TortoiseLoginChallengeRepository,
    TortoiseProviderIdentityRepository,
    TortoiseSessionRepository,
    TortoiseUserRepository,
    UnknownMainDeviceLookup,
)
from karria.shared.clock import Clock, SystemClock
from karria.shared.config import get_settings
from karria.shared.http.dependencies import EventBusDep, SettingsDep, UnitOfWorkDep
from karria.shared.i18n import Language, from_accept_language
from karria.shared.mail import Mailer, build_mailer

# ---------------------------------------------------------------------
# Adaptateurs
# ---------------------------------------------------------------------


def get_clock() -> Clock:
    """L'horloge réelle."""
    return SystemClock()


def get_user_repository() -> UserRepository:
    """Le dépôt des comptes."""
    return TortoiseUserRepository()


def get_identity_repository() -> ProviderIdentityRepository:
    """Le dépôt des rattachements Apple / Google."""
    return TortoiseProviderIdentityRepository()


def get_session_repository() -> SessionRepository:
    """Le dépôt des sessions."""
    return TortoiseSessionRepository()


def get_challenge_repository() -> LoginChallengeRepository:
    """Le dépôt des parcours de connexion."""
    return TortoiseLoginChallengeRepository()


def get_departure_repository() -> DepartureFeedbackRepository:
    """La trace anonyme des départs."""
    return TortoiseDepartureFeedbackRepository()


def get_main_device_lookup() -> MainDeviceLookup:
    """L'appareil principal d'un compte — **inconnu** tant que rien n'est branché.

    C'est la dépendance que `bootstrap/wiring.py` remplace par la réponse du
    module `devices` (règle 4). Sans ce branchement, la trace de départ perd
    la plateforme et la version : dégradé, jamais cassé.
    """
    return UnknownMainDeviceLookup()


@lru_cache(maxsize=1)
def _token_factory(pepper: str) -> Sha256TokenFactory:
    return Sha256TokenFactory(pepper)


def get_token_factory(settings: SettingsDep) -> TokenFactory:
    """La fabrique de jetons, poivrée par la configuration."""
    return _token_factory(settings.security.token_pepper.get_secret_value())


@lru_cache(maxsize=1)
def _throttle() -> InMemoryLoginThrottle:
    return InMemoryLoginThrottle()


def get_login_throttle() -> LoginThrottle:
    """Le limiteur de tentatives, partagé par tout le processus."""
    return _throttle()


@lru_cache(maxsize=1)
def _jwks_source() -> HttpJwksSource:
    return HttpJwksSource()


def get_jwks_source() -> JwksSource:
    """Le cache des clés publiques, partagé par tout le processus."""
    return _jwks_source()


@lru_cache(maxsize=1)
def _mailer_for(provider: str, host: str, sender: str) -> Mailer:
    """Mémorise l'adaptateur d'envoi ; les arguments ne servent qu'à la clé."""
    _ = (provider, host, sender)
    return build_mailer(get_settings().mail)


def get_login_code_sender(settings: SettingsDep) -> LoginCodeSender:
    """L'acheminement du code par e-mail."""
    mailer = _mailer_for(settings.mail.provider.value, settings.mail.host, settings.mail.sender)
    return MailLoginCodeSender(mailer)


ClockDep = Annotated[Clock, Depends(get_clock)]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
IdentityRepositoryDep = Annotated[ProviderIdentityRepository, Depends(get_identity_repository)]
SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
ChallengeRepositoryDep = Annotated[LoginChallengeRepository, Depends(get_challenge_repository)]
DepartureRepositoryDep = Annotated[DepartureFeedbackRepository, Depends(get_departure_repository)]
MainDeviceLookupDep = Annotated[MainDeviceLookup, Depends(get_main_device_lookup)]
TokenFactoryDep = Annotated[TokenFactory, Depends(get_token_factory)]
ThrottleDep = Annotated[LoginThrottle, Depends(get_login_throttle)]
JwksSourceDep = Annotated[JwksSource, Depends(get_jwks_source)]
CodeSenderDep = Annotated[LoginCodeSender, Depends(get_login_code_sender)]


# ---------------------------------------------------------------------
# Contexte de la requête
# ---------------------------------------------------------------------


class RequestContext:
    """Origine d'une requête, réduite à ce qui sert vraiment.

    L'adresse IP est lue pour la clé de limitation et **jamais conservée** :
    ni en base, ni dans les journaux.
    """

    __slots__ = ("ip", "language", "user_agent")

    def __init__(self, *, ip: str, user_agent: str | None, language: Language) -> None:
        self.ip = ip
        self.user_agent = user_agent
        #: Langue de l'en-tête `Accept-Language` : le seul indice pour le
        #: premier code, envoyé à une adresse dont aucun compte ne dépend.
        self.language = language

    def throttle_key(self, subject: str) -> str:
        """Clé de limitation : par cible **et** par origine."""
        return f"{subject.strip().lower()}|{self.ip}"


def get_request_context(request: Request) -> RequestContext:
    """Extrait de la requête ce qui sert à tracer et à limiter."""
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    agent = request.headers.get("user-agent", "")
    return RequestContext(
        ip=client_ip,
        user_agent=agent[:255] or None,
        language=from_accept_language(request.headers.get("accept-language")),
    )


RequestContextDep = Annotated[RequestContext, Depends(get_request_context)]


# ---------------------------------------------------------------------
# Cas d'usage
# ---------------------------------------------------------------------


def get_start_email_login(
    *,
    users: UserRepositoryDep,
    challenges: ChallengeRepositoryDep,
    sender: CodeSenderDep,
    tokens: TokenFactoryDep,
    throttle: ThrottleDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> StartEmailLogin:
    """Assemble l'envoi du premier code."""
    return StartEmailLogin(
        users=users,
        challenges=challenges,
        sender=sender,
        tokens=tokens,
        throttle=throttle,
        clock=clock,
        uow=uow,
    )


def get_resend_email_login_code(
    *,
    challenges: ChallengeRepositoryDep,
    sender: CodeSenderDep,
    tokens: TokenFactoryDep,
    throttle: ThrottleDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
) -> ResendEmailLoginCode:
    """Assemble le renvoi du code."""
    return ResendEmailLoginCode(
        challenges=challenges, sender=sender, tokens=tokens, throttle=throttle, clock=clock, uow=uow
    )


def get_verify_email_login_code(
    *,
    users: UserRepositoryDep,
    challenges: ChallengeRepositoryDep,
    sessions: SessionRepositoryDep,
    tokens: TokenFactoryDep,
    throttle: ThrottleDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
    events: EventBusDep,
) -> VerifyEmailLoginCode:
    """Assemble la validation du code."""
    return VerifyEmailLoginCode(
        users=users,
        challenges=challenges,
        sessions=sessions,
        tokens=tokens,
        throttle=throttle,
        clock=clock,
        uow=uow,
        events=events,
    )


def get_google_login(
    *,
    settings: SettingsDep,
    keys: JwksSourceDep,
    identities: IdentityRepositoryDep,
    users: UserRepositoryDep,
    sessions: SessionRepositoryDep,
    tokens: TokenFactoryDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
    events: EventBusDep,
) -> SocialLogin:
    """Assemble la connexion Google.

    Refuse de se construire sans audience : la route répond « indisponible »
    plutôt que de rejeter chaque jeton comme invalide, ce qui enverrait
    chercher le défaut du côté de l'application.
    """
    if not settings.social.google_enabled:
        raise SocialProviderUnavailableError(SocialProvider.GOOGLE.value)
    return SocialLogin(
        verifier=GoogleTokenVerifier(audiences=settings.social.google_audiences, keys=keys),
        identities=identities,
        users=users,
        sessions=sessions,
        tokens=tokens,
        clock=clock,
        uow=uow,
        events=events,
    )


def get_apple_login(
    *,
    settings: SettingsDep,
    keys: JwksSourceDep,
    identities: IdentityRepositoryDep,
    users: UserRepositoryDep,
    sessions: SessionRepositoryDep,
    tokens: TokenFactoryDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
    events: EventBusDep,
) -> SocialLogin:
    """Assemble la connexion Apple."""
    if not settings.social.apple_enabled:
        raise SocialProviderUnavailableError(SocialProvider.APPLE.value)
    return SocialLogin(
        verifier=AppleTokenVerifier(audiences=settings.social.apple_audiences, keys=keys),
        identities=identities,
        users=users,
        sessions=sessions,
        tokens=tokens,
        clock=clock,
        uow=uow,
        events=events,
    )


def get_refresh_session(
    sessions: SessionRepositoryDep, tokens: TokenFactoryDep, clock: ClockDep, uow: UnitOfWorkDep
) -> RefreshSession:
    """Assemble la rotation des jetons."""
    return RefreshSession(sessions=sessions, tokens=tokens, clock=clock, uow=uow)


def get_resolve_session(
    sessions: SessionRepositoryDep, tokens: TokenFactoryDep, clock: ClockDep
) -> ResolveSession:
    """Assemble la reconnaissance de l'appelant.

    **C'est la dépendance que `bootstrap/wiring.py` branche sur
    `resolve_principal`.** La déclarer ici garde la règle 4 : `shared/auth`
    ne connaît toujours pas `identity`.
    """
    return ResolveSession(sessions=sessions, tokens=tokens, clock=clock)


def get_logout(
    sessions: SessionRepositoryDep, tokens: TokenFactoryDep, clock: ClockDep, uow: UnitOfWorkDep
) -> Logout:
    """Assemble la déconnexion."""
    return Logout(sessions=sessions, tokens=tokens, clock=clock, uow=uow)


def get_current_user_reader(users: UserRepositoryDep) -> GetCurrentUser:
    """Assemble la lecture du compte connecté."""
    return GetCurrentUser(users=users)


def get_delete_account(
    *,
    users: UserRepositoryDep,
    identities: IdentityRepositoryDep,
    sessions: SessionRepositoryDep,
    challenges: ChallengeRepositoryDep,
    departures: DepartureRepositoryDep,
    main_device: MainDeviceLookupDep,
    clock: ClockDep,
    uow: UnitOfWorkDep,
    events: EventBusDep,
) -> DeleteAccount:
    """Assemble la suppression du compte."""
    return DeleteAccount(
        users=users,
        identities=identities,
        sessions=sessions,
        challenges=challenges,
        departures=departures,
        main_device=main_device,
        clock=clock,
        uow=uow,
        events=events,
    )


StartEmailLoginDep = Annotated[StartEmailLogin, Depends(get_start_email_login)]
ResendEmailLoginCodeDep = Annotated[ResendEmailLoginCode, Depends(get_resend_email_login_code)]
VerifyEmailLoginCodeDep = Annotated[VerifyEmailLoginCode, Depends(get_verify_email_login_code)]
GoogleLoginDep = Annotated[SocialLogin, Depends(get_google_login)]
AppleLoginDep = Annotated[SocialLogin, Depends(get_apple_login)]
RefreshSessionDep = Annotated[RefreshSession, Depends(get_refresh_session)]
ResolveSessionDep = Annotated[ResolveSession, Depends(get_resolve_session)]
LogoutDep = Annotated[Logout, Depends(get_logout)]
GetCurrentUserDep = Annotated[GetCurrentUser, Depends(get_current_user_reader)]
DeleteAccountDep = Annotated[DeleteAccount, Depends(get_delete_account)]

__all__ = [
    "AppleLoginDep",
    "ChallengeRepositoryDep",
    "ClockDep",
    "CodeSenderDep",
    "DeleteAccountDep",
    "DepartureRepositoryDep",
    "GetCurrentUserDep",
    "GoogleLoginDep",
    "IdentityRepositoryDep",
    "JwksSourceDep",
    "LogoutDep",
    "MainDeviceLookupDep",
    "RefreshSessionDep",
    "RequestContext",
    "RequestContextDep",
    "ResendEmailLoginCodeDep",
    "ResolveSessionDep",
    "SessionRepositoryDep",
    "StartEmailLoginDep",
    "ThrottleDep",
    "TokenFactoryDep",
    "UserRepositoryDep",
    "VerifyEmailLoginCodeDep",
    "get_challenge_repository",
    "get_clock",
    "get_departure_repository",
    "get_identity_repository",
    "get_jwks_source",
    "get_login_code_sender",
    "get_login_throttle",
    "get_main_device_lookup",
    "get_resolve_session",
    "get_session_repository",
    "get_token_factory",
    "get_user_repository",
]
