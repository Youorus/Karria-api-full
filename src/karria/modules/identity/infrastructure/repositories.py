"""Dépôts Tortoise du module `identity`.

Le chemin critique de toute l'application
------------------------------------------
`find_by_access_fingerprint` s'exécute à **chaque** requête authentifiée.
Il est servi par l'index unique sur `access_token_hash` : une recherche
exacte, sans jointure, sans tri. C'est le prix assumé des sessions opaques
(ADR-0010) : une lecture indexée par requête, contre une révocation
immédiate.

Ces classes ne rendent jamais un modèle Tortoise : elles traduisent.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from karria.modules.identity.domain import (
    AiConsent,
    AiPurpose,
    Consent,
    DepartureRecord,
    EmailAddress,
    LoginChallenge,
    ProviderIdentity,
    Session,
    SessionEndReason,
    SessionNotFoundError,
    SocialProvider,
    User,
)
from karria.modules.identity.infrastructure.models import (
    AiConsentModel,
    DepartureFeedbackModel,
    LoginChallengeModel,
    ProviderIdentityModel,
    SessionModel,
    UserModel,
)

# ---------------------------------------------------------------------
# Comptes
# ---------------------------------------------------------------------


def _to_user(row: UserModel) -> User:
    return User(
        id=row.id,
        email=EmailAddress(row.email),
        email_verified_at=row.email_verified_at,
        consent=Consent(
            terms_version=row.terms_version,
            privacy_policy_version=row.privacy_policy_version,
            accepted_at=row.consent_accepted_at,
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_login_at=row.last_login_at,
    )


def _user_columns(user: User) -> dict[str, Any]:
    return {
        "email": user.email.value,
        "email_verified_at": user.email_verified_at,
        "terms_version": user.consent.terms_version,
        "privacy_policy_version": user.consent.privacy_policy_version,
        "consent_accepted_at": user.consent.accepted_at,
        "last_login_at": user.last_login_at,
    }


class TortoiseUserRepository:
    """Accès persistant aux comptes."""

    async def find_by_id(self, user_id: UUID) -> User | None:
        """Retourne le compte, ou `None`."""
        row = await UserModel.get_or_none(id=user_id)
        return None if row is None else _to_user(row)

    async def find_by_email(self, email: EmailAddress) -> User | None:
        """Retourne le compte portant cette adresse, ou `None`."""
        row = await UserModel.get_or_none(email=email.value)
        return None if row is None else _to_user(row)

    async def add(self, user: User) -> None:
        """Crée un compte."""
        await UserModel.create(id=user.id, **_user_columns(user))

    async def save(self, user: User) -> None:
        """Écrit l'état complet d'un compte."""
        await UserModel.filter(id=user.id).update(**_user_columns(user))

    async def delete(self, user_id: UUID) -> bool:
        """Efface physiquement le compte ; `False` s'il n'existait plus.

        Les clés étrangères des sessions et des rattachements sont en
        `CASCADE` : c'est un filet, pas le mécanisme — le use case les efface
        explicitement avant, pour que l'intention se lise sans connaître le
        schéma.
        """
        return await UserModel.filter(id=user_id).delete() > 0


# ---------------------------------------------------------------------
# Rattachements Apple / Google
# ---------------------------------------------------------------------


def _to_identity(row: ProviderIdentityModel) -> ProviderIdentity:
    return ProviderIdentity(
        id=row.id,
        user_id=row.user_id,
        provider=SocialProvider(row.provider),
        subject=row.subject,
        email=row.email,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


class TortoiseProviderIdentityRepository:
    """Accès persistant aux rattachements."""

    async def find(self, provider: SocialProvider, subject: str) -> ProviderIdentity | None:
        """Le rattachement de ce `sub`, s'il existe."""
        row = await ProviderIdentityModel.get_or_none(provider=provider, subject=subject)
        return None if row is None else _to_identity(row)

    async def add(self, identity: ProviderIdentity) -> None:
        """Rattache un compte à un fournisseur."""
        await ProviderIdentityModel.create(
            id=identity.id,
            user_id=identity.user_id,
            provider=identity.provider,
            subject=identity.subject,
            email=identity.email,
            last_used_at=identity.last_used_at,
        )

    async def touch(self, identity_id: UUID, at: datetime) -> None:
        """Note que ce rattachement vient de servir."""
        await ProviderIdentityModel.filter(id=identity_id).update(last_used_at=at)

    async def list_for(self, user_id: UUID) -> Sequence[ProviderIdentity]:
        """Les rattachements d'un compte."""
        rows = await ProviderIdentityModel.filter(user_id=user_id).order_by("created_at")
        return [_to_identity(row) for row in rows]

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface tous les rattachements d'un compte."""
        return await ProviderIdentityModel.filter(user_id=user_id).delete()


