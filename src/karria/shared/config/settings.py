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
  `storage`, `mail`, `social`, `push`, `gemini`, `llm`, `qdrant`, `inference`,
  `geocoding`, `collector`, `observability`) et non alignés à plat. Chaque bloc
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
from typing import Final, Literal, Self

import structlog
from pydantic import BaseModel, Field, PrivateAttr, SecretStr, model_validator
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
    #: Adresse publique du stockage (`https://storage.karria.fr`) quand l'API
    #: le joint par une adresse interne (`http://karria-minio:9000`) : les URL
    #: présignées sont signées pour cet hôte-là, celui qu'ouvrira le téléphone.
    public_endpoint_url: str | None = None
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


class SocialSettings(BaseModel):
    """Identifiants d'application auprès d'Apple et de Google.

    Ce sont des **audiences**, pas des secrets : un identifiant de client
    OAuth figure déjà dans l'application mobile, que n'importe qui peut
    décompiler. Ce qui protège, c'est que l'API refuse tout jeton émis pour
    une autre audience que celles-ci (ADR-0011).

    Google émet un client **par plateforme** : un jeton obtenu sur iOS
    porte l'identifiant iOS, un jeton obtenu via le client web — celui que
    `@react-native-google-signin` exige pour rendre un `idToken` — porte
    l'identifiant web. N'en déclarer qu'un ferait échouer l'autre
    plateforme avec une erreur qui ressemble à un jeton forgé.
    """

    google_ios_client_id: str = ""
    google_web_client_id: str = ""
    google_android_client_id: str = ""
    #: Le *bundle identifier* de l'application iOS, audience des jetons
    #: Apple. Il n'est pas secret et ne change pas d'un environnement à
    #: l'autre — d'où la valeur par défaut.
    apple_client_id: str = "com.karria.app"

    @property
    def google_audiences(self) -> list[str]:
        """Les audiences Google acceptées, sans les vides."""
        return [
            value
            for value in (
                self.google_ios_client_id,
                self.google_web_client_id,
                self.google_android_client_id,
            )
            if value
        ]

    @property
    def apple_audiences(self) -> list[str]:
        """Les audiences Apple acceptées."""
        return [self.apple_client_id] if self.apple_client_id else []

    @property
    def google_enabled(self) -> bool:
        """La connexion Google est-elle configurée sur ce serveur ?"""
        return bool(self.google_audiences)

    @property
    def apple_enabled(self) -> bool:
        """La connexion Apple est-elle configurée sur ce serveur ?"""
        return bool(self.apple_audiences)


class PushSettings(BaseModel):
    """Accès au service de notifications push d'Expo.

    L'URL est de la configuration parce qu'un test de bout en bout peut la
    pointer vers un serveur local ; le jeton d'accès est facultatif — Expo
    ne l'exige que si la sécurité renforcée est activée sur le projet.
    """

    expo_api_url: str = "https://exp.host/--/api/v2"
    expo_access_token: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=10.0, gt=0)


class GeminiSettings(BaseModel):
    """Accès à l'API Gemini (module `cv` : interprétation, transcription,
    extraction de CV, photo améliorée).

    Les noms de modèles sont ici parce qu'ils décrivent **l'environnement**
    — ce que le fournisseur sert aujourd'hui — et non une règle du produit :
    un modèle retiré par Google se remplace par configuration, sans
    livraison. Les valeurs par défaut sont celles relevées sur la liste
    des modèles disponibles le 2026-10-21 (ADR-0013).

    L'appel passe par l'API REST (`shared/http/outbound.py`), sans SDK : la
    clé voyage dans un en-tête, jamais dans l'URL — donc jamais dans un
    journal d'accès.
    """

    api_key: SecretStr = SecretStr("")
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    #: Texte, JSON à schéma imposé et compréhension audio.
    text_model: str = "gemini-3.8-flash"
    #: Génération d'image à partir d'une image (portrait de CV).
    image_model: str = "gemini-3.1-flash-image"
    #: Une transcription ou une extraction de CV prend plusieurs secondes ;
    #: le délai par défaut du client sortant (10 s) couperait des appels
    #: parfaitement sains.
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    #: Une génération d'image est plus lente encore.
    image_timeout_seconds: float = Field(default=90.0, gt=0)

    @property
    def enabled(self) -> bool:
        """Une clé est-elle renseignée ?"""
        return bool(self.api_key.get_secret_value())


