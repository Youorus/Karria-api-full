"""Bus d'événements in-process.

Raison d'être
-------------
C'est le mécanisme qui rend le découplage inter-modules praticable. Sans
lui, « le module facturation doit réagir à une inscription » se traduit
inévitablement par un import direct — et c'est ainsi que la V1 a fini avec
un graphe de dépendances où quatre-vingt-dix appels pointaient vers un seul
module.

Avec le bus, l'émetteur publie un fait et **ignore qui l'écoute**. Ajouter
une réaction ne modifie pas une ligne du module émetteur.

Garanties et limites — à connaître avant de s'en servir
------------------------------------------------------
- **In-process, synchrone, non durable.** Si le processus tombe entre la
  publication et le traitement, l'événement est perdu.
- **Au plus une fois.** Il n'y a ni file d'attente, ni réessai.
- **Un abonné en échec n'interrompt pas les autres**, ni l'émetteur.

Ces garanties suffisent pour des réactions accessoires (journalisation,
notification, statistiques). Elles ne suffisent **pas** pour une opération
qui doit absolument aboutir : un paiement, un débit, un envoi contractuel.
Ce cas relève du motif *outbox transactionnel*, qui fera l'objet d'une ADR
le jour où le besoin apparaîtra. Ne pas détourner ce bus pour cet usage.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from karria.shared.events.base import DomainEvent
from karria.shared.logging import get_logger

logger = get_logger(__name__)

type EventHandler[EventT: DomainEvent] = Callable[[EventT], Awaitable[None]]


class EventBus:
    """Répartiteur d'événements de domaine.

    Instancié une fois au démarrage et exposé via l'état de l'application,
    jamais comme singleton global : un test crée son propre bus et observe
    les événements publiés sans effet de bord sur le reste de la suite.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[EventHandler[Any]]] = defaultdict(list)

    def subscribe[EventT: DomainEvent](
        self, event_type: type[EventT], handler: EventHandler[EventT]
    ) -> None:
        """Abonne un gestionnaire à un type d'événement."""
        self._handlers[event_type].append(handler)
        logger.debug(
            "event_bus.subscribed",
            domain_event=event_type.__name__,
            handler=getattr(handler, "__qualname__", repr(handler)),
        )

    async def publish(self, event: DomainEvent) -> None:
        """Publie un événement auprès de tous ses abonnés.

        Les gestionnaires sont appelés séquentiellement et leurs erreurs sont
        journalisées puis absorbées : la publication d'un fait ne doit jamais
        faire échouer l'opération métier qui l'a produit.
        """
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return

        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "event_bus.handler_failed",
                    domain_event=event.name,
                    event_id=str(event.event_id),
                    handler=getattr(handler, "__qualname__", repr(handler)),
                )

    def subscriptions(self) -> dict[str, int]:
        """Retourne le nombre d'abonnés par événement (diagnostic, tests)."""
        return {event.__name__: len(handlers) for event, handlers in self._handlers.items()}
