"""Sessions : prouver qu'on est bien soi, à chaque requête.

Ce que ce fichier ne contient pas
---------------------------------
**Aucun jeton en clair.** Une session ne porte que le *condensat* de ses
jetons. Une fuite de la table ne permet pas de se faire passer pour
quelqu'un — l'attaquant obtiendrait des empreintes, pas des clés.

Aucune génération d'aléa non plus : produire un secret est un acte
technique, il vit dans `infrastructure/`. Le domaine décide **quand** une
session est valide, pas **comment** on fabrique son jeton.

La rotation, et pourquoi elle change tout
-----------------------------------------
Chaque usage du jeton de rafraîchissement en émet un nouveau et invalide
l'ancien. Sans rotation, un jeton volé reste utilisable en silence. Avec
elle, la première utilisation — par le voleur **ou** par la victime —
invalide l'autre, et la tentative suivante trahit le vol : toutes les
sessions de la personne tombent (ADR-0010).

Repris de `zoumani_api` (ADR-0010, 0034 de ce cadre) : la mécanique a
tourné un an en production, y compris la tolérance d'une minute au rejeu.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from karria.modules.identity.domain.errors import SessionExpiredError, SessionRevokedError

#: Durée de vie du jeton d'accès. Court à dessein : c'est lui qui circule à
#: chaque requête, donc lui qui a le plus d'occasions d'être intercepté.
#: Quinze minutes bornent la fenêtre d'exploitation d'un vol sans imposer
#: un rafraîchissement permanent.
ACCESS_TOKEN_LIFETIME: Final = timedelta(minutes=15)

#: Durée de vie du jeton de rafraîchissement, **glissante** : chaque
#: rotation la repousse. Un an, parce que la règle attendue d'une
#: application mobile n'est pas « rester connecté longtemps » mais « ne
#: jamais être déconnecté » : personne ne se reconnecte à son application
#: de messagerie parce qu'il ne l'a pas ouverte de l'été. La durée ne mord
#: donc que sur une absence *continue* d'un an ; la session se perd
#: autrement par déconnexion ou révocation, jamais par usage.
REFRESH_TOKEN_LIFETIME: Final = timedelta(days=365)

#: Fenêtre pendant laquelle le rejeu du jeton de rafraîchissement
#: **précédent** est tenu pour un doublon, et non pour un vol.
#:
#: Trois requêtes qui expirent ensemble font partir trois rafraîchissements
#: avec le même jeton ; l'app les sérialise (`lib/http.ts`), mais une
#: réponse perdue en réseau mobile rejoue le même jeton quelques secondes
#: plus tard. Un voleur, lui, rejoue des heures ou des jours après : une
#: minute ne lui ouvre rien. Le rejeu reste **refusé** — l'ancien jeton ne
#: redevient jamais valide — mais ne ferme plus toutes les sessions.
REFRESH_REUSE_GRACE: Final = timedelta(seconds=60)

#: Sessions simultanées par personne. Un téléphone, une tablette, un
#: ancien téléphone, et de la marge. Au-delà, la plus ancienne cède la
#: place : des sessions oubliées qui s'accumulent, ce sont autant de portes
#: laissées ouvertes sur de vieux appareils.
MAX_SESSIONS_PER_USER: Final = 10


class SessionEndReason(StrEnum):
    """Pourquoi une session a pris fin.

    Conservé plutôt qu'un booléen : « déconnexion volontaire » et
    « réutilisation de jeton détectée » ne demandent ni la même réaction du
    support, ni la même alerte.
    """

    LOGGED_OUT = "logged_out"
    #: Élaguée au-delà du plafond de sessions, ou fermée depuis une autre.
    LOGGED_OUT_EVERYWHERE = "logged_out_everywhere"
    #: Un jeton de rafraîchissement déjà consommé a resservi hors de la
    #: fenêtre de tolérance. Signature d'un vol : on coupe tout.
    TOKEN_REUSE_DETECTED = "token_reuse_detected"  # noqa: S105 - un motif, pas un secret


@dataclass(frozen=True, slots=True)
class Session:
    """Une session ouverte, ou close.

    Attributes:
        access_token_hash: Condensat du jeton d'accès. **Jamais le jeton.**
        refresh_token_hash: Condensat du jeton de rafraîchissement courant.
        previous_refresh_token_hash: Condensat de la génération
            **précédente**. Sans lui, faire tourner le jeton effacerait
            toute trace de l'ancien : un rejeu ressemblerait à un jeton
            inconnu, et le vol passerait inaperçu.
        rotation_count: Nombre de rafraîchissements. Diagnostic : rend
            visible une session anormalement bavarde.
        user_agent: Client déclaré, tronqué. Pour le support.
    """

    id: UUID
    user_id: UUID
    access_token_hash: str
    refresh_token_hash: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    created_at: datetime
    updated_at: datetime

    previous_refresh_token_hash: str | None = None
    rotation_count: int = 0
    user_agent: str | None = None
    last_used_at: datetime | None = None
    ended_at: datetime | None = None
    end_reason: SessionEndReason | None = None

    @property
    def is_active(self) -> bool:
        """La session est-elle encore ouverte ?"""
        return self.ended_at is None

    def access_is_valid_at(self, moment: datetime) -> bool:
        """Le jeton d'accès est-il utilisable à cet instant ?"""
        return self.is_active and moment < self.access_expires_at

    def ensure_refreshable_at(self, moment: datetime) -> None:
        """Vérifie qu'on peut encore faire tourner cette session.

        Distingue **révoquée** et **expirée** : le client n'a pas la même
        conduite à tenir, et le support non plus.

        Raises:
            SessionRevokedError: La session a été close.
            SessionExpiredError: La fenêtre glissante est dépassée.
        """
        if not self.is_active:
            raise SessionRevokedError(self.end_reason.value if self.end_reason else None)
        if moment >= self.refresh_expires_at:
            raise SessionExpiredError

    @property
    def issued_at(self) -> datetime:
        """Quand le couple de jetons courant a été émis.

        Dérivé plutôt que stocké : l'ouverture comme la rotation posent
        `access_expires_at` à l'instant d'émission plus la durée d'accès.
        """
        return self.access_expires_at - ACCESS_TOKEN_LIFETIME

    def reuse_is_benign_at(self, moment: datetime) -> bool:
        """Le rejeu du jeton précédent est-il un doublon plutôt qu'un vol ?

        Vrai si la session est ouverte et que la rotation date de moins de
        `REFRESH_REUSE_GRACE`.
        """
        return self.is_active and moment - self.issued_at <= REFRESH_REUSE_GRACE

    def rotated(self, *, access_token_hash: str, refresh_token_hash: str, at: datetime) -> Session:
        """Émet un nouveau couple de jetons et périme l'ancien.

        La fenêtre de rafraîchissement repart de zéro : c'est ce qui rend
        la session glissante.
        """
        return replace(
            self,
            access_token_hash=access_token_hash,
            refresh_token_hash=refresh_token_hash,
            previous_refresh_token_hash=self.refresh_token_hash,
            access_expires_at=at + ACCESS_TOKEN_LIFETIME,
            refresh_expires_at=at + REFRESH_TOKEN_LIFETIME,
            rotation_count=self.rotation_count + 1,
            last_used_at=at,
            updated_at=at,
        )

    def touched(self, *, at: datetime) -> Session:
        """Note que la session vient de servir."""
        return replace(self, last_used_at=at, updated_at=at)

    def should_record_use(self, at: datetime, *, granularity: timedelta) -> bool:
        """Faut-il écrire la date de dernier usage ?

        Une session active produit des dizaines de requêtes par minute.
        Horodater chacune ferait de chaque `GET` une écriture. On n'écrit
        que si la dernière trace date d'assez longtemps.
        """
        if self.last_used_at is None:
            return True
        return at - self.last_used_at >= granularity

    def ended(self, *, reason: SessionEndReason, at: datetime) -> Session:
        """Clôt la session en conservant la raison.

        La ligne n'est pas supprimée : un support qui reçoit « je me fais
        déconnecter sans arrêt » a besoin de lire ce qui s'est passé.
        """
        return replace(self, ended_at=at, end_reason=reason, updated_at=at)


def open_session(
    *,
    session_id: UUID,
    user_id: UUID,
    access_token_hash: str,
    refresh_token_hash: str,
    at: datetime,
    user_agent: str | None,
) -> Session:
    """Construit une session neuve, aux durées du domaine.

    Écrit ici pour que l'ouverture et la rotation posent les mêmes
    échéances : `issued_at` en dépend.
    """
    return Session(
        id=session_id,
        user_id=user_id,
        access_token_hash=access_token_hash,
        refresh_token_hash=refresh_token_hash,
        access_expires_at=at + ACCESS_TOKEN_LIFETIME,
        refresh_expires_at=at + REFRESH_TOKEN_LIFETIME,
        created_at=at,
        updated_at=at,
        last_used_at=at,
        user_agent=user_agent,
    )
