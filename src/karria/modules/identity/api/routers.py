"""Routes d'authentification — le contrat de `karria_app`, à l'identique.

Ce que ces routes ne font jamais
--------------------------------
- **Elles ne disent pas si un compte existe.** `/auth/email/start` répond
  identiquement, que l'adresse soit inscrite ou non ; un code part dans les
  deux cas, parce que c'est la validation qui crée le compte.
- **Elles ne journalisent aucun secret** : ni code, ni jeton.
- **Elles ne posent aucun cookie.** L'app est mobile : elle reçoit les
  jetons dans le corps et les range dans le trousseau du système.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from karria.modules.identity.api.dependencies import (
    AcceptAiConsentDep,
    AppleLoginDep,
    DeleteAccountDep,
    GetAiConsentStatusDep,
    GetCurrentUserDep,
    GoogleLoginDep,
    LogoutDep,
    RefreshSessionDep,
    RequestContextDep,
    ResendEmailLoginCodeDep,
    RevokeAiConsentDep,
    StartEmailLoginDep,
    VerifyEmailLoginCodeDep,
)
from karria.modules.identity.api.schemas import (
    AiConsentAcceptRequest,
    AiConsentResponse,
    AppleLoginRequest,
    ChallengeResponse,
    DeleteAccountRequest,
    EmailResendRequest,
    EmailStartRequest,
    EmailVerifyRequest,
    GoogleLoginRequest,
    LoginResponse,
    MeResponse,
    RefreshRequest,
    SessionTokens,
)
from karria.shared.auth import CurrentPrincipalDep
from karria.shared.errors import ErrorCode, error_responses

router = APIRouter(prefix="/auth", tags=["Authentification"])


# ---------------------------------------------------------------------
# Code par e-mail
# ---------------------------------------------------------------------


@router.post(
    "/email/start",
    response_model=ChallengeResponse,
    summary="Recevoir un code de connexion par e-mail",
    description=(
        "Ouvre un parcours et envoie un code à six chiffres à l'adresse. "
        "Compte existant ou non, la réponse est **identique** : c'est la "
        "validation du code qui crée le compte s'il n'existe pas.\n\n"
        "Le consentement (`terms_version`, `privacy_policy_version`) voyage "
        "ici et sera horodaté sur le compte à la validation.\n\n"
        "Erreurs : `user_email_invalid` (422, `field: email`), "
        "`auth_too_many_attempts` (429, `retry_after`)."
    ),
    responses=error_responses(ErrorCode.INVALID_INPUT, ErrorCode.RATE_LIMITED),
)
async def start_email_login(
    body: EmailStartRequest, context: RequestContextDep, start: StartEmailLoginDep
) -> ChallengeResponse:
    """Envoie le code."""
    return ChallengeResponse.from_result(
        await start.execute(
            email=body.email,
            consent=body.consent(),
            throttle_key=context.throttle_key(body.email),
            language=context.language,
        )
    )


@router.post(
    "/email/resend",
    response_model=ChallengeResponse,
    summary="Redemander le code",
    description=(
        "Émet un nouveau code sur le **même** parcours ; le précédent est "
        "périmé. Un délai sépare deux demandes — `seconds_before_resend` le "
        "donne à chaque réponse.\n\n"
        "Erreurs : `auth_login_challenge_expired` (410), "
        "`auth_login_code_resend_too_soon` (429), "
        "`auth_login_code_resend_exhausted` (429), `auth_too_many_attempts` (429)."
    ),
    responses=error_responses(ErrorCode.GONE, ErrorCode.RATE_LIMITED),
)
async def resend_email_login_code(
    body: EmailResendRequest, context: RequestContextDep, resend: ResendEmailLoginCodeDep
) -> ChallengeResponse:
    """Renvoie le code."""
    return ChallengeResponse.from_result(
        await resend.execute(
            challenge_id=body.challenge_id,
            throttle_key=context.throttle_key(str(body.challenge_id)),
            language=context.language,
        )
    )


@router.post(
    "/email/verify",
    response_model=LoginResponse,
    summary="Valider le code et se connecter",
    description=(
        "Confronte le code au parcours. S'il est juste : le compte est "
        "retrouvé ou **créé** (`created_account`), le consentement consigné, "
        "la session ouverte.\n\n"
        "Erreurs : `auth_code_invalid` (422, `field: code`, "
        "`remaining_attempts`), `auth_code_exhausted` (429, `field: code`), "
        "`auth_login_challenge_expired` (410), `auth_too_many_attempts` (429)."
    ),
    responses=error_responses(ErrorCode.INVALID_INPUT, ErrorCode.GONE, ErrorCode.RATE_LIMITED),
)
async def verify_email_login_code(
    body: EmailVerifyRequest, context: RequestContextDep, verify: VerifyEmailLoginCodeDep
) -> LoginResponse:
    """Valide le code et ouvre la session."""
    return LoginResponse.from_outcome(
        await verify.execute(
            challenge_id=body.challenge_id,
            code=body.code,
            throttle_key=context.throttle_key(str(body.challenge_id)),
            user_agent=context.user_agent,
        )
    )


# ---------------------------------------------------------------------
# Apple et Google
# ---------------------------------------------------------------------


@router.post(
    "/social/google",
    response_model=LoginResponse,
    summary="Se connecter avec Google",
    description=(
        "Vérifie l'`id_token` (signature, émetteur, audience, expiration, "
        "nonce), retrouve ou crée le compte, ouvre la session. Aucun code "
        "n'est envoyé : Google vient d'attester l'adresse.\n\n"
        "Erreurs : `auth_social_token_invalid` (401), "
        "`auth_social_email_unverified` (409), `auth_social_email_missing` (409), "
        "`auth_social_provider_unavailable` (503)."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.RULE_VIOLATION, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def login_with_google(
    body: GoogleLoginRequest, context: RequestContextDep, login: GoogleLoginDep
) -> LoginResponse:
    """Ouvre une session depuis un jeton Google vérifié."""
    return LoginResponse.from_outcome(
        await login.execute(
            token=body.id_token,
            nonce=body.nonce,
            consent=body.consent(),
            user_agent=context.user_agent,
        )
    )


@router.post(
    "/social/apple",
    response_model=LoginResponse,
    summary="Se connecter avec Apple",
    description=(
        "Vérifie l'`identity_token` d'Apple. Le `nonce` est celui **en "
        "clair** : le serveur le hache (SHA-256) pour le comparer à celui "
        "qu'Apple a scellé.\n\n"
        "Apple ne transmet l'adresse — souvent un relais "
        "`@privaterelay.appleid.com` — qu'à la **première** connexion ; les "
        "suivantes sont reconnues par le `sub`.\n\n"
        "Erreurs : `auth_social_token_invalid` (401), "
        "`auth_social_email_missing` (409), `auth_social_provider_unavailable` (503)."
    ),
    responses=error_responses(
        ErrorCode.UNAUTHENTICATED, ErrorCode.RULE_VIOLATION, ErrorCode.DEPENDENCY_FAILURE
    ),
)
async def login_with_apple(
    body: AppleLoginRequest, context: RequestContextDep, login: AppleLoginDep
) -> LoginResponse:
    """Ouvre une session depuis un jeton Apple vérifié."""
    return LoginResponse.from_outcome(
        await login.execute(
            token=body.identity_token,
            nonce=body.nonce,
            consent=body.consent(),
            user_agent=context.user_agent,
        )
    )


# ---------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=SessionTokens,
    summary="Rafraîchir la session",
    description=(
        "Échange le jeton de rafraîchissement contre un couple neuf. Le "
        "jeton **tourne** : l'ancien devient inutilisable. Présenter un "
        "jeton déjà consommé hors de la minute qui suit révèle un vol et "
        "**ferme toutes les sessions** du compte.\n\n"
        "Erreurs (401) : `auth_session_not_found`, `auth_session_revoked`, "
        "`auth_session_expired`."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def refresh(body: RefreshRequest, refresh_uc: RefreshSessionDep) -> SessionTokens:
    """Fait tourner les jetons."""
    return SessionTokens.from_issued(await refresh_uc.execute(body.refresh_token))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Se déconnecter",
    description=(
        "Ferme la session portant ce jeton de rafraîchissement. Silencieux "
        "si elle n'existe plus : se déconnecter deux fois n'est pas une "
        "erreur. Les autres sessions et les appareils ne sont pas touchés — "
        "l'app détache elle-même son appareil (`DELETE /devices/{id}`)."
    ),
)
async def logout(body: RefreshRequest, logout_uc: LogoutDep) -> None:
    """Ferme la session."""
    await logout_uc.execute(body.refresh_token)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Qui suis-je ?",
    description=(
        "Le compte connecté : son identifiant, son adresse, et où en est son consentement "
        "à l'IA (`ai_consent`, même forme que `GET /auth/me/ai-consent`). Rien d'autre."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def me(
    principal: CurrentPrincipalDep, reader: GetCurrentUserDep, consent: GetAiConsentStatusDep
) -> MeResponse:
    """Retourne le compte connecté et son consentement à l'IA."""
    user = await reader.execute(principal.subject_id)
    return MeResponse.from_user_and_consent(user, await consent.execute(principal.subject_id))