#: Les fournisseurs que le routeur connaît (`shared/llm/ports.py::Provider`, même liste).
LLM_PROVIDERS: Final[tuple[str, ...]] = ("gemini", "anthropic", "openai", "compatible")


class LlmProviderSettings(BaseModel):
    """Un fournisseur de modèles : sa clé, son adresse, son délai."""

    api_key: SecretStr = SecretStr("")
    #: Vide : l'adresse par défaut du fournisseur (`shared/llm/*.py`).
    base_url: str = ""
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    #: Pour `compatible` seulement : `json_schema` si le serveur sait imposer
    #: un schéma (vLLM, Mistral), `json_object` sinon (le schéma est alors
    #: rappelé dans la consigne).
    structured_output: Literal["json_schema", "json_object"] = "json_schema"
    #: Pour `anthropic` seulement : l'espace de travail, envoyé en en-tête
    #: `anthropic-workspace-id`. La clé de Karria est une clé d'utilisateur,
    #: rattachée à aucun espace : sans cet identifiant, ses appels échouent.
    #: Tant qu'il est vide, Anthropic reste **hors routage** (journal
    #: `llm.provider_disabled`), sans bloquer le démarrage.
    workspace_id: str = ""

    def is_configured(self, provider: str) -> bool:
        """A-t-on de quoi l'appeler ?

        Une clé — ou, pour un serveur `compatible` local, une adresse. Anthropic
        exige en plus l'espace de travail (voir `workspace_id`).
        """
        return not self.missing(provider)

    def missing(self, provider: str) -> str:
        """Le nom court du réglage qui manque pour appeler ce fournisseur, `""` sinon."""
        if not self.api_key.get_secret_value():
            if provider == "compatible" and self.base_url:
                return ""
            return "API_KEY"
        if provider == "anthropic" and not self.workspace_id.strip():
            return "WORKSPACE_ID"
        return ""


class LlmProvidersSettings(BaseModel):
    """`LLM__PROVIDERS__<FOURNISSEUR>__API_KEY`, `__BASE_URL`…"""

    gemini: LlmProviderSettings = Field(default_factory=LlmProviderSettings)
    anthropic: LlmProviderSettings = Field(default_factory=LlmProviderSettings)
    openai: LlmProviderSettings = Field(default_factory=LlmProviderSettings)
    compatible: LlmProviderSettings = Field(default_factory=LlmProviderSettings)


class LlmTaskRoutes(BaseModel):
    """`LLM__TASKS__<TACHE>=fournisseur:modèle` — une route par tâche que les modules nomment.

    Vide : la route par défaut, dérivée de `GEMINI__TEXT_MODEL` /
    `GEMINI__IMAGE_MODEL` (`Settings._default_llm_routes`), pour qu'une
    configuration d'avant l'ADR-0018 continue de fonctionner telle quelle.
    """

    search_dialogue: str = ""
    cv_interpret: str = ""
    cv_extract: str = ""
    transcribe: str = ""
    portrait: str = ""
    #: L'enrichissement des offres (champs manquants) et la mémoire de Karria.
    enrich: str = ""
    memory: str = ""
    #: Modèles de CV (ADR-0020) : rôle des lignes d'un PDF (vision), et
    #: explication d'un score ATS déjà calculé.
    template_extract: str = ""
    ats_explain: str = ""


class LlmSettings(BaseModel):
    """Le routage des modèles de langage (ADR-0018)."""

    providers: LlmProvidersSettings = Field(default_factory=LlmProvidersSettings)
    tasks: LlmTaskRoutes = Field(default_factory=LlmTaskRoutes)
    #: `LLM__FALLBACKS__<TACHE>=fournisseur:modèle` : le repli sur panne, facultatif.
    fallbacks: LlmTaskRoutes = Field(default_factory=LlmTaskRoutes)


class QdrantSettings(BaseModel):
    """Accès au moteur de recherche vectorielle (module `offers`).

    Les collections portent l'environnement dans leur nom
    (`karria-production-offers`), comme les buckets : une recette ne peut
    pas écrire dans l'index de la production. La clé voyage en en-tête.
    """

    url: str = "http://localhost:6333"
    api_key: SecretStr = SecretStr("")
    collection_prefix: str = Field(default="karria", min_length=1, max_length=32)
    request_timeout_seconds: float = Field(default=30.0, gt=0)


