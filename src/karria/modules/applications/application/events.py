"""Événements publiés par `applications`.

Des faits accomplis, au passé. `bootstrap/wiring.py` peut y abonner un
autre module sans qu'aucun des deux importe l'autre (règle 4) : c'est
par là que le fil d'offres apprend qu'une personne a candidaté.
"""

from __future__ import annotations

from uuid import UUID

from karria.shared.events import DomainEvent


class DossierApplied(DomainEvent):
    """La personne a dit avoir candidaté à cette offre depuis ce dossier."""

    user_id: UUID
    offer_id: UUID
    dossier_id: UUID


class DossierUnapplied(DomainEvent):
    """Elle est revenue sur cette déclaration."""

    user_id: UUID
    offer_id: UUID
    dossier_id: UUID
