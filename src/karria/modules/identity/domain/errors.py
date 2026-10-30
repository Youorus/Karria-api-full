"""Erreurs du module `identity`, et les raisons fines que l'app branche.

Contrat
-------
Le socle fixe la **forme** d'une erreur — `{ error: { code, message,
details } }` — et la traduction du `code` en statut HTTP. Ce fichier fixe
ce que `details.reason` porte pour les comptes et la connexion : la raison
**fine**, stable, sur laquelle `karria_app` décide quoi afficher
(`auth_code_invalid` → « code incorrect » sous le champ ;
`auth_code_exhausted` → proposer un nouveau code).

Ces chaînes font partie du contrat de l'application (`serveur-simule.ts`).
Elles se renomment comme une route : jamais sans l'app.

Ce que les messages ne disent jamais
------------------------------------
Rien qui renseigne un attaquant : un parcours inconnu et un parcours
périmé rendent la **même** erreur, un jeton social forgé et un jeton
expiré aussi. Le détail reste dans les journaux.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

# Le sous-module, et non le paquet `karria.shared.errors` : son `__init__`
# charge les gestionnaires HTTP, donc Starlette — ce que le domaine n'a pas
# le droit de voir (règle 2 bis). Le noyau d'exceptions, lui, ne dépend de rien.
from karria.shared.errors.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusinessRuleViolationError,
    DependencyUnavailableError,
    InvalidInputError,
    RateLimitedError,
    ResourceGoneError,
)


class IdentityReason(StrEnum):
    """Raisons stables exposées dans `error.details.reason`."""

    #: Adresse syntaxiquement invalide. `field: "email"`.
    EMAIL_INVALID = "user_email_invalid"
    #: Le parcours de connexion est inconnu, périmé ou déjà clos.
    LOGIN_CHALLENGE_EXPIRED = "auth_login_challenge_expired"
    #: Le délai avant un nouvel envoi court encore. `details.retry_after`.
    LOGIN_CODE_RESEND_TOO_SOON = "auth_login_code_resend_too_soon"
    #: Trop d'envois sur ce parcours : recommencer depuis l'adresse.
    LOGIN_CODE_RESEND_EXHAUSTED = "auth_login_code_resend_exhausted"
    #: Code faux. `field: "code"`.
    CODE_INVALID = "auth_code_invalid"
    #: Trop d'essais sur ce code : il est brûlé. `field: "code"`.
    CODE_EXHAUSTED = "auth_code_exhausted"
    #: Trop de tentatives depuis cette origine. `details.retry_after`.
    TOO_MANY_ATTEMPTS = "auth_too_many_attempts"
    #: Jeton d'accès ou de rafraîchissement inconnu.
    SESSION_NOT_FOUND = "auth_session_not_found"
    #: La session a été close — déconnexion, ou vol détecté.
    SESSION_REVOKED = "auth_session_revoked"
    #: Le jeton de rafraîchissement a dépassé sa fenêtre glissante.
    SESSION_EXPIRED = "auth_session_expired"
    #: Jeton Apple ou Google refusé : signature, émetteur, audience,
    #: expiration ou nonce. Un seul motif, à dessein.
    SOCIAL_TOKEN_INVALID = "auth_social_token_invalid"  # noqa: S105 - une raison, pas un secret
    #: Le fournisseur n'a transmis aucune adresse : impossible d'ouvrir
    #: un compte qu'on ne saurait pas joindre.
    SOCIAL_EMAIL_MISSING = "auth_social_email_missing"
    #: Le fournisseur transmet une adresse sans l'attester : on ne
    #: rattache pas un compte existant sur une simple déclaration.
    SOCIAL_EMAIL_UNVERIFIED = "auth_social_email_unverified"
    #: Le fournisseur n'est pas configuré sur ce serveur.
    SOCIAL_PROVIDER_UNAVAILABLE = "auth_social_provider_unavailable"
    #: Le compte désigné par la session n'existe plus.
    USER_NOT_FOUND = "user_not_found"
    #: La raison de la suppression n'est pas un des six codes. `field: "reason"`.
    DELETION_REASON_INVALID = "account_deletion_reason_invalid"
    #: Le mot libre n'est ni une chaîne ni `null`. `field: "details"`.
    DELETION_DETAILS_INVALID = "account_deletion_details_invalid"
    #: Le mot libre dépasse le plafond. `field: "details"`, `details.max_length`.
    DELETION_DETAILS_TOO_LONG = "account_deletion_details_too_long"
    #: Aucun consentement à l'IA valable pour l'information courante.
    #: `details.version` dit laquelle accepter. Même valeur que
    #: `shared.auth.AI_CONSENT_REQUIRED` : c'est la raison que l'app branche.
    AI_CONSENT_REQUIRED = "ai_consent_required"
    #: L'app a accepté une version qui n'est plus celle du serveur.
    #: `details.expected_version` dit laquelle redemander.
    AI_CONSENT_VERSION_MISMATCH = "ai_consent_version_mismatch"


def _with_reason(
    reason: IdentityReason, *, field: str | None = None, **extra: Any
) -> dict[str, Any]:
    """Compose `details` : la raison, le champ s'il y en a un, le reste."""
    details: dict[str, Any] = {"reason": reason.value}
    if field is not None:
        details["field"] = field
    details.update(extra)
    return details


