"""Se connecter, rester connecté, se déconnecter — et partir pour de bon.

Trois chemins, une seule sortie
-------------------------------
Code reçu par e-mail, Apple, Google : les trois **prouvent une adresse**,
et tout ce qui suit est identique — retrouver ou créer le compte, consigner
le consentement, ouvrir une session, prévenir le reste de l'application.
Cette suite est écrite une fois (`_AccountResolver`, `_SessionIssuer`) :
écrite trois fois, elle divergerait, et l'oubli le plus probable serait le
consentement.

Le fil conducteur : ne jamais renseigner un attaquant
-----------------------------------------------------
Démarrer une connexion rend **toujours** la même réponse, que l'adresse
corresponde à un compte ou non — un code part dans les deux cas, parce
que c'est la validation du code qui crée le compte. Un parcours inconnu et
un parcours périmé rendent la même erreur. Un jeton social forgé et un
jeton expiré aussi.

Partir : tout s'efface, une trace anonyme reste
-----------------------------------------------
`DeleteAccount` efface le compte **physiquement** — sessions, rattachements,
parcours, ligne du compte — en une transaction, consigne une trace de
départ qui ne désigne personne, puis publie `AccountDeleted` pour que les
autres modules effacent ce qu'ils détiennent (ADR-0015).

Le consentement à l'IA (ADR-0025)
---------------------------------
Quatre intentions sur le même objet : savoir où l'on en est, accepter,
retirer, et **exiger** avant de servir un outil d'IA. L'information
courante se recompose à chaque appel depuis le routage réel des modèles :
c'est ce qui garantit qu'un changement de fournisseur redemande l'accord.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid4

from karria.modules.identity.application.events import (
    AccountDeleted,
    UserRegistered,
    UserSignedIn,
)
from karria.modules.identity.application.ports import (
    AiConsentRepository,
    AiProviderCatalog,
    DepartureFeedbackRepository,
    LoginChallengeRepository,
    LoginCodeSender,
    LoginThrottle,
    MainDeviceLookup,
    ProviderIdentityRepository,
    SessionRepository,
    SocialTokenVerifier,
    TokenFactory,
    UserRepository,
)
from karria.modules.identity.domain import (
    CODE_DIGITS,
    LOGIN_CHALLENGE_LIFETIME,
    MAX_SESSIONS_PER_USER,
    AiConsent,
    AiConsentRequiredError,
    AiConsentStatus,
    AiConsentVersionMismatchError,
    AiDisclosure,
    Consent,
    DepartureFeedback,
    DepartureRecord,
    EmailAddress,
    LoginChallenge,
    LoginChallengeExpiredError,
    LoginCodeExhaustedError,
    LoginCodeInvalidError,
    ProviderIdentity,
    Session,
    SessionEndReason,
    SessionNotFoundError,
    SocialEmailMissingError,
    SocialEmailUnverifiedError,
    User,
    UserNotFoundError,
    VerifiedSocialProfile,
    account_age_in_days,
    open_challenge,
    open_session,
    user_data_tasks,
)
from karria.shared.clock import Clock
from karria.shared.database import UnitOfWork
from karria.shared.events import EventBus
from karria.shared.i18n import DEFAULT_LANGUAGE, Language
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Granularité d'écriture de la date de dernier usage d'une session. Une
#: écriture par requête transformerait chaque lecture en écriture.
_LAST_USED_GRANULARITY: Final = timedelta(minutes=5)


# ---------------------------------------------------------------------
# Ce que les use cases rendent
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsentGiven:
    """Les versions acceptées « en continuant », telles que l'app les envoie."""

    terms_version: str
    privacy_policy_version: str


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Une session fraîchement ouverte, avec ses jetons **en clair**.

    La **seule** structure du module qui porte des jetons lisibles. Elle ne
    franchit la couche API que pour être rendue au client : ni journal, ni
    événement.
    """

    session_id: UUID
    user_id: UUID
    access_token: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """Ce qu'une connexion aboutie rend, quel qu'ait été le chemin."""

    session: IssuedSession
    user: User
    created_account: bool


@dataclass(frozen=True, slots=True)
class ChallengeStarted:
    """Un code est parti ; voici de quoi attendre sa saisie."""

    challenge_id: UUID
    #: L'adresse masquée, pour « code envoyé à m•••@exemple.fr ».
    sent_to: str
    expires_in: int
    seconds_before_resend: int


@dataclass(frozen=True, slots=True)
class AuthenticatedCaller:
    """L'appelant reconnu à partir de son jeton d'accès."""

    user_id: UUID
    session_id: UUID


# ---------------------------------------------------------------------
# Le tronc commun
# ---------------------------------------------------------------------


class _SessionIssuer:
    """Ouvre une session pour un compte déjà authentifié.

    Tirer les jetons, écrire la session, élaguer les plus anciennes :
    l'écrire une fois évite qu'un parcours oublie l'élagage.
    """

    def __init__(
        self, *, sessions: SessionRepository, tokens: TokenFactory, clock: Clock, uow: UnitOfWork
    ) -> None:
        self._sessions = sessions
        self._tokens = tokens
        self._clock = clock
        self._uow = uow

    async def open(self, user: User, *, user_agent: str | None, at: datetime) -> IssuedSession:
        """Ouvre une session et rend ses jetons en clair."""
        access, refresh = self._tokens.new_token(), self._tokens.new_token()
        session = open_session(
            session_id=uuid4(),
            user_id=user.id,
            access_token_hash=self._tokens.fingerprint(access),
            refresh_token_hash=self._tokens.fingerprint(refresh),
            at=at,
            user_agent=user_agent,
        )
        async with self._uow.atomic():
            await self._sessions.add(session)
            await self._prune(user.id, keeping=session.id, at=at)

        logger.info("auth.session_opened", user_id=str(user.id), session_id=str(session.id))
        return IssuedSession(
            session_id=session.id,
            user_id=user.id,
            access_token=access,
            refresh_token=refresh,
        )

    async def _prune(self, user_id: UUID, *, keeping: UUID, at: datetime) -> None:
        """Ferme les sessions les plus anciennes au-delà du plafond."""
        active = await self._sessions.active_of_user(user_id)
        surplus = [s for s in active if s.id != keeping][MAX_SESSIONS_PER_USER - 1 :]
        for session in surplus:
            await self._sessions.save(
                session.ended(reason=SessionEndReason.LOGGED_OUT_EVERYWHERE, at=at)
            )


class _AccountResolver:
    """Retrouve ou crée le compte d'une adresse **prouvée**.

    Appelé uniquement une fois la preuve faite — code validé, jeton signé
    vérifié. C'est lui qui fait qu'une même adresse, par code puis par
    Google, donne le même compte.
    """

    def __init__(self, *, users: UserRepository, uow: UnitOfWork, events: EventBus | None) -> None:
        self._users = users
        self._uow = uow
        self._events = events

    async def resolve(
        self, email: EmailAddress, *, consent: ConsentGiven, at: datetime
    ) -> tuple[User, bool]:
        """Rend `(compte, créé)`, le consentement consigné dans les deux cas.

        Un compte existant voit son consentement **renouvelé** seulement si
        une version a changé : une acceptation identique garde sa date
        d'origine, qui est ce qui en fait une preuve.
        """
        existing = await self._users.find_by_email(email)
        if existing is not None:
            updated = existing.with_consent(
                terms_version=consent.terms_version,
                privacy_policy_version=consent.privacy_policy_version,
                at=at,
            ).signed_in(at=at)
            async with self._uow.atomic():
                await self._users.save(updated)
            return updated, False

        user = User(
            id=uuid4(),
            email=email,
            email_verified_at=at,
            consent=Consent(
                terms_version=consent.terms_version,
                privacy_policy_version=consent.privacy_policy_version,
                accepted_at=at,
            ),
            created_at=at,
            updated_at=at,
            last_login_at=at,
        )
        async with self._uow.atomic():
            await self._users.add(user)
        logger.info("identity.user_registered", user_id=str(user.id))
        if self._events is not None:
            await self._events.publish(UserRegistered(user_id=user.id))
        return user, True


async def _announce(events: EventBus | None, user: User, *, created_account: bool) -> None:
    """Publie `UserSignedIn` — après la session, jamais avant."""
    if events is not None:
        await events.publish(UserSignedIn(user_id=user.id, created_account=created_account))


# ---------------------------------------------------------------------
# Connexion par code e-mail
# ---------------------------------------------------------------------


class _CodeIssuer:
    """Tire un code, l'empreinte, et l'achemine — hors transaction.

    L'écriture en base et l'appel réseau ne partagent pas de transaction :
    un serveur SMTP lent tiendrait sinon un verrou pendant tout l'envoi.
    """

    def __init__(self, *, tokens: TokenFactory, sender: LoginCodeSender) -> None:
        self._tokens = tokens
        self._sender = sender

    def new_code(self) -> tuple[str, str]:
        """Rend `(code en clair, empreinte)`. Le clair ne vit que le temps de l'envoi."""
        code = self._tokens.new_numeric_code(CODE_DIGITS)
        return code, self._tokens.fingerprint(code)

    async def deliver(self, *, email: str, code: str, language: Language) -> None:
        """Achemine le code."""
        await self._sender.send(
            email=email,
            code=code,
            expires_in_minutes=int(LOGIN_CHALLENGE_LIFETIME.total_seconds() // 60),
            language=language,
        )


def _started(challenge: LoginChallenge, email: EmailAddress, at: datetime) -> ChallengeStarted:
    return ChallengeStarted(
        challenge_id=challenge.id,
        sent_to=email.masked,
        expires_in=challenge.seconds_until_expiry(at),
        seconds_before_resend=challenge.seconds_before_resend(at),
    )


class StartEmailLogin:
    """Envoie un code à l'adresse. Compte existant ou non : même réponse."""

    def __init__(
        self,
        *,
        users: UserRepository,
        challenges: LoginChallengeRepository,
        sender: LoginCodeSender,
        tokens: TokenFactory,
        throttle: LoginThrottle,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._users = users
        self._challenges = challenges
        self._codes = _CodeIssuer(tokens=tokens, sender=sender)
        self._throttle = throttle
        self._clock = clock
        self._uow = uow

    async def execute(
        self,
        *,
        email: str,
        consent: ConsentGiven,
        throttle_key: str,
        language: Language = DEFAULT_LANGUAGE,
    ) -> ChallengeStarted:
        """Ouvre un parcours et envoie le code.

        Raises:
            InvalidEmailError: La saisie n'est pas une adresse.
            TooManyAttemptsError: Trop de demandes récentes depuis cette
                origine pour cette adresse. La seule erreur visible — sans
                elle, cette route serait un moyen gratuit d'envoyer des
                courriels à n'importe qui.
        """
        address = EmailAddress.parse(email)
        await self._throttle.ensure_allowed(key=throttle_key)
        await self._throttle.record_failure(key=throttle_key)

        now = self._clock.now()
        user = await self._users.find_by_email(address)
        code, code_hash = self._codes.new_code()
        challenge = open_challenge(
            challenge_id=uuid4(),
            email=address.value,
            user_id=user.id if user else None,
            code_hash=code_hash,
            terms_version=consent.terms_version,
            privacy_policy_version=consent.privacy_policy_version,
            at=now,
        )
        async with self._uow.atomic():
            await self._challenges.add(challenge)

        await self._codes.deliver(email=address.value, code=code, language=language)
        logger.info("auth.login_started", challenge_id=str(challenge.id), known=user is not None)
        return _started(challenge, address, now)


class ResendEmailLoginCode:
    """Renvoie le code sur le **même** parcours.

    Rappeler `start` ouvrirait un nouveau parcours : nouvel identifiant,
    nouveau compteur, et un renvoi illimité. Ici, c'est le parcours qui
    compte les envois et tient le délai — donc lui qui peut dire non.
    """

    def __init__(
        self,
        *,
        challenges: LoginChallengeRepository,
        sender: LoginCodeSender,
        tokens: TokenFactory,
        throttle: LoginThrottle,
        clock: Clock,
        uow: UnitOfWork,
    ) -> None:
        self._challenges = challenges
        self._codes = _CodeIssuer(tokens=tokens, sender=sender)
        self._throttle = throttle
        self._clock = clock
        self._uow = uow

    async def execute(
        self,
        *,
        challenge_id: UUID,
        throttle_key: str,
        language: Language = DEFAULT_LANGUAGE,
    ) -> ChallengeStarted:
        """Émet un nouveau code pour un parcours ouvert.

        Raises:
            TooManyAttemptsError: Trop de demandes depuis cette origine.
            LoginChallengeExpiredError: Parcours inconnu, périmé ou clos.
            LoginCodeResendTooSoonError: Le délai court encore.
            LoginCodeResendExhaustedError: Trop d'envois sur ce parcours.
        """
        await self._throttle.ensure_allowed(key=throttle_key)
        await self._throttle.record_failure(key=throttle_key)

        now = self._clock.now()
        challenge = await self._challenges.find(challenge_id)
        if challenge is None:
            raise LoginChallengeExpiredError
        challenge.ensure_usable_at(now)

        code, code_hash = self._codes.new_code()
        # Le refus tombe ici, avant toute écriture et tout envoi.
        resent = challenge.resent(code_hash=code_hash, at=now)
        async with self._uow.atomic():
            await self._challenges.save(resent)

        address = EmailAddress(resent.email)
        await self._codes.deliver(email=address.value, code=code, language=language)
        logger.info("auth.login_code_resent", challenge_id=str(resent.id), sends=resent.code_sends)
        return _started(resent, address, now)


class VerifyEmailLoginCode:
    """Valide le code : le compte naît s'il n'existe pas, la session s'ouvre."""

    def __init__(
        self,
        *,
        users: UserRepository,
        challenges: LoginChallengeRepository,
        sessions: SessionRepository,
        tokens: TokenFactory,
        throttle: LoginThrottle,
        clock: Clock,
        uow: UnitOfWork,
        events: EventBus | None = None,
    ) -> None:
        self._challenges = challenges
        self._tokens = tokens
        self._throttle = throttle
        self._clock = clock
        self._uow = uow
        self._events = events
        self._accounts = _AccountResolver(users=users, uow=uow, events=events)
        self._issuer = _SessionIssuer(sessions=sessions, tokens=tokens, clock=clock, uow=uow)

    async def execute(
        self,
        *,
        challenge_id: UUID,
        code: str,
        throttle_key: str,
        user_agent: str | None = None,
    ) -> LoginOutcome:
        """Confronte le code au parcours et, s'il est juste, connecte.

        Raises:
            TooManyAttemptsError: Trop d'essais depuis cette origine.
            LoginChallengeExpiredError: Parcours inconnu, périmé ou clos.
            LoginCodeExhaustedError: Le code est brûlé — en redemander un.
            LoginCodeInvalidError: Code faux ; `remaining_attempts` dit
                combien d'essais restent.
        """
        await self._throttle.ensure_allowed(key=throttle_key)

        now = self._clock.now()
        challenge = await self._challenges.find(challenge_id)
        if challenge is None:
            raise LoginChallengeExpiredError
        challenge.ensure_usable_at(now)
        challenge.ensure_attempt_allowed()

        if not self._tokens.matches(code.strip(), challenge.code_hash):
            failed = challenge.failed_attempt(at=now)
            async with self._uow.atomic():
                await self._challenges.save(failed)
            await self._throttle.record_failure(key=throttle_key)
            logger.info(
                "auth.login_code_rejected",
                challenge_id=str(challenge_id),
                remaining=failed.remaining_attempts,
            )
            if failed.remaining_attempts == 0:
                raise LoginCodeExhaustedError
            raise LoginCodeInvalidError(failed.remaining_attempts)

        user, created = await self._accounts.resolve(
            EmailAddress(challenge.email),
            consent=ConsentGiven(
                terms_version=challenge.terms_version,
                privacy_policy_version=challenge.privacy_policy_version,
            ),
            at=now,
        )
        async with self._uow.atomic():
            await self._challenges.save(
                challenge.completed(user_id=user.id, created_account=created, at=now)
            )
        session = await self._issuer.open(user, user_agent=user_agent, at=now)
        await self._throttle.reset(key=throttle_key)
        await _announce(self._events, user, created_account=created)
        logger.info("auth.login_completed", user_id=str(user.id), method="email", created=created)
        return LoginOutcome(session=session, user=user, created_account=created)


# ---------------------------------------------------------------------
# Connexion par Apple ou Google
# ---------------------------------------------------------------------


class SocialLogin:
    """Connecte, rattache ou crée un compte depuis un fournisseur tiers.

    Les trois issues, dans l'ordre où on les cherche :

    1. **le rattachement existe** → c'est cette personne, on ouvre ;
    2. **l'adresse attestée correspond à un compte** → on rattache ;
    3. **rien** → on crée.

    Le `sub` prime sur l'adresse : une adresse change de mains, un `sub`
    est stable et signé. Et Apple ne renvoie l'adresse qu'une fois.
    """

    def __init__(
        self,
        *,
        verifier: SocialTokenVerifier,
        identities: ProviderIdentityRepository,
        users: UserRepository,
        sessions: SessionRepository,
        tokens: TokenFactory,
        clock: Clock,
        uow: UnitOfWork,
        events: EventBus | None = None,
    ) -> None:
        self._verifier = verifier
        self._identities = identities
        self._users = users
        self._clock = clock
        self._uow = uow
        self._events = events
        self._accounts = _AccountResolver(users=users, uow=uow, events=events)
        self._issuer = _SessionIssuer(sessions=sessions, tokens=tokens, clock=clock, uow=uow)

    async def execute(
        self,
        *,
        token: str,
        nonce: str | None,
        consent: ConsentGiven,
        user_agent: str | None = None,
    ) -> LoginOutcome:
        """Vérifie, résout le compte, ouvre la session.

        Raises:
            InvalidSocialTokenError: Jeton refusé.
            SocialEmailMissingError: Première venue sans adresse.
            SocialEmailUnverifiedError: Adresse non attestée par le
                fournisseur — on ne crée ni ne rattache sur une déclaration.
        """
        profile = await self._verifier.verify(token, nonce=nonce)
        now = self._clock.now()

        identity = await self._identities.find(profile.provider, profile.subject)
        if identity is not None:
            user = await self._users.find_by_id(identity.user_id)
            if user is not None:
                return await self._returning(
                    identity, user, consent=consent, now=now, ua=user_agent
                )
            # Le compte a disparu mais le rattachement subsiste : première
            # venue plutôt qu'un échec que la personne ne pourrait corriger.
            logger.warning("social.orphan_identity", identity_id=str(identity.id))

        return await self._first_time(profile, consent=consent, now=now, ua=user_agent)

    async def _returning(
        self,
        identity: ProviderIdentity,
        user: User,
        *,
        consent: ConsentGiven,
        now: datetime,
        ua: str | None,
    ) -> LoginOutcome:
        updated = user.with_consent(
            terms_version=consent.terms_version,
            privacy_policy_version=consent.privacy_policy_version,
            at=now,
        ).signed_in(at=now)
        async with self._uow.atomic():
            await self._users.save(updated)
            await self._identities.touch(identity.id, now)
        session = await self._issuer.open(updated, user_agent=ua, at=now)
        await _announce(self._events, updated, created_account=False)
        logger.info("social.signed_in", provider=identity.provider.value, created=False)
        return LoginOutcome(session=session, user=updated, created_account=False)

    async def _first_time(
        self,
        profile: VerifiedSocialProfile,
        *,
        consent: ConsentGiven,
        now: datetime,
        ua: str | None,
    ) -> LoginOutcome:
        if profile.email is None:
            # Un compte sans adresse ne peut pas être retrouvé au prochain
            # appareil, ni par code e-mail. Le domaine l'interdit.
            raise SocialEmailMissingError
        if not profile.email_verified:
            # Un compte est une adresse **prouvée**. Une adresse seulement
            # déclarée chez le fournisseur ne prouve rien — et rattacher
            # dessus donnerait le compte d'autrui au premier qui la déclare.
            raise SocialEmailUnverifiedError

        user, created = await self._accounts.resolve(
            EmailAddress.parse(profile.email), consent=consent, at=now
        )
        async with self._uow.atomic():
            await self._identities.add(
                ProviderIdentity(
                    id=uuid4(),
                    user_id=user.id,
                    provider=profile.provider,
                    subject=profile.subject,
                    email=profile.email,
                    created_at=now,
                    last_used_at=now,
                )
            )
        session = await self._issuer.open(user, user_agent=ua, at=now)
        await _announce(self._events, user, created_account=created)
        logger.info("social.signed_in", provider=profile.provider.value, created=created)
        return LoginOutcome(session=session, user=user, created_account=created)


# ---------------------------------------------------------------------
# La vie d'une session
# ---------------------------------------------------------------------


class RefreshSession:
    """Échange un jeton de rafraîchissement contre un couple neuf.

    C'est ici que se détecte le vol de session — voir `_handle_possible_reuse`.
    """

    def __init__(
        self, *, sessions: SessionRepository, tokens: TokenFactory, clock: Clock, uow: UnitOfWork
    ) -> None:
        self._sessions = sessions
        self._tokens = tokens
        self._clock = clock
        self._uow = uow

    async def execute(self, refresh_token: str) -> IssuedSession:
        """Fait tourner les jetons de la session.

        Raises:
            SessionNotFoundError: Jeton inconnu — y compris déjà consommé.
            SessionRevokedError: Session close.
            SessionExpiredError: Fenêtre glissante dépassée.
        """
        fingerprint = self._tokens.fingerprint(refresh_token)
        now = self._clock.now()

        session = await self._sessions.find_by_refresh_fingerprint(fingerprint)
        if session is None:
            await self._handle_possible_reuse(fingerprint, now)
            raise SessionNotFoundError

        session.ensure_refreshable_at(now)

        access, refresh = self._tokens.new_token(), self._tokens.new_token()
        rotated = session.rotated(
            access_token_hash=self._tokens.fingerprint(access),
            refresh_token_hash=self._tokens.fingerprint(refresh),
            at=now,
        )
        async with self._uow.atomic():
            await self._sessions.save(rotated)

        logger.info(
            "auth.session_refreshed", session_id=str(session.id), rotation=rotated.rotation_count
        )
        return IssuedSession(
            session_id=rotated.id,
            user_id=rotated.user_id,
            access_token=access,
            refresh_token=refresh,
        )

    async def _handle_possible_reuse(self, fingerprint: str, now: datetime) -> None:
        """Coupe toutes les sessions si un jeton déjà consommé ressert.

        Deux acteurs détiennent alors la même session ; on ne sait pas
        lequel est le voleur, et se tromper coûterait le compte : on ferme
        tout. **Sauf dans la minute qui suit la rotation** : un doublon
        réseau n'est pas un vol. Le rejeu est refusé quand même.
        """
        stale = await self._sessions.find_by_previous_refresh(fingerprint)
        if stale is None:
            return
        if stale.reuse_is_benign_at(now):
            logger.info("auth.refresh_token_reuse_tolerated", session_id=str(stale.id))
            return
        async with self._uow.atomic():
            closed = await self._sessions.end_all_of_user(
                stale.user_id, reason=SessionEndReason.TOKEN_REUSE_DETECTED, at=now
            )
        logger.warning(
            "auth.refresh_token_reuse_detected",
            user_id=str(stale.user_id),
            sessions_closed=closed,
        )


class ResolveSession:
    """Reconnaît l'appelant à partir de son jeton d'accès.

    Appelé à **chaque** requête authentifiée : une lecture indexée sur le
    condensat, et rien d'autre.
    """

    def __init__(self, *, sessions: SessionRepository, tokens: TokenFactory, clock: Clock) -> None:
        self._sessions = sessions
        self._tokens = tokens
        self._clock = clock

    async def execute(self, access_token: str) -> AuthenticatedCaller | None:
        """Retourne l'appelant, ou `None` si le jeton ne vaut rien.

        `None` plutôt qu'une exception : certaines routes sont publiques et
        tolèrent un jeton périmé. C'est `CurrentPrincipalDep` qui exige.
        """
        session = await self._sessions.find_by_access_fingerprint(
            self._tokens.fingerprint(access_token)
        )
        now = self._clock.now()
        if session is None or not session.access_is_valid_at(now):
            return None
        if session.should_record_use(now, granularity=_LAST_USED_GRANULARITY):
            await self._sessions.save(session.touched(at=now))
        return AuthenticatedCaller(user_id=session.user_id, session_id=session.id)


class Logout:
    """Ferme la session portant ce jeton de rafraîchissement.

    Silencieux si elle n'existe pas : se déconnecter deux fois n'est pas
    une erreur. Ne touche **pas** aux autres sessions ni aux appareils —
    l'app détache elle-même son appareil (`DELETE /devices/{id}`).
    """

    def __init__(
        self, *, sessions: SessionRepository, tokens: TokenFactory, clock: Clock, uow: UnitOfWork
    ) -> None:
        self._sessions = sessions
        self._tokens = tokens
        self._clock = clock
        self._uow = uow

    async def execute(self, refresh_token: str) -> None:
        """Ferme la session, si elle est ouverte."""
        session: Session | None = await self._sessions.find_by_refresh_fingerprint(
            self._tokens.fingerprint(refresh_token)
        )
        if session is None:
            return
        async with self._uow.atomic():
            await self._sessions.save(
                session.ended(reason=SessionEndReason.LOGGED_OUT, at=self._clock.now())
            )
        logger.info("auth.logged_out", session_id=str(session.id))


class GetCurrentUser:
    """Rend le compte connecté — `GET /auth/me`."""

    def __init__(self, *, users: UserRepository) -> None:
        self._users = users

    async def execute(self, user_id: UUID) -> User:
        """Retourne le compte.

        Raises:
            UserNotFoundError: La session désigne un compte disparu.
        """
        user = await self._users.find_by_id(user_id)
        if user is None:
            raise UserNotFoundError
        return user


# ---------------------------------------------------------------------
# La fin d'un compte
# ---------------------------------------------------------------------


class DeleteAccount:
    """Supprime le compte connecté — `DELETE /auth/me`.

    L'ordre des opérations est le cœur de la garantie :

    1. **lire** ce qui servira à la trace (âge du compte, appareil
       principal) pendant que les données existent encore ;
    2. **effacer** sessions, rattachements, parcours et compte, et écrire la
       trace anonyme, **dans une seule transaction** : à la validation, la
       personne n'a plus de session, plus d'adresse chez nous, et l'équipe
       produit a sa raison. Rien d'intermédiaire n'est observable ;
    3. **publier** `AccountDeleted` — après la validation, jamais avant :
       un abonné qui efface un CV ne doit pas le faire pour un compte que la
       transaction aurait finalement gardé.

    La suppression est physique : l'adresse ne subsiste nulle part dans ce
    module, et se réinscrire avec elle crée un compte neuf (`created_account`
    vrai). Ce que la trace retient est délibérément pauvre — voir
    `DepartureRecord`.
    """

    def __init__(
        self,
        *,
        users: UserRepository,
        identities: ProviderIdentityRepository,
        sessions: SessionRepository,
        challenges: LoginChallengeRepository,
        departures: DepartureFeedbackRepository,
        main_device: MainDeviceLookup,
        clock: Clock,
        uow: UnitOfWork,
        events: EventBus | None = None,
    ) -> None:
        self._users = users
        self._identities = identities
        self._sessions = sessions
        self._challenges = challenges
        self._departures = departures
        self._main_device = main_device
        self._clock = clock
        self._uow = uow
        self._events = events

    async def execute(self, *, user_id: UUID, feedback: DepartureFeedback) -> None:
        """Efface le compte et consigne le départ.

        Raises:
            UserNotFoundError: La session désigne un compte déjà disparu.
        """
        user = await self._users.find_by_id(user_id)
        if user is None:
            raise UserNotFoundError

        now = self._clock.now()
        device = await self._main_device.main_device_of(user_id)
        record = DepartureRecord(
            reason=feedback.reason,
            details=feedback.details,
            account_age_days=account_age_in_days(created_at=user.created_at, at=now),
            platform=device.platform if device else None,
            app_version=device.app_version if device else None,
            recorded_at=now,
        )

        async with self._uow.atomic():
            sessions = await self._sessions.delete_all_of_user(user_id)
            identities = await self._identities.delete_all_of_user(user_id)
            challenges = await self._challenges.delete_for_email(user.email)
            await self._users.delete(user_id)
            await self._departures.add(record)

        # Le journal porte l'identifiant — c'est la seule trace qui le relie
        # au départ, et elle sert au support (« j'ai supprimé par erreur »).
        # La raison n'y est pas : elle ne regarde que la trace anonyme.
        logger.info(
            "identity.account_deleted",
            user_id=str(user_id),
            sessions=sessions,
            identities=identities,
            challenges=challenges,
            account_age_days=record.account_age_days,
        )
        if self._events is not None:
            await self._events.publish(AccountDeleted(user_id=user_id))


# ---------------------------------------------------------------------
# Le consentement à l'IA (ADR-0025)
# ---------------------------------------------------------------------


class _AiConsentBase:
    """Le tronc commun : composer l'information courante, lire l'état de la personne."""

    def __init__(self, *, consents: AiConsentRepository, catalog: AiProviderCatalog) -> None:
        self._consents = consents
        self._catalog = catalog

    def disclosure(self) -> AiDisclosure:
        """L'information d'aujourd'hui, depuis le routage réel des modèles."""
        return AiDisclosure.build(self._catalog.providers_for(user_data_tasks()))

    async def status_of(self, user_id: UUID) -> AiConsentStatus:
        """Où en est la personne."""
        return AiConsentStatus(
            disclosure=self.disclosure(), consent=await self._consents.find(user_id)
        )


class GetAiConsentStatus(_AiConsentBase):
    """`GET /auth/me/ai-consent`, et le champ `ai_consent` de `GET /auth/me`."""

    async def execute(self, user_id: UUID) -> AiConsentStatus:
        """L'état courant."""
        return await self.status_of(user_id)


class AcceptAiConsent(_AiConsentBase):
    """`PUT /auth/me/ai-consent` : la personne accepte l'information **qu'elle a lue**."""

    def __init__(
        self, *, consents: AiConsentRepository, catalog: AiProviderCatalog, clock: Clock
    ) -> None:
        super().__init__(consents=consents, catalog=catalog)
        self._clock = clock

    async def execute(self, user_id: UUID, *, version: str) -> AiConsentStatus:
        """Enregistre l'accord si la version est celle en cours.

        Une acceptation identique et toujours valable n'est pas réécrite :
        sa date d'origine est ce qui en fait une preuve.

        Raises:
            AiConsentVersionMismatchError: L'app a accepté une version périmée.
        """
        disclosure = self.disclosure()
        if version != disclosure.version:
            raise AiConsentVersionMismatchError(expected_version=disclosure.version)
        existing = await self._consents.find(user_id)
        if existing is not None and existing.covers(disclosure):
            return AiConsentStatus(disclosure=disclosure, consent=existing)
        consent = AiConsent.given(user_id, disclosure, at=self._clock.now())
        await self._consents.save(consent)
        logger.info(
            "identity.ai_consent_given",
            user_id=str(user_id),
            version=consent.version,
            providers=list(consent.providers),
        )
        return AiConsentStatus(disclosure=disclosure, consent=consent)


class RevokeAiConsent(_AiConsentBase):
    """`DELETE /auth/me/ai-consent` : la personne retire son accord ; la trace reste."""

    def __init__(
        self, *, consents: AiConsentRepository, catalog: AiProviderCatalog, clock: Clock
    ) -> None:
        super().__init__(consents=consents, catalog=catalog)
        self._clock = clock

    async def execute(self, user_id: UUID) -> AiConsentStatus:
        """Marque le retrait. Sans accord préalable, rien à faire : l'état est déjà « requis »."""
        disclosure = self.disclosure()
        existing = await self._consents.find(user_id)
        if existing is None or not existing.active:
            return AiConsentStatus(disclosure=disclosure, consent=existing)
        revoked = existing.revoked(at=self._clock.now())
        await self._consents.save(revoked)
        logger.info("identity.ai_consent_revoked", user_id=str(user_id), version=revoked.version)
        return AiConsentStatus(disclosure=disclosure, consent=revoked)


class EnsureAiConsent(_AiConsentBase):
    """Ce que `bootstrap/wiring.py` branche sur la porte d'IA du socle."""

    async def execute(self, user_id: UUID) -> None:
        """Ne rend rien si l'accord vaut.

        Raises:
            AiConsentRequiredError: Aucun accord valable pour l'information courante.
        """
        status = await self.status_of(user_id)
        if status.required:
            logger.info("identity.ai_consent_missing", user_id=str(user_id))
            raise AiConsentRequiredError(
                version=status.disclosure.version, providers=status.disclosure.providers
            )
