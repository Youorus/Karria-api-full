"""Modèles de persistance du module `applications`.

Une table, et pourquoi pas quatre
---------------------------------
Un dossier se lit et s'écrit toujours entier : la copie adaptée du CV, les
propositions de Karria avec leur état, la lettre et la couverture des
mots-clés sont des colonnes **JSONB** (comme le CV lui-même, ADR-0014). Une
table de propositions ou de paragraphes n'achèterait que des jointures
pour une requête que personne ne fait.

`user_id` et `offer_id` sont des `UUID` **sans clé étrangère** : les comptes
et les offres appartiennent à d'autres modules (règle 4). L'offre peut
disparaître de l'index ; le dossier garde ce qu'il en a figé
(`offer_snapshot`) et reste lisible. La suppression d'un compte efface les
dossiers par le bus (ADR-0015).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from tortoise import fields

from karria.shared.database import BaseDBModel


class ApplicationDossierModel(BaseDBModel):
    """Un dossier de candidature : une offre figée, un CV adapté, une lettre."""

    user_id: UUID = fields.UUIDField(
        db_index=True,
        description="Le compte. Pas de clé étrangère : `applications` ne lit pas `identity`.",
    )
    offer_id: UUID = fields.UUIDField(
        description="L'offre visée (module `offers`), sans clé étrangère."
    )
    offer_snapshot: dict[str, Any] = fields.JSONField(
        description="L'offre figée à la création : titre, entreprise, logo, ville, contrat, texte, mots-clés."
    )
    template_id: UUID | None = fields.UUIDField(
        null=True, description="Le modèle de CV choisi (module `documents`), ou aucun."
    )
    cv: dict[str, Any] = fields.JSONField(
        description="La copie adaptée du CV, au format `GET /cv/me`."
    )
    cv_base_hash = fields.CharField(
        max_length=64, description="Condensat du CV de base copié : dit s'il a changé depuis."
    )
    propositions: list[dict[str, Any]] = fields.JSONField(
        default=list, description="Les propositions de Karria : chemin, avant, après, raison, état."
    )
    letter: dict[str, Any] | None = fields.JSONField(
        null=True,
        description="La lettre de motivation : destinataire, objet, paragraphes, formule.",
    )
    coverage: dict[str, Any] = fields.JSONField(
        default=dict, description="Mots-clés de l'offre présents et absents du CV adapté."
    )
    status = fields.CharField(max_length=16, description="brouillon, pret ou candidate.")
    adaptation = fields.CharField(max_length=16, description="en_cours, faite ou echouee.")
    adaptation_error = fields.CharField(
        max_length=64, null=True, description="La raison d'un échec d'adaptation, courte."
    )
    applied_at = fields.DatetimeField(
        null=True, description="Quand la personne a dit avoir candidaté."
    )
    primary_color = fields.CharField(
        max_length=7,
        null=True,
        description="Couleur principale posée sur le modèle du dossier (#RRGGBB), ou NULL.",
    )
    letter_style = fields.CharField(
        max_length=8,
        null=True,
        description="Style de lettre retenu (template ou plain), ou NULL (ADR-0036).",
    )

    class Meta:
        table = "applications_dossiers"
        table_description = (
            "Les dossiers de candidature : offre figée, CV adapté, lettre, propositions."
        )
        unique_together = (("user_id", "offer_id"),)
        indexes = (("user_id", "updated_at"),)
