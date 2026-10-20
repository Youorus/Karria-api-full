"""Unité de travail : la transaction, exprimée sans nommer la base.

Le problème résolu
------------------
Un use case a besoin de dire « ces trois écritures réussissent ensemble ou
échouent ensemble ». S'il l'exprime avec `tortoise.transactions.in_transaction`,
la couche `application` importe l'ORM — et devient impossible à tester sans
PostgreSQL, en plus d'être soudée à Tortoise pour toujours.

Le port `UnitOfWork` déplace ce choix dans `infrastructure`. Le use case
déclare son besoin d'atomicité ; il ignore comment il est satisfait.

Note sur Tortoise
-----------------
Tortoise n'expose pas d'objet session : la transaction est portée par un
`contextvar` de connexion. C'est précisément pour cela que le port n'expose
aucune session — il n'exposerait qu'un détail d'implémentation qu'un futur
remplacement d'ORM invaliderait.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from tortoise.transactions import in_transaction

from karria.shared.database.config import DEFAULT_CONNECTION


class UnitOfWork(Protocol):
    """Port : délimite une frontière transactionnelle."""

    def atomic(self) -> AbstractAsyncContextManager[None]:
        """Ouvre une transaction ; valide en sortie, annule en cas d'exception."""
        ...


class TortoiseUnitOfWork:
    """Implémentation du port au-dessus de Tortoise."""

    def __init__(self, connection_name: str = DEFAULT_CONNECTION) -> None:
        self._connection_name = connection_name

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        """Exécute le bloc dans une transaction unique."""
        async with in_transaction(self._connection_name):
            yield


class NullUnitOfWork:
    """Implémentation neutre pour les tests unitaires.

    Permet d'exercer un use case sans base de données : l'atomicité n'est
    pas ce que teste un test unitaire, elle relève du test d'intégration.
    """

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        """N'ouvre aucune transaction."""
        yield
