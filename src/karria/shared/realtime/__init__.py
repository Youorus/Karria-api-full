"""Réveils en temps réel entre processus — le socle du « direct » de Karria.

Ce que c'est
------------
Un **bus de réveils**, pas une file de messages : un émetteur publie un
sujet (« la conversation X a bougé »), les abonnés à ce sujet sont réveillés
et vont **relire la base**. Le réveil ne transporte aucune donnée : il n'a
donc ni ordre à garantir ni perte à rattraper — un abonné qui le manque
relit de toute façon à son prochain délai d'attente (`Subscription.wait`
rend `False` au bout du délai, et l'appelant relit quand même).

Pourquoi PostgreSQL et pas Redis
--------------------------------
La pile de production n'a pas de Redis (ADR-0016 : la file de travaux est
dans PostgreSQL pour la même raison). `LISTEN`/`NOTIFY` est là, gratuit,
transactionnel, et traverse les processus Gunicorn comme les services
Dokploy. Le détail et le repli sont dans l'ADR-0026.

Deux implémentations du même port : `PostgresBroker` (production) et
`InMemoryBroker` (tests, et tout processus sans base).
"""

from karria.shared.realtime.factory import build_broker
from karria.shared.realtime.memory import InMemoryBroker
from karria.shared.realtime.ports import Broker, Subscription
from karria.shared.realtime.postgres import PostgresBroker

__all__ = ["Broker", "InMemoryBroker", "PostgresBroker", "Subscription", "build_broker"]
