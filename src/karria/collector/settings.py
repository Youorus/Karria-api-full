"""La configuration du collecteur — la sienne, pas celle de l'API.

Le collecteur ne reçoit **aucun** secret de l'API (ni base, ni stockage,
ni modèle) : son jeton de service et l'adresse de l'API suffisent. C'est
ce qui borne ce qu'une compromission de ce processus — celui qui parle à
des sites tiers — peut atteindre.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from karria.collector.france_travail import Credentials


class CollectorSettings(BaseSettings):
    """Variables `COLLECTOR__*`."""

    model_config = SettingsConfigDict(env_prefix="COLLECTOR__", extra="ignore")

    #: L'API, vue du collecteur (réseau interne Dokploy).
    api_url: str = "http://localhost:8000/api/v1"
    token: SecretStr = SecretStr("")
    #: Mandataires sortants (`http://user:pass@hôte:port`), essayés en rotation.
    proxies: list[str] = Field(default_factory=list)
    #: Sites interrogés : HelloWork est français, Indeed couvre large,
    #: LinkedIn apporte cadres et tech, avec un logo pour presque chaque
    #: annonce. LinkedIn coûte une requête de plus par annonce (sa
    #: description) et limite vite : il a son propre budget, une pause plus
    #: longue et un disjoncteur sur 429/999 (`site_*`, `blocked_cooldown_*`).
    #: Mesuré le 2026-10-25 depuis le serveur, sans mandataire : 25 annonces
    #: par recherche, 21 à 24 avec description et logo, ~28 s par recherche.
    sites: list[str] = Field(default_factory=lambda: ["indeed", "hellowork", "linkedin"])
    #: Annonces demandées par requête et par site.
    results_wanted: int = Field(default=60, ge=1, le=500)
    #: On ne demande que les annonces des trois derniers jours : au-delà,
    #: on les a déjà vues au passage précédent.
    hours_old: int = Field(default=72, ge=1)
    #: Cibles demandées à l'API par passage.
    targets_per_run: int = Field(default=30, ge=1, le=100)
    #: Pause entre deux requêtes au même site, en secondes : la politesse
    #: qui évite le bannissement. Une gigue de ±30 % la rend irrégulière :
    #: un rythme parfaitement régulier est une signature de robot.
    pause_seconds: float = Field(default=2.0, ge=0)
    pause_jitter: float = Field(default=0.3, ge=0, le=1)
    #: Requêtes par site et par heure glissante, au plus : au-delà, le site
    #: attend le passage suivant. 60 cibles x 2 sites toutes les 10 minutes
    #: font 360/h ; 200 laisse de la marge sans jamais marteler un site.
    site_hourly_budget: int = Field(default=200, ge=1)
    #: Budgets propres à un site (recherches par heure glissante). LinkedIn :
    #: 40 recherches de 25 annonces, soit ~1 000 annonces lues par heure.
    site_hourly_budgets: dict[str, int] = Field(default_factory=lambda: {"linkedin": 40})
    #: Pauses propres à un site, en secondes, et leur gigue (±).
    site_pause_seconds: dict[str, float] = Field(default_factory=lambda: {"linkedin": 12.0})
    site_pause_jitter: dict[str, float] = Field(default_factory=lambda: {"linkedin": 0.5})
    #: Annonces demandées par recherche, propres à un site (LinkedIn : chaque
    #: annonce coûte une requête de description).
    site_results_wanted: dict[str, int] = Field(default_factory=lambda: {"linkedin": 25})
    #: Disjoncteur : un site qui répond 429, 999 ou « bloqué » est laissé de
    #: côté pendant ce délai, doublé à chaque blocage consécutif jusqu'au
    #: plafond — d'un passage à l'autre, pas seulement pour le passage.
    blocked_cooldown_seconds: float = Field(default=1_800, ge=0)
    blocked_cooldown_max_seconds: float = Field(default=6 * 3_600, ge=0)
    #: Un site qui rend zéro annonce sur autant de cibles qui en rendaient
    #: hier est probablement cassé (page changée) : on l'arrête pour le
    #: passage et on le journalise en erreur (« zéro silencieux »).
    silent_zero_limit: int = Field(default=3, ge=1)
    #: Une cible qui avait trouvé moins que cela ne compte pas comme preuve.
    silent_zero_floor: int = Field(default=5, ge=1)
    #: Échecs consécutifs d'un site au-delà desquels il est laissé de côté
    #: pour le reste du passage (bannissement probable).
    site_failure_limit: int = Field(default=3, ge=1)
    #: En boucle : délai entre deux passages JobSpy.
    interval_seconds: float = Field(default=600, ge=1)
    request_timeout_seconds: float = Field(default=60, gt=0)

    # --- France Travail (la source n°1, ADR-0016 bis) --------------------
    #: Identifiants OAuth2 `client_credentials` du compte partenaire. Vides,
    #: la source est simplement absente : le collecteur l'annonce au
    #: démarrage et continue avec JobSpy.
    france_travail_client_id: str = ""
    france_travail_client_secret: SecretStr = SecretStr("")
    france_travail_scope: str = "api_offresdemploiv2 o2dsoffre"
    france_travail_token_url: str = (
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"  # noqa: S105 - une URL, pas un secret
    )
    france_travail_search_url: str = (
        "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
    )
    #: Cadence du passage incrémental : les annonces créées depuis le
    #: dernier passage. Trois minutes : ~60 annonces par passage en journée
    #: (24 500 par jour mesurées le 2026-10-22), une page.
    france_travail_poll_seconds: float = Field(default=180, ge=10)
    #: Recouvrement relu à chaque passage : une annonce indexée en retard
    #: par France Travail n'est pas perdue ; revue à l'identique, elle ne
    #: coûte à l'API qu'un `last_seen_at`. Mesuré le 2026-10-22 : ~140
    #: annonces par minute en journée, donc ~700 relues par passage.
    france_travail_overlap_seconds: float = Field(default=300, ge=0)
    #: Au démarrage, sans repère : on relit cette durée en arrière.
    france_travail_startup_hours: float = Field(default=2, ge=0)
    #: Chaque jour, un balayage par département relit cette fenêtre : il
    #: rattrape ce qu'un passage aurait manqué.
    france_travail_sweep_days: int = Field(default=1, ge=0, le=31)
    france_travail_sweep_hour_utc: int = Field(default=2, ge=0, le=23)

    @property
    def france_travail(self) -> Credentials | None:
        """Les identifiants, ou `None` si la source n'est pas configurée."""
        if not self.france_travail_client_id or not self.france_travail_client_secret:
            return None
        return Credentials(
            client_id=self.france_travail_client_id,
            client_secret=self.france_travail_client_secret.get_secret_value(),
            scope=self.france_travail_scope,
            token_url=self.france_travail_token_url,
            search_url=self.france_travail_search_url,
        )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "json"
