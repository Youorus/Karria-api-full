"""Événements publiés par `identity`.

Des faits accomplis, au passé. Les autres modules s'y abonnent par le bus
sans jamais importer `identity` (règle 4) : c'est par là qu'un module
saura qu'un compte vient de naître, sans lire sa table.
"""

from __future__ import annotations

from uuid import UUID

from karria.shared.events import DomainEvent


class UserRegistered(DomainEvent):
    """Un compte vient d'être créé — par code e-mail, Apple ou Google."""

    user_id: UUID


class UserSignedIn(DomainEvent):
    """Une session vient de s'ouvrir.

    Attributes:
        created_account: Vrai si cette connexion a aussi créé le compte.
    """

    user_id: UUID
    created_account: bool


class AccountDeleted(DomainEvent):
    """Un compte vient d'être supprimé, à la demande de la personne.

    Publié **après** la transaction qui a effacé le compte : quand un module
    le reçoit, la personne n'a plus de session et son adresse n'existe plus
    chez `identity`. Chaque module qui référence un `user_id` doit alors
    effacer ce qu'il en détient — lignes et objets (ADR-0015). Rien d'autre
    que l'identifiant ne voyage : l'adresse est déjà partie.
    """

    user_id: UUID
