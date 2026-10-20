"""Configuration de l'application.

Contrat
-------
- La configuration décrit l'**environnement**, jamais les règles métier.
  Un seuil de facturation, un quota d'appels à un modèle, un plafond de
  matching appartiennent au domaine, pas ici. C'est la frontière la plus
  souvent franchie par erreur — la V1 pilotait `OPENAI_DAILY_BUDGET_EUR`
  et `RUN_STARTUP_REINDEX` par variable d'environnement — et celle qui
  rend une configuration illisible en dix-huit mois.
- Les réglages sont **composés par domaine** (`app`, `db`, `security`,
  `storage`, `mail`, `observability`) et non alignés à plat. Chaque bloc
  est lisible seul et peut être injecté seul dans un test.
- L'objet est **injecté** via `SettingsDep`, jamais importé comme singleton
  global. Un test substitue la configuration sans monkeypatch.
- Toute incohérence est détectée **au démarrage**, pas au premier appel en
  production.

Les variables d'environnement utilisent le séparateur `__` :
`DB__URL`, `SECURITY__SECRET_KEY`, `OBSERVABILITY__LOG_LEVEL`.

Ce que ce fichier ne contient pas encore, et pourquoi
-----------------------------------------------------
Aucun bloc pour un fournisseur de modèles (Gemini), un service de
transcription ou une source d'offres. Ces blocs arrivent **avec le module
qui en a besoin**, dans le commit qui le crée : un réglage sans
consommateur est une promesse que personne ne vérifie.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

import structlog
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from karria.shared.config.providers import PROFILES, Provider, ProviderProfile

#: Racine du dépôt, d'où est lu le `.env`.
#:
#: `parents[4]` et non `[3]` : le chemin est
#: `<racine>/src/karria/shared/config/settings.py`, donc remonter de trois
#: crans s'arrête à `src/`. Le `.env` y était cherché — et n'y étant jamais,
#: il n'était **jamais lu** : toute la configuration retombait sur ses
#: valeurs par défaut, en silence. Un fichier de configuration ignoré sans
#: le moindre avertissement est exactement le genre de panne qui se
#: découvre en production.
BASE_DIR = Path(__file__).resolve().parents[4]

_INSECURE_SECRETS = frozenset(
    {"change-me-en-local-et-jamais-en-production", "secret", "changeme", ""}
)


# `structlog` et non `logging` : le reste du projet journalise en
# structuré, et un avertissement de configuration doit être requêtable
# comme les autres.
logger = structlog.get_logger(__name__)


class Environment(StrEnum):
    """Environnement d'exécution."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_deployed(self) -> bool:
        """Vrai pour les environnements exposés à de vrais utilisateurs."""
        return self in {Environment.STAGING, Environment.PRODUCTION}


class AppSettings(BaseModel):
    """Identité et exposition HTTP de l'application."""

    name: str = "Karria API"
    version: str = "0.1.0"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    api_prefix: str = "/api/v1"
    #: Adresse publique de cette API, sans barre finale.
    #:
    #: Elle sert à composer les URL que des tiers doivent atteindre — la
    #: page où un prestataire renvoie l'utilisateur, un lien de rappel.
    #: Elle ne se déduit **pas** de la requête : `Host` et `Origin` sont
    #: fournis par l'appelant, et suffiraient à faire revenir quelqu'un
    #: sur un domaine qu'il contrôle.
    public_url: str = "http://localhost:8000"

    def public_api_url(self, path: str) -> str:
        """L'adresse publique d'une route de cette API, composée côté serveur.

        L'adresse ne dépend que de la configuration, jamais de l'appelant,
        ce qui ferme la redirection ouverte. Écrit ici pour que deux
        modules la partagent sans s'importer (règle 4).

        Args:
            path: Chemin sous le préfixe versionné, commençant par `/`.
        """
        return f"{self.public_url.rstrip('/')}{self.api_prefix}{path}"


class DatabaseSettings(BaseModel):
    """Connexion PostgreSQL."""

    url: str = "postgres://karria:karria@localhost:5432/karria"
    min_pool_size: int = Field(default=1, ge=1)
    max_pool_size: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_pool_bounds(self) -> Self:
        if self.max_pool_size < self.min_pool_size:
            msg = "DB__MAX_POOL_SIZE doit être supérieur ou égal à DB__MIN_POOL_SIZE."
            raise ValueError(msg)
        return self


class SecuritySettings(BaseModel):
    """Secrets et politique d'exposition réseau."""

    secret_key: SecretStr = SecretStr("change-me-en-local-et-jamais-en-production")
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    allowed_hosts: list[str] = Field(default_factory=lambda: ["*"])

    #: Poivre ajouté avant de hacher un jeton de session ou un code à usage
    #: unique. Vit ici et **jamais en base** : une fuite de la seule table
    #: de sessions devient alors inexploitable.
    #:
    #: ⚠️ Le changer déconnecte tout le monde — toutes les empreintes
    #: stockées cessent de correspondre. C'est d'ailleurs le geste à faire
    #: si l'on soupçonne une fuite de la base.
    token_pepper: SecretStr = SecretStr("")