class InferenceSettings(BaseModel):
    """Le serveur d'inférence local (Text Embeddings Inference) : les plongements.

    Le nom du modèle est de la configuration parce qu'il décrit ce que le
    serveur sert ; il entre aussi dans la **version d'index** : un vecteur
    calculé par un autre modèle n'est pas comparable, et tout changement de
    modèle impose un ré-indexage explicite (jamais au démarrage).
    """

    embeddings_url: str = "http://localhost:8080"
    #: Ce que le serveur sert, quantification comprise : `@int8` distingue
    #: deux plongements qui ne sont pas comparables entre eux.
    embedding_model: str = "intfloat/multilingual-e5-base@int8"
    embedding_dimension: int = Field(default=768, ge=8)
    #: Textes par appel au serveur de plongements, et appels simultanés.
    #: TEI (ONNX) traite huit textes par passe ; quatre appels de huit
    #: occupent les huit cœurs du serveur (mesuré le 2026-10-22). Une
    #: machine différente se règle ici, pas dans le code.
    embedding_batch_size: int = Field(default=8, ge=1, le=256)
    embedding_concurrency: int = Field(default=4, ge=1, le=32)
    request_timeout_seconds: float = Field(default=120.0, gt=0)

    @property
    def enabled(self) -> bool:
        """Le serveur est-il renseigné ? (Le défaut pointe le poste local.)"""
        return bool(self.embeddings_url)


class GeocodingSettings(BaseModel):
    """Le géocodeur : la Base Adresse Nationale, ou un miroir."""

    ban_url: str = "https://api-adresse.data.gouv.fr"
    request_timeout_seconds: float = Field(default=10.0, gt=0)


class DocumentsSettings(BaseModel):
    """Modèles de CV et exports (ADR-0020).

    `gotenberg_url` vise le service interne de la composition `karria-donnees`
    (Chromium + LibreOffice, jamais exposé). `admin_token` ouvre les routes
    `/internal/templates` (dépôt et revue des modèles) ; vide, elles répondent
    401 à tout le monde.
    """

    gotenberg_url: str = "http://localhost:3005"
    gotenberg_timeout_seconds: float = Field(default=60.0, gt=0)
    admin_token: SecretStr = SecretStr("")


class PaymentsSettings(BaseModel):
    """Encaissements Stripe (module `payments`).

    Sans `secret_key`, les routes de paiement répondent 503 et le catalogue dit
    `enabled: false` : il n'y a pas de mode démonstration. `webhook_secret`
    authentifie les événements que Stripe envoie ; vide, ils sont tous refusés.
    `return_url` est la destination par défaut après paiement ; l'app en propose
    une autre (`karria://…`), dans les bornes du domaine.
    """

    secret_key: SecretStr = SecretStr("")
    webhook_secret: SecretStr = SecretStr("")
    return_url: str = "https://karria.fr/paiement"
    #: L'interrupteur par plateforme (ADR-0024). Éteindre `ios_enabled` cache
    #: tout paiement dans l'app iOS sans livraison — si Apple s'y oppose. Ce
    #: n'est pas une règle métier mais un fait d'exploitation : « ce canal
    #: est-il ouvert aujourd'hui ? ».
    ios_enabled: bool = True
    android_enabled: bool = True
    #: Stripe Tax calcule et ventile la TVA sur des prix TTC. Faux par défaut :
    #: Stripe refuse d'ouvrir une session tant que Stripe Tax n'est pas activé
    #: dans le tableau de bord.
    automatic_tax: bool = False