# ---------------------------------------------------------------------
# Adresse et consentement
# ---------------------------------------------------------------------


class InvalidEmailError(InvalidInputError):
    """L'adresse n'a pas la forme d'une adresse e-mail."""

    reason: ClassVar[IdentityReason] = IdentityReason.EMAIL_INVALID

    def __init__(self) -> None:
        super().__init__(
            "Adresse e-mail invalide.", details=_with_reason(self.reason, field="email")
        )


# ---------------------------------------------------------------------
# Parcours par code e-mail
# ---------------------------------------------------------------------


class LoginChallengeExpiredError(ResourceGoneError):
    """Parcours inconnu, périmé ou déjà clos.

    Un seul message pour les trois cas : distinguer « inconnu » de
    « périmé » dirait à qui tâtonne sur des identifiants lesquels ont
    existé.
    """

    reason: ClassVar[IdentityReason] = IdentityReason.LOGIN_CHALLENGE_EXPIRED

    def __init__(self) -> None:
        super().__init__(
            "Ce parcours de connexion a expiré. Recommencez depuis votre adresse.",
            details=_with_reason(self.reason),
        )


class LoginCodeResendTooSoonError(RateLimitedError):
    """Le délai avant de redemander un code court encore."""

    reason: ClassVar[IdentityReason] = IdentityReason.LOGIN_CODE_RESEND_TOO_SOON

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "Un code vient d'être envoyé. Patientez quelques secondes avant d'en redemander.",
            retry_after=retry_after,
            details=_with_reason(self.reason),
        )


class LoginCodeResendExhaustedError(RateLimitedError):
    """Trop d'envois sur ce parcours : il faut recommencer."""

    reason: ClassVar[IdentityReason] = IdentityReason.LOGIN_CODE_RESEND_EXHAUSTED

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "Trop de codes envoyés pour ce parcours. Recommencez depuis votre adresse.",
            retry_after=retry_after,
            details=_with_reason(self.reason),
        )


class LoginCodeInvalidError(InvalidInputError):
    """Le code présenté n'est pas celui attendu.

    Porte le nombre d'essais restants : l'app peut dire « encore deux
    essais » plutôt que laisser deviner quand le code sera brûlé.
    """

    reason: ClassVar[IdentityReason] = IdentityReason.CODE_INVALID

    def __init__(self, remaining_attempts: int) -> None:
        super().__init__(
            "Code incorrect.",
            details=_with_reason(self.reason, field="code", remaining_attempts=remaining_attempts),
        )


class LoginCodeExhaustedError(RateLimitedError):
    """Trop d'essais sur ce code : il est brûlé, il faut en redemander un."""

    reason: ClassVar[IdentityReason] = IdentityReason.CODE_EXHAUSTED

    def __init__(self) -> None:
        super().__init__(
            "Trop d'essais. Demandez un nouveau code.",
            retry_after=0,
            details=_with_reason(self.reason, field="code"),
        )


class TooManyAttemptsError(RateLimitedError):
    """Trop de tentatives depuis cette origine, sur cette cible."""

    reason: ClassVar[IdentityReason] = IdentityReason.TOO_MANY_ATTEMPTS

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "Trop de tentatives. Réessayez dans quelques minutes.",
            retry_after=retry_after,
            details=_with_reason(self.reason),
        )


# ---------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------


class SessionNotFoundError(AuthenticationError):
    """Le jeton présenté ne correspond à aucune session."""

    reason: ClassVar[IdentityReason] = IdentityReason.SESSION_NOT_FOUND

    def __init__(self) -> None:
        super().__init__("Session inconnue. Reconnectez-vous.", details=_with_reason(self.reason))


class SessionRevokedError(AuthenticationError):
    """La session a été close : déconnexion, ou réutilisation détectée."""

    reason: ClassVar[IdentityReason] = IdentityReason.SESSION_REVOKED

    def __init__(self, end_reason: str | None = None) -> None:
        extra = {"end_reason": end_reason} if end_reason else {}
        super().__init__(
            "Cette session a été fermée. Reconnectez-vous.",
            details=_with_reason(self.reason, **extra),
        )


class SessionExpiredError(AuthenticationError):
    """La fenêtre glissante du jeton de rafraîchissement est dépassée."""

    reason: ClassVar[IdentityReason] = IdentityReason.SESSION_EXPIRED

    def __init__(self) -> None:
        super().__init__("Session expirée. Reconnectez-vous.", details=_with_reason(self.reason))


# ---------------------------------------------------------------------
# Connexion par Apple ou Google
# ---------------------------------------------------------------------


