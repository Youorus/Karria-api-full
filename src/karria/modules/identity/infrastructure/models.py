"""Modèles de persistance du module `identity`.

Ce sont des **modèles de persistance**, pas des entités : ils décrivent
comment une donnée est stockée. Ils ne franchissent jamais la frontière du
module — les repositories traduisent en objets du domaine (AGENTS.md §6.10).

Ce que ces tables ne contiennent pas
------------------------------------
Aucun jeton, aucun code en clair : des condensats. Aucune adresse IP. Aucun
nom, prénom, téléphone : le contrat de l'app ne les collecte plus.
"""

from __future__ import annotations

from uuid import UUID

from tortoise import fields

from karria.modules.identity.domain import (
    DEPARTURE_DETAILS_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    DepartureReason,
    LoginChallengeStatus,
    SessionEndReason,
    SocialProvider,
)
from karria.shared.database import BaseDBModel

#: Longueur d'un condensat SHA-256 en hexadécimal.
_HASH_LENGTH = 64
#: Longueur d'une version de document contractuel (`2026-10`, `v3`…).
_VERSION_LENGTH = 32


class UserModel(BaseDBModel):
    """Un compte : une adresse prouvée et son consentement."""

    email = fields.CharField(
        max_length=EMAIL_MAX_LENGTH,
        unique=True,
        description="Adresse normalisée (minuscules). Unique : une boîte, un compte.",
    )
    email_verified_at = fields.DatetimeField(description="Quand l'adresse a été prouvée.")
    terms_version = fields.CharField(max_length=_VERSION_LENGTH)
    privacy_policy_version = fields.CharField(max_length=_VERSION_LENGTH)
    consent_accepted_at = fields.DatetimeField(
        description="Horodatage du consentement donné « en continuant »."
    )
    last_login_at = fields.DatetimeField(null=True)

    class Meta:
        table = "identity_users"
        table_description = "Comptes Karria : une adresse e-mail prouvée, rien d'autre."


class AiConsentModel(BaseDBModel):
    """L'accord d'une personne à la remise de ses données à un modèle tiers (ADR-0025).

    Une ligne par compte : un nouvel accord remplace l'ancien, un retrait
    pose `revoked_at` sans effacer. La clé étrangère en cascade fait partir
    la ligne avec le compte (ADR-0015) — c'est la seule table du module qui
    s'y fie, parce qu'elle ne contient rien qui doive survivre au compte.
    """

    user: fields.ForeignKeyRelation[UserModel] = fields.ForeignKeyField(
        "models.UserModel", related_name="ai_consent", on_delete=fields.CASCADE, unique=True
    )
    user_id: UUID
    version = fields.CharField(
        max_length=64, description="La version de l'information acceptée (`2026-10.<empreinte>`)."
    )
    providers: list[str] = fields.JSONField(
        description="Les fournisseurs nommés à la personne à ce moment-là."
    )
    purposes: list[str] = fields.JSONField(
        description="Les usages nommés à la personne à ce moment-là."
    )
    accepted_at = fields.DatetimeField(description="Quand la personne a accepté.")
    revoked_at = fields.DatetimeField(
        null=True, description="Quand elle s'est ravisée, le cas échéant."
    )

    class Meta:
        table = "identity_ai_consents"
        table_description = "Consentement à l'IA : version acceptée, fournisseurs, retrait."


class ProviderIdentityModel(BaseDBModel):
    """Le rattachement d'un compte à un `sub` Apple ou Google.

    `(provider, subject)` est unique : c'est ce qui garantit qu'une personne
    revenant par Apple retrouve son compte plutôt que d'en créer un second.
    L'adresse n'est **pas** unique : la même peut rattacher un compte à
    Apple et à Google.
    """

    user: fields.ForeignKeyRelation[UserModel] = fields.ForeignKeyField(
        "models.UserModel", related_name="provider_identities", on_delete=fields.CASCADE
    )
    user_id: UUID
    provider = fields.CharEnumField(SocialProvider, max_length=16)
    subject = fields.CharField(
        max_length=255, description="Le `sub` signé par le fournisseur. C'est lui la clé."
    )
    email = fields.CharField(
        max_length=EMAIL_MAX_LENGTH,
        null=True,
        description="Indicative : l'adresse transmise au rattachement. N'identifie jamais.",
    )
    last_used_at = fields.DatetimeField()

    class Meta:
        table = "identity_provider_identities"
        table_description = "Rattachements Apple / Google, clés sur le `sub`."
        unique_together = (("provider", "subject"),)
        indexes = (("user_id",),)