class CollectorAccessSettings(BaseModel):
    """L'accès du collecteur d'offres aux routes internes (`/internal/offers/*`).

    Le collecteur est un processus à part (`python -m karria.collector`) ;
    il s'authentifie par ce jeton de service, partagé par ses seules deux
    configurations. Vide : les routes internes répondent 401 à tout le monde.
    """

    token: SecretStr = SecretStr("")

    @property
    def enabled(self) -> bool:
        """Un jeton est-il configuré ?"""
        return bool(self.token.get_secret_value())


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

    _explicit_llm_routes: dict[str, str] = PrivateAttr(default_factory=dict)

    app: AppSettings = Field(default_factory=AppSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
    social: SocialSettings = Field(default_factory=SocialSettings)
    push: PushSettings = Field(default_factory=PushSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    inference: InferenceSettings = Field(default_factory=InferenceSettings)
    geocoding: GeocodingSettings = Field(default_factory=GeocodingSettings)
    collector: CollectorAccessSettings = Field(default_factory=CollectorAccessSettings)
    documents: DocumentsSettings = Field(default_factory=DocumentsSettings)
    payments: PaymentsSettings = Field(default_factory=PaymentsSettings)
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
    def _default_llm_routes(self) -> Self:
        """Avant l'ADR-0018, Gemini seul : ses variables restent la source par défaut.

        `GEMINI__API_KEY` alimente `LLM__PROVIDERS__GEMINI__API_KEY` s'il est
        vide, et chaque tâche sans route part sur `GEMINI__TEXT_MODEL`
        (`GEMINI__IMAGE_MODEL` pour le portrait). Les routes **explicites**
        sont retenues à part : ce sont elles que le démarrage vérifie
        strictement (`llm_problems`) ; une route par défaut sans clé n'est
        qu'une fonctionnalité d'IA absente, qui avertit sans bloquer.
        """
        self._explicit_llm_routes = {
            task: value
            for task, value in {
                **self.llm.tasks.model_dump(),
                **{f"fallback:{t}": v for t, v in self.llm.fallbacks.model_dump().items()},
            }.items()
            if value.strip()
        }
        gemini = self.llm.providers.gemini
        if not gemini.api_key.get_secret_value() and self.gemini.enabled:
            gemini.api_key = self.gemini.api_key
        if not gemini.base_url:
            gemini.base_url = self.gemini.base_url
        text = f"gemini:{self.gemini.text_model}"
        for task in (
            "search_dialogue",
            "cv_interpret",
            "cv_extract",
            "transcribe",
            "enrich",
            "memory",
            "template_extract",
            "ats_explain",
        ):
            if not getattr(self.llm.tasks, task).strip():
                setattr(self.llm.tasks, task, text)
        if not self.llm.tasks.portrait.strip():
            self.llm.tasks.portrait = f"gemini:{self.gemini.image_model}"
        return self

    def llm_problems(self) -> list[str]:
        """Les routes LLM explicites qui ne tiendraient pas : mal formées, ou vers un fournisseur sans clé.

        Vérifié au démarrage en environnement déployé (bloque) ; en local,
        `build_language_model` l'avertit seulement. Une route laissée par
        défaut (Gemini) sans clé n'est pas un problème de configuration mais
        une IA absente : `config.gemini_disabled` l'annonce. La lecture est
        faite ici, sans importer `shared/llm` : la configuration est lue par
        le collecteur aussi, qui ne doit pas connaître le client sortant.
        """
        problems: list[str] = []
        for task, value in self._explicit_llm_routes.items():
            variable = (
                f"LLM__FALLBACKS__{task.removeprefix('fallback:').upper()}"
                if task.startswith("fallback:")
                else f"LLM__TASKS__{task.upper()}"
            )
            provider, sep, model = value.strip().partition(":")
            provider = provider.strip().lower()
            if not sep or not model.strip() or provider not in LLM_PROVIDERS:
                problems.append(
                    f"{variable}=« {value} » : la forme attendue est fournisseur:modèle, "
                    f"fournisseur parmi {', '.join(LLM_PROVIDERS)}."
                )
                continue
            missing = getattr(self.llm.providers, provider).missing(provider)
            if missing:
                problems.append(
                    f"{variable} est routée vers {provider}, mais "
                    f"LLM__PROVIDERS__{provider.upper()}__{missing} est vide."
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
        problems.extend(self.llm_problems())
        if not self.gemini.enabled:
            # Avertit sans bloquer : seule la création du CV en est privée,
            # et retenir un correctif urgent pour une clé d'IA manquante
            # coûterait plus que des routes qui répondent 503 le temps de
            # la poser.
            logger.warning(
                "config.gemini_disabled",
                detail="GEMINI__API_KEY est vide : interprétation, transcription, import et "
                "portrait répondront 503 `ai_unavailable`.",
            )

        if not self.social.google_enabled:
            # Avertit sans bloquer : la connexion Google répondra
            # « indisponible », le reste de l'API sert. Un correctif urgent
            # ne doit pas attendre qu'un identifiant OAuth soit posé.
            logger.warning(
                "config.google_login_disabled",
                hint="SOCIAL__GOOGLE_IOS_CLIENT_ID / SOCIAL__GOOGLE_WEB_CLIENT_ID absents.",
            )

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
