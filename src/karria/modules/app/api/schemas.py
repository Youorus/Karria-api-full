"""Schémas HTTP du module `app` — `docs/contrat-api-documents.md` § version minimale."""

from __future__ import annotations

from pydantic import BaseModel, Field

from karria.modules.app.domain import ReleaseStatus


class ReleaseRead(BaseModel):
    """Ce que l'app installée doit faire de sa version."""

    status: ReleaseStatus = Field(description="`ok`, `update_available` ou `update_required`.")
    latest_build: int = Field(description="Dernier build publié ; `0` si inconnu.")
    minimum_build: int = Field(description="Plus ancien build encore admis ; `0` sans contrainte.")
    latest_version: str = Field(
        default="",
        description="Le numéro lisible de la dernière version (« 1.2.0 ») ; vide si inconnu.",
    )
    store_url: str = Field(description="La fiche du magasin ; vide si elle n'existe pas encore.")
