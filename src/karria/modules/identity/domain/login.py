"""Le parcours de connexion par code e-mail.

Ce qu'il est
------------
Une preuve de **possession** : la personne montre qu'elle relève la boîte
mail dont elle donne l'adresse. Aucun mot de passe — donc rien qui puisse
être deviné, réutilisé d'un autre site ou lu dans une fuite.

Compte existant ou non, le parcours est **le même** : c'est la validation
du code qui crée le compte s'il n'existe pas. Entre les deux, l'app ne sait
pas — et ne doit pas savoir — si l'adresse était connue.

Pourquoi le code vit dans le parcours
-------------------------------------
`zoumani_api` séparait le code (`OneTimeCode`) du parcours, parce que ses
codes servaient à quatre usages. Karria n'en a qu'un : connecter. Un objet
unique porte donc l'adresse, le condensat du code, les essais et les
envois. Moins de tables, et surtout une seule chose à périmer.

Ce que ce fichier ne contient pas
---------------------------------
Aucun code en clair, aucune génération d'aléa. Le parcours ne porte que le
**condensat** du code ; le comparer est un acte technique (`TokenFactory`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from karria.modules.identity.domain.errors import (
    LoginChallengeExpiredError,
    LoginCodeExhaustedError,
    LoginCodeResendExhaustedError,
    LoginCodeResendTooSoonError,
)

#: Longueur du code. Six chiffres : assez pour que cinq essais ne mènent
#: nulle part, assez court pour être recopié depuis un e-mail sans erreur.
CODE_DIGITS: Final = 6

#: Durée de vie du parcours — et du code, qui ne lui survit pas. Dix
#: minutes : assez pour relever un e-mail lent, trop peu pour qu'un
#: parcours abandonné sur un poste public reste franchissable le lendemain.
#: C'est l'`expires_in: 600` du contrat de l'app.
LOGIN_CHALLENGE_LIFETIME: Final = timedelta(minutes=10)

#: Délai avant de pouvoir redemander un code.
#:
#: Ce n'est pas une punition, c'est une protection contre soi-même : un
#: renvoi périme le code précédent. Sans délai, quelqu'un qui appuie deux
#: fois reçoit deux e-mails, saisit celui arrivé en premier, et se voit
#: refuser un code pourtant exact. Trente secondes : le
#: `seconds_before_resend: 30` du contrat.
LOGIN_CODE_RESEND_DELAY: Final = timedelta(seconds=30)

#: Envois du code sur un même parcours, le premier compris. Au-delà, ce
#: n'est plus « l'e-mail n'arrive pas » : c'est une adresse qui ne répond
#: pas, ou quelqu'un qui s'en sert pour en inonder une autre.
MAX_CODE_SENDS: Final = 4

#: Tentatives autorisées sur un code. Cinq essais sur un million de
#: combinaisons rendent la force brute inopérante ; au-delà, le code est
#: brûlé et il faut en redemander un (`essais >= 5` dans le simulateur).
MAX_CODE_ATTEMPTS: Final = 5


class LoginChallengeStatus(StrEnum):
    """Où en est le parcours."""

    #: Un code est parti ; on l'attend.
    OPEN = "open"
    #: Le code a été validé. Le parcours a produit une session.
    COMPLETED = "completed"
    #: Trop d'essais : le code est brûlé.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    """Un parcours de connexion en cours.

    Attributes:
        email: Adresse visée, normalisée. C'est elle qui fait le lien
            entre les trois appels, parce qu'un compte peut ne pas exister
            encore.
        user_id: Compte visé. `None` tant qu'aucun n'existe — le parcours
            sert alors à en créer un à la validation.
        code_hash: Condensat du code en cours. Remplacé à chaque renvoi.
        code_sent_at: Dernier envoi. C'est la **demande** qu'on limite.
        code_sends: Nombre d'envois, celui d'ouverture compris.
        attempts: Essais infructueux sur le code en cours.
        created_account: Le parcours a-t-il **créé** le compte ? Sert à
            l'app pour afficher un accueil, et au support pour distinguer
            une première venue d'un retour.
        terms_version: Version des conditions acceptées en continuant.
            Portée par le parcours parce que le compte n'existe pas encore
            au moment où l'app l'envoie ; elle est horodatée sur le compte
            à la validation du code.
        privacy_policy_version: Idem, pour la politique de confidentialité.
    """

    id: UUID
    email: str
    user_id: UUID | None
    code_hash: str
    status: LoginChallengeStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    code_sent_at: datetime
    terms_version: str
    privacy_policy_version: str
    code_sends: int = 1
    attempts: int = 0
    completed_at: datetime | None = None
    created_account: bool = False

    @property
    def is_open(self) -> bool:
        """Le parcours peut-il encore avancer ?"""
        return self.status is LoginChallengeStatus.OPEN

    def ensure_usable_at(self, moment: datetime) -> None:
        """Vérifie que le parcours peut encore avancer.

        Raises:
            LoginChallengeExpiredError: Périmé, brûlé ou déjà consommé. Un
                seul message pour les trois : dire lequel renseignerait qui
                tâtonne sur des identifiants.
        """
        if not self.is_open or moment >= self.expires_at:
            raise LoginChallengeExpiredError

    def seconds_until_expiry(self, moment: datetime) -> int:
        """Ce qu'il reste de validité, en secondes, jamais négatif."""
        return max(0, int((self.expires_at - moment).total_seconds()))

    def seconds_before_resend(self, moment: datetime) -> int:
        """Ce qu'il reste à patienter avant un renvoi, jamais négatif.

        Rendu à chaque réponse pour que l'app arme son décompte sur
        l'horloge du serveur : une constante recopiée côté client finirait
        par diverger, et c'est l'affichage qui aurait tort.
        """
        remaining = (self.code_sent_at + LOGIN_CODE_RESEND_DELAY - moment).total_seconds()
        return max(0, int(remaining + 0.999))

    def resent(self, *, code_hash: str, at: datetime) -> LoginChallenge:
        """Enregistre un nouvel envoi : nouveau code, essais remis à zéro.

        L'ordre des refus compte : d'abord ce qui se répare en attendant,
        puis ce qui ne se répare qu'en recommençant. L'inverse dirait
        « recommencez » à quelqu'un qui n'a qu'à patienter dix secondes.

        Raises:
            LoginCodeResendTooSoonError: Le délai court encore.
            LoginCodeResendExhaustedError: Trop d'envois sur ce parcours.
        """
        remaining = self.seconds_before_resend(at)
        if remaining > 0:
            raise LoginCodeResendTooSoonError(remaining)
        if self.code_sends >= MAX_CODE_SENDS:
            raise LoginCodeResendExhaustedError(self.seconds_until_expiry(at))
        return replace(
            self,
            code_hash=code_hash,
            code_sent_at=at,
            code_sends=self.code_sends + 1,
            attempts=0,
            updated_at=at,
        )

    @property
    def remaining_attempts(self) -> int:
        """Essais restants avant que le code ne soit brûlé."""
        return max(0, MAX_CODE_ATTEMPTS - self.attempts)

    def failed_attempt(self, *, at: datetime) -> LoginChallenge:
        """Compte un essai infructueux ; brûle le parcours au dernier.

        Brûlé et non supprimé : un support qui reçoit « je n'arrive pas à
        me connecter » a besoin de voir où la personne a buté.
        """
        attempts = self.attempts + 1
        status = LoginChallengeStatus.FAILED if attempts >= MAX_CODE_ATTEMPTS else self.status
        return replace(self, attempts=attempts, status=status, updated_at=at)

    def ensure_attempt_allowed(self) -> None:
        """Vérifie qu'un essai de plus est permis.

        Raises:
            LoginCodeExhaustedError: Le code est brûlé.
        """
        if self.attempts >= MAX_CODE_ATTEMPTS:
            raise LoginCodeExhaustedError

    def completed(self, *, user_id: UUID, created_account: bool, at: datetime) -> LoginChallenge:
        """Clôt le parcours : le code a été validé, la session va s'ouvrir."""
        return replace(
            self,
            status=LoginChallengeStatus.COMPLETED,
            user_id=user_id,
            created_account=created_account,
            completed_at=at,
            updated_at=at,
        )


def open_challenge(
    *,
    challenge_id: UUID,
    email: str,
    user_id: UUID | None,
    code_hash: str,
    terms_version: str,
    privacy_policy_version: str,
    at: datetime,
) -> LoginChallenge:
    """Ouvre un parcours, aux durées du domaine."""
    return LoginChallenge(
        id=challenge_id,
        email=email,
        user_id=user_id,
        code_hash=code_hash,
        status=LoginChallengeStatus.OPEN,
        expires_at=at + LOGIN_CHALLENGE_LIFETIME,
        created_at=at,
        updated_at=at,
        code_sent_at=at,
        terms_version=terms_version,
        privacy_policy_version=privacy_policy_version,
    )
