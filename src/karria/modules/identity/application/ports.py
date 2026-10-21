"""Ports du module `identity`.

Chacun décrit un **besoin**, pas une technologie. C'est ce qui permet
d'exercer tout le parcours de connexion — rotation, détection de
réutilisation, blocage après trop d'essais, vérification d'un jeton Google
signé — sans PostgreSQL, sans SMTP et sans réseau, en millisecondes.

Aucun port ne rend de modèle Tortoise (AGENTS.md §6.10) : les repositories
traduisent en entités du domaine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from karria.modules.identity.domain import (
    DepartureRecord,
    EmailAddress,
    LoginChallenge,
    ProviderIdentity,
    Session,
    SessionEndReason,
    SocialProvider,
    User,
    VerifiedSocialProfile,
)
from karria.shared.i18n import Language

# ---------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------


class UserRepository(Protocol):
    """Port : accès persistant aux comptes."""

    async def find_by_id(self, user_id: UUID) -> User | None:
        """Retourne le compte, ou `None`."""
        ...

    async def find_by_email(self, email: EmailAddress) -> User | None:
        """Retourne le compte portant cette adresse normalisée, ou `None`.

        C'est la lecture qui décide « première venue » ou « retour » : elle
        doit être servie par l'index unique sur l'adresse.
        """
        ...

    async def add(self, user: User) -> None:
        """Crée un compte."""
        ...

    async def save(self, user: User) -> None:
        """Écrit l'état complet d'un compte existant."""
        ...

    async def delete(self, user_id: UUID) -> bool:
        """Efface le compte. Rend `False` s'il n'existait plus.

        Suppression **physique** : il ne reste ni ligne, ni adresse. C'est
        ce qui permet à la même adresse de s'inscrire à nouveau comme une
        première venue (ADR-0015).
        """
        ...


class ProviderIdentityRepository(Protocol):
    """Port : les rattachements d'un compte à Apple ou Google."""

    async def find(self, provider: SocialProvider, subject: str) -> ProviderIdentity | None:
        """Le rattachement correspondant à ce `sub`, s'il existe.

        C'est **la** requête du parcours social : elle décide entre « je
        connais cette personne » et « c'est une première fois ».
        """
        ...

    async def add(self, identity: ProviderIdentity) -> None:
        """Rattache un compte à un fournisseur."""
        ...

    async def touch(self, identity_id: UUID, at: datetime) -> None:
        """Note que ce rattachement vient de servir."""
        ...

    async def list_for(self, user_id: UUID) -> Sequence[ProviderIdentity]:
        """Les rattachements d'un compte, pour le support et les tests."""
        ...

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface tous les rattachements d'un compte. Rend le nombre effacé.

        Après quoi le `sub` Apple ou Google de la personne redevient inconnu :
        revenir par le même fournisseur crée un compte neuf.
        """
        ...


class SessionRepository(Protocol):
    """Port : accès persistant aux sessions."""

    async def add(self, session: Session) -> None:
        """Ouvre une session."""
        ...

    async def save(self, session: Session) -> None:
        """Écrit l'état complet d'une session existante.

        Raises:
            SessionNotFoundError: La session a disparu entre-temps.
        """
        ...

    async def find_by_access_fingerprint(self, fingerprint: str) -> Session | None:
        """Retrouve une session par le condensat de son jeton d'accès.

        **La** lecture du chemin critique : à chaque requête authentifiée.
        Servie par un index unique.
        """
        ...

    async def find_by_refresh_fingerprint(self, fingerprint: str) -> Session | None:
        """Retrouve la session **active** portant ce jeton de rafraîchissement."""
        ...

    async def find_by_previous_refresh(self, fingerprint: str) -> Session | None:
        """Retrouve une session dont ce jeton a été **périmé par rotation**.

        Le détecteur de vol : une correspondance signifie que deux acteurs
        détiennent la même session.
        """
        ...

    async def active_of_user(self, user_id: UUID) -> Sequence[Session]:
        """Les sessions ouvertes d'une personne, la plus récente d'abord."""
        ...

    async def end_all_of_user(
        self, user_id: UUID, *, reason: SessionEndReason, at: datetime
    ) -> int:
        """Clôt toutes les sessions d'une personne, en une instruction.

        Returns:
            Le nombre de sessions closes.
        """
        ...

    async def delete_all_of_user(self, user_id: UUID) -> int:
        """Efface **toutes** les sessions d'un compte, ouvertes ou closes.

        Pas une clôture : une suppression. Les sessions closes sont gardées
        pour le support ; un compte supprimé n'a plus de support à recevoir,
        et chaque ligne porte un `user_agent` qui est une donnée personnelle.
        """
        ...

    async def purge_ended_before(self, moment: datetime) -> int:
        """Supprime les sessions closes ou périmées depuis assez longtemps.

        Appelé par une commande d'exploitation, jamais au démarrage
        (AGENTS.md §6.4).
        """
        ...


class LoginChallengeRepository(Protocol):
    """Port : accès persistant aux parcours de connexion."""

    async def add(self, challenge: LoginChallenge) -> None:
        """Ouvre un parcours."""
        ...

    async def save(self, challenge: LoginChallenge) -> None:
        """Écrit l'état d'un parcours existant."""
        ...

    async def find(self, challenge_id: UUID) -> LoginChallenge | None:
        """Retourne le parcours, ou `None`.

        `None` — et non une erreur — pour un identifiant inconnu : le use
        case en tire la **même** erreur que pour un parcours périmé, de
        sorte qu'un identifiant leurre soit indiscernable d'un vrai.
        """
        ...

    async def purge_before(self, moment: datetime) -> int:
        """Supprime les parcours périmés depuis assez longtemps."""
        ...

    async def delete_for_email(self, email: EmailAddress) -> int:
        """Efface tous les parcours ouverts pour cette adresse, quel que soit leur état.

        Un parcours porte l'adresse en clair : il part avec le compte.
        """
        ...


class DepartureFeedbackRepository(Protocol):
    """Port : la trace anonyme des départs.

    Écriture seule depuis l'application : ce qu'on y range ne se relit
    jamais pour servir une personne — il n'y a d'ailleurs rien dedans qui
    la désigne. La lecture appartient à l'analyse produit, hors de l'API.
    """

    async def add(self, record: DepartureRecord) -> None:
        """Consigne un départ."""
        ...


# ---------------------------------------------------------------------
# Ce que les autres modules savent, et que l'on demande par un port
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MainDeviceSnapshot:
    """Ce qu'on retient de l'appareil principal d'une personne qui part.

    Deux champs, et pas la fiche entière : la trace de départ est anonyme,
    et un modèle d'appareil précis plus une version d'OS suffisent souvent
    à reconnaître quelqu'un dans un petit effectif.
    """

    platform: str
    app_version: str


class MainDeviceLookup(Protocol):
    """Port : l'appareil principal d'un compte, s'il en a déclaré un.

    Les appareils appartiennent au module `devices` ; `identity` n'a pas le
    droit de le lire (règle 4). Ce port dit le besoin — « sur quoi tournait
    l'app de cette personne ? » — et `bootstrap/wiring.py` y branche la
    réponse. L'implémentation par défaut répond « inconnu » : un branchement
    oublié produit une statistique moins riche, jamais une erreur.
    """

    async def main_device_of(self, user_id: UUID) -> MainDeviceSnapshot | None:
        """L'appareil vu en dernier, ou `None` si aucun n'est connu."""
        ...


# ---------------------------------------------------------------------
# Services techniques
# ---------------------------------------------------------------------


class TokenFactory(Protocol):
    """Port : fabrique et empreinte les secrets d'authentification.

    Isolé du domaine parce que produire de l'aléa cryptographique et
    calculer un condensat sont des actes **techniques**.
    """

    def new_token(self) -> str:
        """Produit un secret imprévisible, à usage de jeton."""
        ...

    def fingerprint(self, secret: str) -> str:
        """Le condensat sous lequel un secret est stocké. Déterministe."""
        ...

    def new_numeric_code(self, digits: int) -> str:
        """Produit un code numérique imprévisible, zéros de tête compris."""
        ...

    def matches(self, secret: str, fingerprint: str) -> bool:
        """Compare un secret à une empreinte, à temps constant."""
        ...


class LoginCodeSender(Protocol):
    """Port : achemine un code de connexion jusqu'à la personne.

    Le module ignore par quoi il part — SMTP, console. C'est ce qui permet
    d'exercer le parcours sans jamais envoyer un courriel réel.
    """

    async def send(
        self, *, email: str, code: str, expires_in_minutes: int, language: Language
    ) -> None:
        """Achemine le code, rédigé dans la langue indiquée.

        Raises:
            DependencyUnavailableError: Le transport n'a pas accepté le
                message. La personne doit pouvoir réessayer, pas rester
                bloquée devant un parcours ouvert sans code.
        """
        ...


class LoginThrottle(Protocol):
    """Port : borne le nombre de tentatives.

    Compté par **cible et par origine** : borner seulement l'origine laisse
    un botnet distribuer ses essais, borner seulement la cible permet de
    bloquer le compte d'autrui à volonté.
    """

    async def ensure_allowed(self, *, key: str) -> None:
        """Vérifie qu'une tentative supplémentaire est permise.

        Raises:
            TooManyAttemptsError: Le plafond est atteint.
        """
        ...

    async def record_failure(self, *, key: str) -> None:
        """Consigne une tentative."""
        ...

    async def reset(self, *, key: str) -> None:
        """Efface le compteur après une réussite."""
        ...


class SocialTokenVerifier(Protocol):
    """Port : transformer une affirmation du client en preuve.

    Un port parce que la vérification appelle le réseau — les clés publiques
    du fournisseur. Derrière lui, un test injecte un profil déjà vérifié et
    se concentre sur la décision : rattacher, créer, refuser.

    Une implémentation doit vérifier **signature, émetteur, audience,
    expiration et nonce**. Aucun n'est facultatif (ADR-0011).
    """

    @property
    def provider(self) -> SocialProvider:
        """Le fournisseur dont ce vérificateur accepte les jetons."""
        ...

    async def verify(self, token: str, *, nonce: str | None) -> VerifiedSocialProfile:
        """Vérifie et rend ce que le fournisseur affirme.

        Raises:
            InvalidSocialTokenError: L'un des cinq contrôles a échoué.
        """
        ...


class JwksSource(Protocol):
    """Port : fournit les clés publiques (JWKS) d'un fournisseur.

    Séparé du vérificateur pour que la cryptographie **réelle** se teste :
    un test fournit un JWKS construit à partir d'une clé RSA générée
    localement, signe un jeton avec la clé privée, et le vérificateur de
    production le valide — ou le refuse — pour de vrai.
    """

    async def fetch(self, url: str) -> dict[str, Any]:
        """Rend le document JWKS de cette URL, éventuellement depuis un cache."""
        ...

    async def refresh(self, url: str) -> dict[str, Any]:
        """Force une relecture : un `kid` inconnu signale une rotation de clés."""
        ...
