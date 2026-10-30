"""Contrat HTTP de l'authentification.

Il reproduit **exactement** ce que `karria_app` attend
(`src/features/auth/api/auth.api.ts`, `auth.types.ts`, et le simulateur
`src/lib/dev/serveur-simule.ts`). Un champ ne s'y renomme pas sans l'app.

Ce que ce contrat ne porte pas, et pourquoi
-------------------------------------------
Ni nom, ni prénom, ni téléphone : un compte est une adresse prouvée. Pas de
`permissions` dans `/auth/me` non plus — il n'existe qu'un seul type
d'utilisateur, et un compte connecté a accès à tout (ADR-0007).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from karria.modules.identity.application import (
    ChallengeStarted,
    ConsentGiven,
    IssuedSession,
    LoginOutcome,
)
from karria.modules.identity.domain import (
    DEPARTURE_DETAILS_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    AiConsentStatus,
    AiPurpose,
    DepartureFeedback,
    User,
    provider_name,
)

#: Longueur maximale d'une version de document contractuel.
_VERSION_MAX = 32
#: Un jeton d'identité Apple ou Google ne dépasse pas quelques kilo-octets.
_TOKEN_MAX = 8192


class _WithConsent(BaseModel):
    """Le consentement voyage avec la première requête.

    Il n'y a plus d'écran où cocher une case : l'écran d'accueil dit « en
    continuant, tu acceptes », et continuer **est** l'acceptation. La preuve
    tient dans ces versions, horodatées par le serveur avec le compte.
    """

    terms_version: str = Field(
        min_length=1, max_length=_VERSION_MAX, description="Version des conditions acceptées."
    )
    privacy_policy_version: str = Field(
        min_length=1,
        max_length=_VERSION_MAX,
        description="Version de la politique de confidentialité acceptée.",
    )

    def consent(self) -> ConsentGiven:
        """Projette le consentement vers la couche application."""
        return ConsentGiven(
            terms_version=self.terms_version, privacy_policy_version=self.privacy_policy_version
        )


# ---------------------------------------------------------------------
# Parcours par code e-mail
# ---------------------------------------------------------------------


class EmailStartRequest(_WithConsent):
    """`POST /auth/email/start`."""

    #: Validée par le domaine, pas par Pydantic : c'est ce qui permet de
    #: répondre `user_email_invalid` sous le champ, et non une erreur de
    #: forme générique.
    email: str = Field(max_length=EMAIL_MAX_LENGTH + 64, description="Adresse à prouver.")


class EmailResendRequest(BaseModel):
    """`POST /auth/email/resend`."""

    challenge_id: UUID


class EmailVerifyRequest(BaseModel):
    """`POST /auth/email/verify`."""

    challenge_id: UUID
    #: Pas de motif à six chiffres imposé ici : un code mal formé est un
    #: code **faux**, et compte comme un essai. Le dire autrement
    #: renseignerait sur la forme attendue sans rien coûter à l'attaquant.
    code: str = Field(min_length=1, max_length=32)


class ChallengeResponse(BaseModel):
    """Un code est parti : de quoi attendre sa saisie.

    `seconds_before_resend` vient du serveur à chaque réponse : le client ne
    connaît jamais le délai, il le lit.
    """

    challenge_id: UUID
    sent_to: str = Field(description="Adresse masquée, pour « code envoyé à … ».")
    expires_in: int = Field(description="Secondes de validité du code.")
    seconds_before_resend: int = Field(description="Secondes avant de pouvoir redemander un code.")

    @classmethod
    def from_result(cls, result: ChallengeStarted) -> ChallengeResponse:
        """Projette le résultat du use case."""
        return cls(
            challenge_id=result.challenge_id,
            sent_to=result.sent_to,
            expires_in=result.expires_in,
            seconds_before_resend=result.seconds_before_resend,
        )


# ---------------------------------------------------------------------
# Connexion par Apple ou Google
# ---------------------------------------------------------------------


class GoogleLoginRequest(_WithConsent):
    """`POST /auth/social/google`."""

    id_token: str = Field(min_length=1, max_length=_TOKEN_MAX)
    #: Le nonce **en clair**, tel que l'app l'a tiré. Google le signe tel
    #: quel ; sans lui, un jeton intercepté se rejoue.
    nonce: str | None = Field(default=None, max_length=256)


class AppleLoginRequest(_WithConsent):
    """`POST /auth/social/apple`."""

    identity_token: str = Field(min_length=1, max_length=_TOKEN_MAX)
    #: Le nonce **en clair** : le serveur le hache lui-même pour le
    #: comparer à celui qu'Apple a scellé dans le jeton.
    nonce: str | None = Field(default=None, max_length=256)


# ---------------------------------------------------------------------
# Session et compte
# ---------------------------------------------------------------------


class SessionTokens(BaseModel):
    """Le couple de jetons, en clair, une seule fois."""

    access_token: str
    refresh_token: str

    @classmethod
    def from_issued(cls, issued: IssuedSession) -> SessionTokens:
        """Projette une session émise."""
        return cls(access_token=issued.access_token, refresh_token=issued.refresh_token)


class UserResponse(BaseModel):
    """Le compte, réduit à ce que l'app affiche."""

    id: UUID
    email: str

    @classmethod
    def from_domain(cls, user: User) -> UserResponse:
        """Projette un compte."""
        return cls(id=user.id, email=user.email.value)