# ---------------------------------------------------------------------
# Consentement à l'IA (ADR-0025)
# ---------------------------------------------------------------------


@router.get(
    "/me/ai-consent",
    response_model=AiConsentResponse,
    summary="Mon consentement à l'IA",
    description=(
        "Ce que la personne doit accepter **avant la première utilisation** d'un outil d'IA "
        "(parler à Karria, dicter, créer ou importer un CV, améliorer une photo, expliquer "
        "un score ATS) : la `disclosure` courante — version, fournisseurs réellement routés, "
        "usages — et l'état de son accord. `required: true` tant qu'aucun accord ne couvre "
        "la version courante.\n\n"
        "Les routes d'IA refusent avec `403 forbidden` et "
        "`details.reason = ai_consent_required` (`details.version` dit quoi accepter) : "
        "l'app ne peut pas contourner cette porte."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def read_ai_consent(
    principal: CurrentPrincipalDep, status_uc: GetAiConsentStatusDep
) -> AiConsentResponse:
    """L'état courant."""
    return AiConsentResponse.from_status(await status_uc.execute(principal.subject_id))


@router.put(
    "/me/ai-consent",
    response_model=AiConsentResponse,
    summary="Accepter l'information sur l'IA",
    description=(
        "Enregistre l'accord pour la `version` lue, horodaté par le serveur, avec la liste "
        "des fournisseurs montrés. Une acceptation déjà valable n'est pas réécrite.\n\n"
        "Erreurs : `ai_consent_version_mismatch` (409, `details.expected_version`) si "
        "l'information a changé entre la lecture et l'acceptation — la remontrer."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.RULE_VIOLATION),
)
async def accept_ai_consent(
    body: AiConsentAcceptRequest, principal: CurrentPrincipalDep, accept: AcceptAiConsentDep
) -> AiConsentResponse:
    """Consigne l'accord."""
    return AiConsentResponse.from_status(
        await accept.execute(principal.subject_id, version=body.version)
    )


@router.delete(
    "/me/ai-consent",
    response_model=AiConsentResponse,
    summary="Retirer mon consentement à l'IA",
    description=(
        "Marque le retrait (la trace de l'accord et sa date restent). Dès lors, "
        "`required` repasse à vrai et les outils d'IA redemandent l'accord. "
        "Silencieux s'il n'y avait rien à retirer."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED),
)
async def revoke_ai_consent(
    principal: CurrentPrincipalDep, revoke: RevokeAiConsentDep
) -> AiConsentResponse:
    """Retire l'accord."""
    return AiConsentResponse.from_status(await revoke.execute(principal.subject_id))


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer mon compte",
    description=(
        "Efface le compte connecté **pour de bon** : toutes ses sessions, ses "
        "rattachements Apple/Google, ses parcours de connexion et la ligne du "
        "compte, en une transaction. Les autres modules effacent ensuite ce "
        "qu'ils détiennent de la personne (CV, brouillon, photos, appareils).\n\n"
        "Seule survit une trace **anonyme** du départ — la raison, le mot libre "
        "(expurgé des adresses et numéros qu'il contiendrait), l'âge du compte, "
        "la plateforme — sans identifiant ni adresse. La même adresse peut se "
        "réinscrire : ce sera un compte neuf.\n\n"
        "Le corps porte `reason` (un des six codes du contrat) et `details` "
        "(texte libre ou `null`, 1 000 caractères au plus). Aucun `logout` à "
        "faire ensuite : il ne reste rien à fermer.\n\n"
        "Erreurs (422, `invalid_input`) : `account_deletion_reason_invalid` "
        "(`field: reason`), `account_deletion_details_invalid` (`field: details`), "
        "`account_deletion_details_too_long` (`field: details`, `max_length`). "
        "`user_not_found` (401) : la session désignait un compte déjà parti."
    ),
    responses=error_responses(ErrorCode.UNAUTHENTICATED, ErrorCode.INVALID_INPUT),
)
async def delete_me(
    body: DeleteAccountRequest, principal: CurrentPrincipalDep, delete: DeleteAccountDep
) -> None:
    """Supprime le compte connecté."""
    await delete.execute(user_id=principal.subject_id, feedback=body.feedback())
