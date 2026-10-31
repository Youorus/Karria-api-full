"""Le contrat des notifications push, version 1 — la seule façon d'en écrire une.

Pourquoi un contrat, et pourquoi ici
------------------------------------
Avant lui, chaque émetteur composait son `data` à la main : les alertes
d'offres envoyaient `{"type": "NEW_OFFERS", "offer_id": …}`, la campagne
`{"screen": "feed"}`, l'essai `{"type": "TEST"}` — et l'app, qui ne lisait
que `data.url`, n'ouvrait rien du tout. Une notification touchée qui
n'ouvre rien est la pire des notifications.

Le contrat vit dans `shared/` parce que **plusieurs modules** en écrivent
(`offers`, `devices`, les scripts) et que la règle 4 leur interdit de
s'importer : `offers` ne peut pas emprunter un type à `devices`. C'est un
format d'échange avec l'app, sans règle métier — comme les gabarits de
`shared/mail`. Pur stdlib : le domaine d'un module peut l'importer sans
enfreindre la règle 2 bis.

Le format (miroir de `karria_app/docs/contrat-notifications.md`)
-----------------------------------------------------------------
`data`, uniquement des chaînes (ce que FCM et APNs transmettent intact) :

- `v` — `"1"`, toujours ;
- `type` — l'une des valeurs de `PushKind` ;
- `id` — obligatoire pour `offer` et `template`, permis pour `cv_export`,
  refusé ailleurs ;
- `count` — permis pour `offer`, `offers_feed`, `search_results` ;
- `campaign` — étiquette libre bornée, pour la mesure ;
- `url` — le chemin équivalent, pour les versions de l'app qui ne lisent
  que `data.url`.

Hors `data`, le message Expo porte `channelId` et `categoryId` (la même
valeur, déduite du type) et `badge` (1 pour les offres, absent sinon).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

#: La version du contrat, portée par chaque notification.
PUSH_CONTRACT_VERSION: Final = "1"

#: Longueur au-delà de laquelle Expo tronque, et au-delà de laquelle un
#: titre n'en est plus un.
MAX_TITLE_LENGTH: Final = 120
MAX_BODY_LENGTH: Final = 500

#: Une étiquette de campagne : courte, sans espace ni donnée personnelle.
CAMPAIGN_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: Le schéma de l'app — les liens `url` le visent.
APP_SCHEME: Final = "karria://"


class PushKind(StrEnum):
    """Ce que la notification annonce — donc l'écran qu'elle ouvre."""

    OFFER = "offer"
    OFFERS_FEED = "offers_feed"
    SEARCH_RESULTS = "search_results"
    CV_READY = "cv_ready"
    CV_EXPORT = "cv_export"
    ASSISTANT = "assistant"
    TEMPLATE = "template"
    TEMPLATES = "templates"
    ACCOUNT = "account"
    #: L'équipe Karria a répondu dans « Parler à l'équipe ».
    SUPPORT = "support"


class PushChannel(StrEnum):
    """Le canal Android et la catégorie iOS : ce que la personne peut couper à part.

    Trois et pas un par type : un réglage système se lit par une personne,
    qui veut couper « les offres » ou « mes documents », pas `search_results`.
    """

    OFFRES = "offres"
    DOCUMENTS = "documents"
    COMPTE = "compte"


_CHANNEL: Final[dict[PushKind, PushChannel]] = {
    PushKind.OFFER: PushChannel.OFFRES,
    PushKind.OFFERS_FEED: PushChannel.OFFRES,
    PushKind.SEARCH_RESULTS: PushChannel.OFFRES,
    PushKind.ASSISTANT: PushChannel.OFFRES,
    PushKind.CV_READY: PushChannel.DOCUMENTS,
    PushKind.CV_EXPORT: PushChannel.DOCUMENTS,
    PushKind.TEMPLATE: PushChannel.DOCUMENTS,
    PushKind.TEMPLATES: PushChannel.DOCUMENTS,
    PushKind.ACCOUNT: PushChannel.COMPTE,
    PushKind.SUPPORT: PushChannel.COMPTE,
}

#: Les types qui désignent une chose précise : sans `id`, ils n'ouvrent rien.
_ID_REQUIRED: Final = frozenset({PushKind.OFFER, PushKind.TEMPLATE})
#: Ceux qui l'acceptent sans l'exiger.
_ID_ALLOWED: Final = _ID_REQUIRED | {PushKind.CV_EXPORT}
#: Ceux qui annoncent un nombre d'offres.
_COUNT_ALLOWED: Final = frozenset({PushKind.OFFER, PushKind.OFFERS_FEED, PushKind.SEARCH_RESULTS})


#: Le chemin de l'app que l'ancienne version ouvre par `data.url` ;
#: `{id}` y est remplacé par l'identifiant.
_PATH: Final[dict[PushKind, str]] = {
    PushKind.OFFER: "offre/{id}",
    PushKind.TEMPLATE: "modele/{id}",
    PushKind.OFFERS_FEED: "offres",
    PushKind.SEARCH_RESULTS: "offres",
    PushKind.ASSISTANT: "offres?karria=1",
    PushKind.CV_READY: "cv",
    PushKind.CV_EXPORT: "cv",
    PushKind.TEMPLATES: "modeles",
    PushKind.ACCOUNT: "parametres",
    PushKind.SUPPORT: "equipe",
}


@dataclass(frozen=True, slots=True)
class PushMessage:
    """Une notification conforme au contrat, validée à la construction.

    Valider ici plutôt qu'à l'envoi : l'erreur remonte chez l'émetteur qui
    l'a commise (un `offer` sans identifiant), pas dans `devices` qui n'en
    peut rien.
    """

    kind: PushKind
    title: str
    body: str
    target_id: UUID | None = None
    count: int | None = None
    campaign: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title) > MAX_TITLE_LENGTH:
            msg = "Le titre d'une notification est obligatoire et borné."
            raise ValueError(msg)
        if not self.body.strip() or len(self.body) > MAX_BODY_LENGTH:
            msg = "Le corps d'une notification est obligatoire et borné."
            raise ValueError(msg)
        if self.kind in _ID_REQUIRED and self.target_id is None:
            msg = f"Une notification `{self.kind}` désigne une chose : l'identifiant manque."
            raise ValueError(msg)
        if self.kind not in _ID_ALLOWED and self.target_id is not None:
            msg = f"Une notification `{self.kind}` ne porte pas d'identifiant."
            raise ValueError(msg)
        if self.count is not None and (self.kind not in _COUNT_ALLOWED or self.count < 1):
            msg = f"Un nombre d'offres n'a pas de sens pour `{self.kind}`, ou il est nul."
            raise ValueError(msg)
        if self.campaign is not None and CAMPAIGN_PATTERN.match(self.campaign) is None:
            msg = "Une campagne est une étiquette courte en minuscules, sans espace."
            raise ValueError(msg)

    @property
    def channel(self) -> PushChannel:
        """Le canal Android, déduit du type."""
        return _CHANNEL[self.kind]

    @property
    def category(self) -> str:
        """La catégorie iOS : celle du canal, pour que l'app n'en tienne qu'une table."""
        return self.channel.value

    @property
    def badge(self) -> int | None:
        """Une pastille pour les offres seulement : c'est ce qui se « lit » en retard."""
        return 1 if self.channel is PushChannel.OFFRES else None

    @property
    def url(self) -> str:
        """Le lien équivalent, pour les versions de l'app qui ne lisent que lui."""
        return APP_SCHEME + _PATH[self.kind].format(id=self.target_id)

    @property
    def data(self) -> dict[str, str]:
        """Le `data` du message Expo : des chaînes, et rien d'autre que le contrat."""
        data = {"v": PUSH_CONTRACT_VERSION, "type": self.kind.value, "url": self.url}
        if self.target_id is not None:
            data["id"] = str(self.target_id)
        if self.count is not None:
            data["count"] = str(self.count)
        if self.campaign is not None:
            data["campaign"] = self.campaign
        return data