class MailProvider(StrEnum):
    """Par quoi les e-mails sortent."""

    #: Un serveur SMTP : Gmail, un hébergeur, un service transactionnel.
    SMTP = "smtp"
    #: Affiche le message dans les journaux. Développement et tests.
    CONSOLE = "console"


class MailSettings(BaseModel):
    """Accès au service d'envoi d'e-mails.

    `console` est le défaut : un projet fraîchement cloné doit démarrer et
    laisser lire les codes sans qu'on ait à ouvrir un compte quelque part.
    L'invariant de production le refuse — un code affiché dans les journaux
    n'atteint jamais son destinataire, et la connexion de Karria passe par
    un code envoyé par e-mail.
    """

    provider: MailProvider = MailProvider.CONSOLE

    host: str = ""
    port: int = Field(default=587, ge=1, le=65535)
    username: str = ""
    password: SecretStr = SecretStr("")

    #: Adresse d'expédition.
    #:
    #: Elle doit correspondre au compte authentifié. La plupart des
    #: fournisseurs refusent un `From:` qui ne leur appartient pas — et
    #: quand ils ne refusent pas, ils réécrivent l'en-tête, de sorte que
    #: les réponses partent ailleurs que prévu.
    #:
    #: C'est aussi ce que vérifie SPF côté destinataire : un `From:` d'un
    #: domaine dont l'enregistrement SPF n'autorise pas le serveur
    #: émetteur part directement en indésirable.
    from_address: str = ""
    from_name: str = "Karria"

    #: URL publique du site, où mènent les boutons des e-mails. Elle change
    #: d'un environnement à l'autre — c'est donc de la configuration, et
    #: elle est injectée dans **tous** les gabarits sans que personne ait
    #: à la passer. Un gabarit peut écrire `$app_url` et rien d'autre.
    app_url: str = "https://karria.fr"
    #: Adresse de réponse, quand elle diffère de l'expéditeur.
    reply_to: str | None = None

    #: Domaine des identifiants de message.
    #:
    #: Un `Message-ID` dont le domaine ne correspond pas à celui de
    #: l'expéditeur est un signal d'incohérence pour les filtres.
    message_id_domain: str = "karria.fr"

    @model_validator(mode="after")
    def _check_smtp_completeness(self) -> Self:
        """Un envoi SMTP à moitié configuré échoue au premier code envoyé.

        Mieux vaut refuser au démarrage : l'incident dure deux minutes,
        contre un compte inaccessible et un silence côté journaux.
        """
        if self.provider is not MailProvider.SMTP:
            return self
        manquants = [
            nom
            for nom, valeur in (
                ("MAIL__HOST", self.host),
                ("MAIL__USERNAME", self.username),
                ("MAIL__PASSWORD", self.password.get_secret_value()),
                ("MAIL__FROM_ADDRESS", self.from_address or self.username),
            )
            if not valeur
        ]
        if manquants:
            msg = f"MAIL__PROVIDER=smtp exige : {', '.join(manquants)}."
            raise ValueError(msg)
        return self

    @property
    def sender(self) -> str:
        """Adresse d'expédition effective.

        Retombe sur le compte authentifié : chez la plupart des serveurs,
        expédier au nom d'une autre adresse est refusé ou réécrit.
        """
        return self.from_address or self.username


