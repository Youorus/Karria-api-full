"""Événements publiés par le module `cv`.

Un fait accompli, au passé, et rien d'autre. Le module qui porte les
comptes pourra s'y abonner pour recopier l'identité dans le profil : le
contrat dit que la finalisation « met à jour l'identité du profil », et la
règle 4 interdit d'aller le faire nous-mêmes.
"""

from __future__ import annotations

from uuid import UUID

from karria.shared.events import DomainEvent


class CvFinalized(DomainEvent):
    """Un brouillon est devenu le CV de la personne."""

    user_id: UUID
    cv_id: UUID
    first_name: str | None = None
    last_name: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