# ---------------------------------------------------------------------
# Consentement à l'IA (ADR-0025)
# ---------------------------------------------------------------------


class AiProviderResponse(BaseModel):
    """Un fournisseur nommé à la personne."""

    id: str = Field(description="L'identifiant du routage : `gemini`, `anthropic`, `openai`…")
    name: str = Field(description="Le nom à afficher : « Google Gemini »…")


class AiDisclosureResponse(BaseModel):
    """Ce que la personne doit accepter aujourd'hui."""

    version: str = Field(
        description=(
            "La version à renvoyer dans `PUT /auth/me/ai-consent`. Change dès qu'un "
            "fournisseur ou un usage entre ou sort : le consentement se redemande alors."
        )
    )
    providers: list[AiProviderResponse] = Field(
        description="Les fournisseurs qui reçoivent des données, d'après le routage réel."
    )
    purposes: list[AiPurpose] = Field(
        description=(
            "Les outils concernés : `assistant`, `transcription`, `cv_creation`, `cv_import`, "
            "`photo_enhancement`, `template_analysis`."
        )
    )


class AiConsentResponse(BaseModel):
    """`GET /auth/me/ai-consent`, et le champ `ai_consent` de `GET /auth/me`."""

    required: bool = Field(
        description="Vrai : montrer l'information et obtenir l'accord avant tout outil d'IA."
    )
    disclosure: AiDisclosureResponse
    accepted_version: str | None = Field(description="La version du dernier accord, ou `null`.")
    accepted_at: datetime | None = Field(description="Quand il a été donné, ou `null`.")
    revoked_at: datetime | None = Field(description="Quand il a été retiré, ou `null`.")

    @classmethod
    def from_status(cls, status: AiConsentStatus) -> AiConsentResponse:
        """Projette l'état."""
        consent = status.consent
        return cls(
            required=status.required,
            disclosure=AiDisclosureResponse(
                version=status.disclosure.version,
                providers=[
                    AiProviderResponse(id=p, name=provider_name(p))
                    for p in status.disclosure.providers
                ],
                purposes=list(status.disclosure.purposes),
            ),
            accepted_version=consent.version if consent else None,
            accepted_at=consent.accepted_at if consent else None,
            revoked_at=consent.revoked_at if consent else None,
        )


class AiConsentAcceptRequest(BaseModel):
    """`PUT /auth/me/ai-consent` : la version lue, et rien d'autre."""

    version: str = Field(
        min_length=1,
        max_length=64,
        description="La `disclosure.version` que la personne vient de lire et d'accepter.",
    )


class MeResponse(UserResponse):
    """`GET /auth/me` : le compte, et où en est son consentement à l'IA."""

    ai_consent: AiConsentResponse

    @classmethod
    def from_user_and_consent(cls, user: User, status: AiConsentStatus) -> MeResponse:
        """Projette le compte et son consentement."""
        return cls(
            id=user.id, email=user.email.value, ai_consent=AiConsentResponse.from_status(status)
        )


class LoginResponse(BaseModel):
    """Réponse commune aux trois chemins de connexion."""

    session: SessionTokens
    user: UserResponse
    created_account: bool = Field(
        description="Vrai à la toute première connexion : l'assistant se présente."
    )

    @classmethod
    def from_outcome(cls, outcome: LoginOutcome) -> LoginResponse:
        """Projette le résultat d'une connexion."""
        return cls(
            session=SessionTokens.from_issued(outcome.session),
            user=UserResponse.from_domain(outcome.user),
            created_account=outcome.created_account,
        )


class RefreshRequest(BaseModel):
    """`POST /auth/refresh` et `POST /auth/logout`."""

    refresh_token: str = Field(min_length=1, max_length=512)


class DeleteAccountRequest(BaseModel):
    """`DELETE /auth/me` : la raison du départ, et un mot si la personne veut.

    Validé par le **domaine**, pas par Pydantic — comme l'adresse à la
    connexion : c'est ce qui permet de répondre les raisons fines du contrat
    (`account_deletion_reason_invalid`, `account_deletion_details_invalid`,
    `account_deletion_details_too_long`) sous le champ fautif. Pydantic ne
    fait que refuser l'absurde (une raison de dix kilo-octets).
    """

    reason: str = Field(
        max_length=64,
        description=(
            "Pourquoi la personne part : `found_job`, `no_matching_offers`, `not_useful`, "
            "`too_many_notifications`, `privacy` ou `other`."
        ),
    )
    details: Any = Field(
        default=None,
        description=(
            f"Mot libre, facultatif : texte ou `null`. {DEPARTURE_DETAILS_MAX_LENGTH} caractères "
            "au plus une fois les blancs ôtés."
        ),
    )

    def feedback(self) -> DepartureFeedback:
        """Projette l'avis vers le domaine, normalisé.

        Raises:
            DepartureReasonInvalidError: La raison n'est pas un code du contrat.
            DepartureDetailsInvalidError: Le mot libre n'est ni texte ni `null`.
            DepartureDetailsTooLongError: Le mot libre dépasse le plafond.
        """
        return DepartureFeedback.parse(reason=self.reason, details=self.details)