class StorageSettings(BaseModel):
    """Accès au stockage objet.

    L'endpoint n'est **pas saisi à la main** : il est dérivé du fournisseur
    et de la région. Une URL d'endpoint recopiée dans un `.env` est une
    faute de frappe en attente — et elle échoue au premier envoi de fichier,
    pas au démarrage, donc chez un utilisateur et non en déploiement.

    Changer de fournisseur se réduit ainsi à `STORAGE__PROVIDER` et aux
    trois secrets d'accès.
    """

    provider: Provider = Provider.MEMORY
    region: str | None = None
    #: Cloudflare R2 uniquement : l'identifiant de compte est dans l'URL.
    account_id: str | None = None
    #: Échappatoire : force l'endpoint quand un fournisseur sort du
    #: catalogue. À n'employer que le temps d'ajouter son profil.
    endpoint_url: str | None = None
    access_key: SecretStr = SecretStr("")
    secret_key: SecretStr = SecretStr("")

    bucket_prefix: str = Field(default="karria", min_length=1, max_length=32)
    presign_ttl_seconds: int = Field(default=3600, ge=60)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=10)

    @property
    def profile(self) -> ProviderProfile:
        """Particularités du fournisseur retenu."""
        return PROFILES[self.provider]

    @property
    def resolved_region(self) -> str:
        """Région effective, celle du profil à défaut de réglage."""
        return self.region or self.profile.default_region

    @property
    def resolved_endpoint(self) -> str | None:
        """URL du service, dérivée du profil sauf surcharge explicite.

        `None` pour AWS, dont le SDK connaît déjà ses propres endpoints.
        """
        if self.endpoint_url:
            return self.endpoint_url
        template = self.profile.endpoint_template
        if template is None:
            return None
        return template.format(region=self.resolved_region, account=self.account_id or "")

    @model_validator(mode="after")
    def _check_coherence(self) -> Self:
        """Échoue au démarrage plutôt qu'au premier envoi de fichier."""
        problems: list[str] = []
        if self.provider is not Provider.MEMORY:
            if not self.access_key.get_secret_value():
                problems.append("STORAGE__ACCESS_KEY est vide.")
            if not self.secret_key.get_secret_value():
                problems.append("STORAGE__SECRET_KEY est vide.")
        if self.profile.requires_account_id and not self.account_id:
            problems.append(f"STORAGE__ACCOUNT_ID est requis pour '{self.provider.value}'.")
        if self.presign_ttl_seconds > self.profile.max_presign_seconds:
            problems.append(
                f"STORAGE__PRESIGN_TTL_SECONDS dépasse le maximum de "
                f"'{self.provider.value}' ({self.profile.max_presign_seconds} s)."
            )
        if problems:
            details = "\n  - ".join(problems)
            msg = f"Configuration de stockage invalide :\n  - {details}"
            raise ValueError(msg)
        return self


class ObservabilitySettings(BaseModel):
    """Journalisation et traçabilité."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "json"


class Settings(BaseSettings):
    """Configuration complète, composée de blocs indépendants."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    app: AppSettings = Field(default_factory=AppSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    def _provider_problems(self) -> list[str]:
        """Les fournisseurs configurés d'une façon qui ne tiendrait pas.

        Regroupés ici, et non dans le validateur, pour que tout ce qui
        répond à la même question — « ce service est-il en état de servir
        de vrais utilisateurs ? » — se lise au même endroit. Chaque
        fournisseur branché plus tard y ajoute sa ligne.
        """
        problems: list[str] = []
        if self.mail.provider is MailProvider.CONSOLE:
            problems.append(
                "MAIL__PROVIDER=console : les codes de connexion s'afficheraient "
                "dans les journaux au lieu d'atteindre leur destinataire."
            )
        return problems

    @model_validator(mode="after")
    def _enforce_production_invariants(self) -> Self:
        """Échoue au démarrage plutôt qu'en production silencieusement.

        Une configuration dangereuse doit empêcher le conteneur de démarrer :
        un déploiement qui refuse de booter est un incident de deux minutes,
        un secret par défaut en production est une brèche de plusieurs mois.

        La règle de partage entre ce qui **bloque** et ce qui **avertit** :
        un garde-fou bloque ce qui compromet — un secret par défaut, un
        CORS ouvert, un stockage qui perd les fichiers. Ce qui ne fait que
        dégrader une fonctionnalité parmi d'autres se journalise, sans
        prendre en otage le correctif urgent qu'on essaie de déployer.
        """
        if not self.app.environment.is_deployed:
            return self

        problems: list[str] = []
        if self.app.debug:
            problems.append("APP__DEBUG doit être false hors environnement local.")
        if self.security.secret_key.get_secret_value() in _INSECURE_SECRETS:
            problems.append("SECURITY__SECRET_KEY est resté à sa valeur par défaut.")
        if "*" in self.security.allowed_hosts:
            problems.append("SECURITY__ALLOWED_HOSTS ne doit pas contenir '*'.")
        if "*" in self.security.allowed_origins:
            problems.append("SECURITY__ALLOWED_ORIGINS ne doit pas contenir '*'.")
        if not self.storage.profile.durable:
            # Testé sur une propriété du profil, et non sur le nom du
            # fournisseur : la configuration n'a pas à connaître la liste.
            problems.append(
                f"STORAGE__PROVIDER='{self.storage.provider.value}' ne conserve pas les "
                "fichiers au redémarrage et ne peut pas servir hors du local."
            )
        if self.security.token_pepper.get_secret_value() in _INSECURE_SECRETS:
            problems.append(
                "SECURITY__TOKEN_PEPPER doit être défini : sans lui, une fuite de la "
                "table de sessions suffit à casser les codes à six chiffres."
            )
        problems.extend(self._provider_problems())

        if problems:
            details = "\n  - ".join(problems)
            msg = f"Configuration invalide pour '{self.app.environment}' :\n  - {details}"
            raise ValueError(msg)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retourne la configuration du processus.

    Mise en cache : la lecture du `.env` et la validation n'ont lieu qu'une
    fois. Passer par cette fonction (et non par une constante de module)
    permet à un test de vider le cache ou de surcharger la dépendance FastAPI.
    """
    return Settings()
