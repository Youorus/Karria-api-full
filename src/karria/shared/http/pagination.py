"""Pagination normalisée.

Contrat
-------
Toutes les collections de l'API se paginent **de la même manière** et
renvoient **la même enveloppe**. La V1 exposait des listes nues : le jour
où une collection dépasse quelques milliers d'éléments, la corriger devient
un changement cassant pour tous les clients. On paie la contrainte
maintenant, une fois.

Le plafond de `limit` n'est pas négociable côté client : sans lui, un seul
appel peut saturer la mémoire du processus.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class PaginationParams(BaseModel):
    """Paramètres de pagination, injectables comme dépendance FastAPI."""

    limit: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description="Nombre maximum d'éléments retournés."),
    ] = DEFAULT_PAGE_SIZE
    offset: Annotated[int, Query(ge=0, description="Nombre d'éléments ignorés.")] = 0


class Page[ItemT](BaseModel):
    """Fragment de collection accompagné de son contexte."""

    items: Sequence[ItemT]
    total: int = Field(description="Nombre total d'éléments, tous fragments confondus.")
    limit: int
    offset: int

    @property
    def has_next(self) -> bool:
        """Indique s'il reste des éléments au-delà de ce fragment."""
        return self.offset + len(self.items) < self.total

    @classmethod
    def build(cls, items: Sequence[ItemT], *, total: int, params: PaginationParams) -> Page[ItemT]:
        """Assemble un fragment à partir des paramètres de la requête."""
        return cls(items=items, total=total, limit=params.limit, offset=params.offset)