class SessionModel(BaseDBModel):
    """Une session d'authentification. Condensats uniquement."""

    user: fields.ForeignKeyRelation[UserModel] = fields.ForeignKeyField(
        "models.UserModel", related_name="sessions", on_delete=fields.CASCADE
    )
    user_id: UUID

    access_token_hash = fields.CharField(
        max_length=_HASH_LENGTH,
        unique=True,
        description="Condensat SHA-256 du jeton d'accès. Jamais le jeton.",
    )
    refresh_token_hash = fields.CharField(
        max_length=_HASH_LENGTH,
        unique=True,
        description="Condensat SHA-256 du jeton de rafraîchissement courant.",
    )
    previous_refresh_token_hash = fields.CharField(
        max_length=_HASH_LENGTH,
        null=True,
        db_index=True,
        description=(
            "Condensat de la génération précédente. C'est lui qui rend la détection "
            "de vol possible : sans trace de l'ancien jeton, un rejeu ressemblerait "
            "à un jeton inconnu."
        ),
    )
    access_expires_at = fields.DatetimeField()
    refresh_expires_at = fields.DatetimeField(db_index=True)
    rotation_count = fields.IntField(default=0)
    user_agent = fields.CharField(max_length=255, null=True)
    last_used_at = fields.DatetimeField(null=True)
    ended_at = fields.DatetimeField(null=True, db_index=True)
    end_reason = fields.CharEnumField(SessionEndReason, max_length=32, null=True)

    class Meta:
        table = "identity_sessions"
        table_description = "Sessions d'authentification, jetons hachés."


class LoginChallengeModel(BaseDBModel):
    """Un parcours de connexion par code e-mail."""

    email = fields.CharField(max_length=EMAIL_MAX_LENGTH, description="Adresse visée, normalisée.")
    # Pas de clé étrangère : le compte peut ne pas exister encore. À la
    # suppression du compte, les parcours de l'adresse sont effacés avec lui
    # (ADR-0015) — ils portent l'adresse en clair.
    user_id = fields.UUIDField(
        null=True, description="Compte visé s'il existait déjà. Sans clé étrangère."
    )
    code_hash = fields.CharField(max_length=_HASH_LENGTH, description="Condensat du code en cours.")
    status = fields.CharEnumField(LoginChallengeStatus, max_length=16, db_index=True)
    expires_at = fields.DatetimeField(db_index=True)
    code_sent_at = fields.DatetimeField()
    code_sends = fields.IntField(default=1)
    attempts = fields.IntField(default=0)
    completed_at = fields.DatetimeField(null=True)
    created_account = fields.BooleanField(default=False)
    terms_version = fields.CharField(max_length=_VERSION_LENGTH)
    privacy_policy_version = fields.CharField(max_length=_VERSION_LENGTH)

    class Meta:
        table = "identity_login_challenges"
        table_description = "Parcours de connexion par code e-mail, codes hachés."


class DepartureFeedbackModel(BaseDBModel):
    """La trace **anonyme** d'un départ (ADR-0015).

    Ni `user_id`, ni adresse, ni identifiant d'installation : rien dans
    cette table ne désigne une personne. `created_at` (hérité) est la date
    du départ. Elle s'alimente à chaque suppression de compte et se lit
    hors de l'API, pour l'analyse produit.
    """

    reason = fields.CharEnumField(
        DepartureReason, max_length=32, db_index=True, description="Raison choisie dans l'app."
    )
    details = fields.CharField(
        max_length=DEPARTURE_DETAILS_MAX_LENGTH,
        null=True,
        description="Mot libre de la personne, blancs ôtés. Peut contenir ce qu'elle a voulu y mettre.",
    )
    account_age_days = fields.IntField(description="Âge du compte à la suppression, en jours.")
    platform = fields.CharField(
        max_length=16, null=True, description="Plateforme de l'appareil principal, si connue."
    )
    app_version = fields.CharField(
        max_length=100, null=True, description="Version de l'app sur cet appareil, si connue."
    )

    class Meta:
        table = "identity_departure_feedback"
        table_description = "Pourquoi les gens partent. Anonyme : aucun lien vers un compte."
