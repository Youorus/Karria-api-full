"""Modèles de persistance du module `cv`.

Trois tables, et pourquoi pas sept
----------------------------------
La V1 rangeait le CV dans `cvs` plus six tables de sections. Ici les
sections sont des colonnes **JSONB** du CV (ADR-0014) : un CV se lit et
s'écrit toujours entier, aucune requête ne filtre une expérience par
entreprise, et chaque élément porte déjà un `id` stable dans le JSON. Six
tables, six clés étrangères et six migrations n'achèteraient rien — sinon
la possibilité de requêter ce qu'on ne requête pas.

Le brouillon suit la même logique : ses chapitres sont un seul document
JSON, relu et validé par le domaine à chaque chargement.

`user_id` est un `UUID` **sans clé étrangère** : les comptes appartiennent à
un autre module (règle 4). La cohérence référentielle est assurée par le
bus — un compte supprimé publiera un événement auquel ce module réagira.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from tortoise import fields

from karria.shared.database import BaseDBModel
from karria.shared.storage import KEY_MAX_LENGTH


class CvDraftModel(BaseDBModel):
    """Le brouillon en cours d'une personne. Un seul par `user_id`."""

    user_id: UUID = fields.UUIDField(unique=True, description="Compte propriétaire.")
    chapters: dict[str, Any] = fields.JSONField(
        description="État des chapitres : {clé: {status, data, items}}."
    )

    class Meta:
        table = "cv_drafts"


class CurriculumVitaeModel(BaseDBModel):
    """Le CV d'une personne, sections comprises. Un seul par `user_id`."""

    user_id: UUID = fields.UUIDField(unique=True, description="Compte propriétaire.")
    identity: dict[str, Any] | None = fields.JSONField(null=True)
    contact: dict[str, Any] | None = fields.JSONField(null=True)
    summary: str | None = fields.TextField(null=True)
    image_key = fields.CharField(
        max_length=KEY_MAX_LENGTH, null=True, description="Clé objet de la photo (bucket media)."
    )
    experiences: list[dict[str, Any]] = fields.JSONField(default=list)
    educations: list[dict[str, Any]] = fields.JSONField(default=list)
    skills: list[dict[str, Any]] = fields.JSONField(default=list)
    languages: list[dict[str, Any]] = fields.JSONField(default=list)
    interests: list[dict[str, Any]] = fields.JSONField(default=list)
    projects: list[dict[str, Any]] = fields.JSONField(default=list)

    class Meta:
        table = "cv_curricula"


class CvPhotoModel(BaseDBModel):
    """Une photo envoyée pour le CV, en attente d'être retenue ou oubliée."""

    user_id: UUID = fields.UUIDField(db_index=True, description="Compte propriétaire.")
    image_key = fields.CharField(max_length=KEY_MAX_LENGTH)
    enhanced = fields.BooleanField(default=False)

    class Meta:
        table = "cv_photos"