class InvalidSocialTokenError(AuthenticationError):
    """Le jeton du fournisseur n'a pas passé l'un des cinq contrôles.

    Un seul type, sans détail : dire *lequel* a échoué renseignerait qui
    essaie de forger un jeton. Le détail part dans les journaux.
    """

    reason: ClassVar[IdentityReason] = IdentityReason.SOCIAL_TOKEN_INVALID

    def __init__(self) -> None:
        super().__init__(
            "Le justificatif du fournisseur est invalide ou expiré. Réessayez.",
            details=_with_reason(self.reason),
        )


class SocialEmailMissingError(BusinessRuleViolationError):
    """Le fournisseur n'a transmis aucune adresse."""

    reason: ClassVar[IdentityReason] = IdentityReason.SOCIAL_EMAIL_MISSING

    def __init__(self) -> None:
        super().__init__(
            "Le fournisseur n'a pas transmis d'adresse e-mail. Connectez-vous avec votre adresse.",
            details=_with_reason(self.reason),
        )


class SocialEmailUnverifiedError(BusinessRuleViolationError):
    """L'adresse transmise n'est pas attestée par le fournisseur."""

    reason: ClassVar[IdentityReason] = IdentityReason.SOCIAL_EMAIL_UNVERIFIED

    def __init__(self) -> None:
        super().__init__(
            "Cette adresse n'est pas vérifiée chez le fournisseur. "
            "Connectez-vous avec un code envoyé par e-mail.",
            details=_with_reason(self.reason),
        )


class SocialProviderUnavailableError(DependencyUnavailableError):
    """Le fournisseur n'est pas configuré sur ce serveur.

    Dit « indisponible » plutôt que « jeton invalide » : le second message
    enverrait chercher du côté de l'application mobile un défaut qui est
    une variable d'environnement manquante.
    """

    reason: ClassVar[IdentityReason] = IdentityReason.SOCIAL_PROVIDER_UNAVAILABLE

    def __init__(self, provider: str) -> None:
        super().__init__(f"connexion {provider}", reason=self.reason.value)


# ---------------------------------------------------------------------
# Compte
# ---------------------------------------------------------------------


class UserNotFoundError(AuthenticationError):
    """La session désigne un compte qui n'existe plus.

    Rendue comme un défaut d'authentification et non comme un 404 : du
    point de vue de l'app, la session ne vaut plus rien et il faut se
    reconnecter.
    """

    reason: ClassVar[IdentityReason] = IdentityReason.USER_NOT_FOUND

    def __init__(self) -> None:
        super().__init__("Compte introuvable. Reconnectez-vous.", details=_with_reason(self.reason))


class DepartureReasonInvalidError(InvalidInputError):
    """La raison de départ n'est pas un code du contrat."""

    reason: ClassVar[IdentityReason] = IdentityReason.DELETION_REASON_INVALID

    def __init__(self) -> None:
        super().__init__(
            "Raison de suppression inconnue.", details=_with_reason(self.reason, field="reason")
        )


class DepartureDetailsInvalidError(InvalidInputError):
    """Le mot libre n'est ni un texte ni `null`."""

    reason: ClassVar[IdentityReason] = IdentityReason.DELETION_DETAILS_INVALID

    def __init__(self) -> None:
        super().__init__(
            "Le commentaire doit être un texte, ou rester vide.",
            details=_with_reason(self.reason, field="details"),
        )


class DepartureDetailsTooLongError(InvalidInputError):
    """Le mot libre de la suppression dépasse le plafond du contrat."""

    reason: ClassVar[IdentityReason] = IdentityReason.DELETION_DETAILS_TOO_LONG

    def __init__(self, max_length: int) -> None:
        super().__init__(
            f"Le commentaire ne doit pas dépasser {max_length} caractères.",
            details=_with_reason(self.reason, field="details", max_length=max_length),
        )


# ---------------------------------------------------------------------
# Consentement à l'IA (ADR-0025)
# ---------------------------------------------------------------------


class AiConsentRequiredError(AuthorizationError):
    """La personne n'a pas (ou plus) accepté la remise de ses données à un modèle tiers.

    `403` et non `401` : la session est bonne, c'est une permission qui
    manque — et l'app sait la demander, grâce à `details.version`.
    """

    reason: ClassVar[IdentityReason] = IdentityReason.AI_CONSENT_REQUIRED

    def __init__(self, *, version: str, providers: tuple[str, ...]) -> None:
        super().__init__(
            "Avant de continuer, Karria doit t'expliquer ce qu'elle partage avec ses "
            "fournisseurs d'IA.",
            details=_with_reason(self.reason, version=version, providers=list(providers)),
        )


class AiConsentVersionMismatchError(BusinessRuleViolationError):
    """L'accord envoyé vise une autre version que celle en cours : l'app doit la remontrer."""

    reason: ClassVar[IdentityReason] = IdentityReason.AI_CONSENT_VERSION_MISMATCH

    def __init__(self, *, expected_version: str) -> None:
        super().__init__(
            "L'information a changé depuis : relis-la avant d'accepter.",
            details=_with_reason(self.reason, expected_version=expected_version),
        )
