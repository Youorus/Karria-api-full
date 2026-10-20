"""Socle des repositories Tortoise.

Ce que ce socle règle
---------------------
La V1 plaçait les requêtes ORM directement dans les services : le métier
était soudé à Tortoise et intestable sans base réelle. Ici, **seule** une
implémentation de repository touche l'ORM. Le use case, lui, ne connaît
qu'un `Protocol` déclaré dans sa couche `application`.

Ce que ce socle ne fait PAS, volontairement
-------------------------------------------
Aucun `create`/`update` générique. Une écriture est un acte métier
(« publier », « archiver », « clôturer ») et son repository doit l'exprimer
avec ce vocabulaire. Un `save(**kwargs)` universel redonnerait à l'appelant
un accès direct au modèle et annulerait tout le bénéfice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from karria.shared.database.base import BaseDBModel
from karria.shared.errors import EntityNotFoundError


class TortoiseRepository[ModelT: BaseDBModel]:
    """Opérations de lecture communes à tous les repositories.

    Le nom d'entité est fourni explicitement : il apparaît dans les messages
    d'erreur renvoyés au client, qui doivent parler le langage du métier
    (« Commande introuvable ») et non celui du schéma (« OrderRow »).
    """

    def __init__(self, model: type[ModelT], *, entity_name: str) -> None:
        self._model = model
        self._entity_name = entity_name

    async def find_by_id(self, entity_id: Any) -> ModelT | None:
        """Retourne l'enregistrement, ou `None` s'il n'existe pas."""
        return await self._model.get_or_none(id=entity_id)

    async def get_by_id(self, entity_id: Any) -> ModelT:
        """Retourne l'enregistrement ou lève `EntityNotFoundError`.

        À utiliser quand l'absence est une anomalie ; `find_by_id` quand
        l'absence est un cas nominal.
        """
        entity = await self._model.get_or_none(id=entity_id)
        if entity is None:
            raise EntityNotFoundError(self._entity_name, entity_id)
        return entity

    async def exists(self, **filters: Any) -> bool:
        """Indique si au moins un enregistrement satisfait les critères."""
        return await self._model.filter(**filters).exists()

    async def count(self, **filters: Any) -> int:
        """Compte les enregistrements satisfaisant les critères."""
        return await self._model.filter(**filters).count()

    async def list_page(
        self, *, limit: int, offset: int, order_by: str = "-created_at", **filters: Any
    ) -> Sequence[ModelT]:
        """Retourne un fragment ordonné de la collection.

        L'ordre est explicite et par défaut déterministe : une pagination
        sans tri stable renvoie des doublons et des trous entre deux pages,
        un bug qui ne se manifeste qu'en production avec du volume.
        """
        return await self._model.filter(**filters).order_by(order_by).offset(offset).limit(limit)

    async def delete_by_id(self, entity_id: Any) -> None:
        """Supprime l'enregistrement, ou lève `EntityNotFoundError`."""
        deleted = await self._model.filter(id=entity_id).delete()
        if not deleted:
            raise EntityNotFoundError(self._entity_name, entity_id)
