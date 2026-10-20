"""Modèle de persistance de base.

Vocabulaire — important pour ne pas reproduire la confusion de la V1 :
ces classes sont des **modèles de persistance**, pas des entités de
domaine. Elles décrivent comment une donnée est stockée, pas ce qu'elle
signifie. Elles vivent donc exclusivement dans `infrastructure/` et ne
franchissent jamais la frontière d'un module : un repository renvoie des
DTOs de la couche `application`, jamais un modèle Tortoise.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from tortoise import fields
from tortoise.models import Model


class BaseDBModel(Model):
    """Base commune : identifiant opaque et horodatage d'audit.

    Clé primaire en UUID plutôt qu'en entier auto-incrémenté :
    - un identifiant séquentiel exposé dans une URL divulgue le volume de
      données et permet l'énumération des ressources ;
    - un UUID peut être généré côté application, avant tout aller-retour
      avec la base, ce qui simplifie les opérations en lot et les tests.

    Contrepartie assumée : un UUID aléatoire disperse les écritures dans
    l'index. Si le volume d'une table rend ce coût mesurable, la réponse
    sera un UUID ordonné dans le temps (v7) sur cette table précise — pas
    un retour à l'entier séquentiel.
    """

    id: UUID = fields.UUIDField(primary_key=True, default=uuid4)
    created_at = fields.DatetimeField(auto_now_add=True, description="Création (UTC).")
    updated_at = fields.DatetimeField(auto_now=True, description="Dernière modification (UTC).")

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.id})"