# ---------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------


def _to_session(row: SessionModel) -> Session:
    return Session(
        id=row.id,
        user_id=row.user_id,
        access_token_hash=row.access_token_hash,
        refresh_token_hash=row.refresh_token_hash,
        previous_refresh_token_hash=row.previous_refresh_token_hash,
        access_expires_at=row.access_expires_at,
        refresh_expires_at=row.refresh_expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        rotation_count=row.rotation_count,
        user_agent=row.user_agent,
        last_used_at=row.last_used_at,
        ended_at=row.ended_at,
        end_reason=row.end_reason,
    )


def _session_columns(session: Session) -> dict[str, Any]:
    return {
        "user_id": session.user_id,
        "access_token_hash": session.access_token_hash,
        "refresh_token_hash": session.refresh_token_hash,
        "previous_refresh_token_hash": session.previous_refresh_token_hash,
        "access_expires_at": session.access_expires_at,
        "refresh_expires_at": session.refresh_expires_at,
        "rotation_count": session.rotation_count,
        "user_agent": session.user_agent,
        "last_used_at": session.last_used_at,
        "ended_at": session.ended_at,
        "end_reason": session.end_reason,
    }


class TortoiseSessionRepository:
    """Accès persistant aux sessions."""

    async def add(self, session: Session) -> None:
        """Ouvre une session."""
        await SessionModel.create(id=session.id, **_session_columns(session))

    async def save(self, session: Session) -> None:
        """Écrit l'état complet d'une session.

        Raises:
            SessionNotFoundError: La session a disparu entre-temps.
        """
        touched = await SessionModel.filter(id=session.id).update(**_session_columns(session))
        if touched == 0:
            raise SessionNotFoundError

    async def find_by_access_fingerprint(self, fingerprint: str) -> Session | None:
        """Retrouve une session par le condensat de son jeton d'accès."""
        row = await SessionModel.get_or_none(access_token_hash=fingerprint)
        return None if row is None else _to_session(row)

    async def find_by_refresh_fingerprint(self, fingerprint: str) -> Session | None:
        """Retrouve la session **active** portant ce jeton de rafraîchissement."""
        row = await SessionModel.get_or_none(refresh_token_hash=fingerprint, ended_at=None)
        return None if row is None else _to_session(row)

    async def find_by_previous_refresh(self, fingerprint: str) -> Session | None:
        """Retrouve une session dont ce jeton a été périmé par rotation."""
        row = await SessionModel.filter(previous_refresh_token_hash=fingerprint).first()
        return None if row is None else _to_session(row)

    async def active_of_user(self, user_id: UUID) -> Sequence[Session]:
        """Les sessions ouvertes, la plus récente d'abord."""
        rows = await SessionModel.filter(user_id=user_id, ended_at=None).order_by("-created_at")
        return [_to_session(row) for row in rows]

    async def end_all_of_user(
        self, user_id: UUID, *, reason: SessionEndReason, at: datetime
    ) -> int:
        """Clôt toutes les sessions d'une personne, en une instruction."""
        return await SessionModel.filter(user_id=user_id, ended_at=None).update(
            end_reason=reason, ended_at=at
        )

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface toutes les sessions d'un compte, ouvertes ou closes."""
        return await SessionModel.filter(user_id=user_id).delete()

    async def purge_ended_before(self, moment: datetime) -> int:
        """Supprime les sessions closes ou périmées avant cet instant."""
        return await SessionModel.filter(refresh_expires_at__lt=moment).delete()


