"""Événements publiés par `devices`.

Des faits accomplis, au passé. Un autre module y réagit par le bus, abonné
dans `bootstrap/wiring.py`, sans jamais importer `devices` (règle 4).
"""

from __future__ import annotations

from uuid import UUID

from karria.shared.events import DomainEvent


class DeviceDeclared(DomainEvent):
    """Un compte vient de déclarer un appareil (`PUT /devices/{installation_id}`).

    Ne porte que ce qu'un autre module peut vouloir retenir **du compte** :
    la région et le fuseau que l'app annonce, tels quels — ni jeton, ni
    modèle, ni identifiant d'installation. C'est par lui que le compte d'une
    version de l'app qui n'annonce rien à la connexion reçoit quand même son
    pays d'origine (ADR-0042). Les valeurs ne sont **pas** validées ici : qui
    les retient applique sa propre règle.

    Publié seulement si l'une des deux valeurs est présente.
    """

    user_id: UUID
    region: str | None
    timezone: str | None