# ---------------------------------------------------------------------
# Parcours de connexion
# ---------------------------------------------------------------------


def _to_challenge(row: LoginChallengeModel) -> LoginChallenge:
    return LoginChallenge(
        id=row.id,
        email=row.email,
        user_id=row.user_id,
        code_hash=row.code_hash,
        status=row.status,
        expires_at=row.expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        code_sent_at=row.code_sent_at,
        code_sends=row.code_sends,
        attempts=row.attempts,
        completed_at=row.completed_at,
        created_account=row.created_account,
        terms_version=row.terms_version,
        privacy_policy_version=row.privacy_policy_version,
    )


def _challenge_columns(challenge: LoginChallenge) -> dict[str, Any]:
    return {
        "email": challenge.email,
        "user_id": challenge.user_id,
        "code_hash": challenge.code_hash,
        "status": challenge.status,
        "expires_at": challenge.expires_at,
        "code_sent_at": challenge.code_sent_at,
        "code_sends": challenge.code_sends,
        "attempts": challenge.attempts,
        "completed_at": challenge.completed_at,
        "created_account": challenge.created_account,
        "terms_version": challenge.terms_version,
        "privacy_policy_version": challenge.privacy_policy_version,
    }


class TortoiseLoginChallengeRepository:
    """Accès persistant aux parcours de connexion."""

    async def add(self, challenge: LoginChallenge) -> None:
        """Ouvre un parcours."""
        await LoginChallengeModel.create(id=challenge.id, **_challenge_columns(challenge))

    async def save(self, challenge: LoginChallenge) -> None:
        """Écrit l'état d'un parcours."""
        await LoginChallengeModel.filter(id=challenge.id).update(**_challenge_columns(challenge))

    async def find(self, challenge_id: UUID) -> LoginChallenge | None:
        """Retourne le parcours, ou `None`."""
        row = await LoginChallengeModel.get_or_none(id=challenge_id)
        return None if row is None else _to_challenge(row)

    async def purge_before(self, moment: datetime) -> int:
        """Supprime les parcours périmés avant cet instant."""
        return await LoginChallengeModel.filter(expires_at__lt=moment).delete()

    async def delete_for_email(self, email: EmailAddress) -> int:
        """Efface tous les parcours de cette adresse."""
        return await LoginChallengeModel.filter(email=email.value).delete()


# ---------------------------------------------------------------------
# Traces de départ
# ---------------------------------------------------------------------


class TortoiseDepartureFeedbackRepository:
    """Consigne les départs. Écriture seule : rien ici ne se relit pour une personne."""

    async def add(self, record: DepartureRecord) -> None:
        """Consigne un départ.

        `created_at` est posé par la base à l'insertion ; `recorded_at` est
        l'instant de l'horloge du use case. Les deux coïncident à la
        milliseconde près, et c'est la date de la base qui fait foi.
        """
        await DepartureFeedbackModel.create(
            reason=record.reason,
            details=record.details,
            account_age_days=record.account_age_days,
            platform=record.platform,
            app_version=record.app_version,
        )


# ---------------------------------------------------------------------
# Consentement à l'IA
# ---------------------------------------------------------------------


def _to_ai_consent(row: AiConsentModel) -> AiConsent:
    return AiConsent(
        user_id=row.user_id,
        version=row.version,
        providers=tuple(row.providers),
        purposes=tuple(AiPurpose(p) for p in row.purposes if p in set(AiPurpose)),
        accepted_at=row.accepted_at,
        revoked_at=row.revoked_at,
    )


class TortoiseAiConsentRepository:
    """Une ligne par compte, remplacée à chaque nouvel accord."""

    async def find(self, user_id: UUID) -> AiConsent | None:
        row = await AiConsentModel.get_or_none(user_id=user_id)
        return _to_ai_consent(row) if row else None

    async def save(self, consent: AiConsent) -> None:
        await AiConsentModel.update_or_create(
            defaults={
                "version": consent.version,
                "providers": list(consent.providers),
                "purposes": [p.value for p in consent.purposes],
                "accepted_at": consent.accepted_at,
                "revoked_at": consent.revoked_at,
            },
            user_id=consent.user_id,
        )
