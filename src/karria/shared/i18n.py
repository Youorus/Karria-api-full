"""Les langues dans lesquelles l'application sait s'adresser à quelqu'un.

    ┌────────────────────────────────────────────────────────────────┐
    │  AJOUTER UNE LANGUE = UNE VALEUR ICI, PUIS LES TESTS DISENT     │
    │  EXACTEMENT CE QU'IL RESTE À TRADUIRE.                          │
    └────────────────────────────────────────────────────────────────┘

Pourquoi une énumération, et non le champ libre du compte
----------------------------------------------------------
`User.preferred_language` accepte n'importe quel code ISO 639-1 : c'est
une préférence déclarée, et quelqu'un peut parfaitement demander le
wolof. Les gabarits, eux, n'existent que dans les langues réellement
écrites. Confondre les deux produirait un e-mail vide — ou pire, un
`KeyError` au moment d'envoyer un code de connexion.

Cette énumération est donc la liste de ce qu'on **sait dire**, et
`resolve()` la porte d'entrée qui ramène toute demande à quelque chose
d'écrit.

Pourquoi le français par défaut
--------------------------------
Ce n'est pas un choix esthétique : Karria sert le marché de l'emploi
français, et ses offres sont rédigées en français. Quelqu'un dont on
ignore la langue a bien plus de chances de lire le français que l'anglais.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final


class Language(StrEnum):
    """Langue dans laquelle l'application sait rédiger."""

    FR = "fr"
    EN = "en"


#: Langue employée quand on ne sait pas, ou qu'on ne sait pas faire mieux.
DEFAULT_LANGUAGE: Final = Language.FR


def resolve(preferred: str | None) -> Language:
    """Ramène une préférence déclarée à une langue réellement écrite.

    Accepte `en`, `EN`, `en-GB`, `en_US` : selon la plateforme, un client
    remonte l'une ou l'autre forme. La variante régionale est ignorée —
    on n'écrit pas différemment pour le Canada et pour la France.

    Toute langue inconnue retombe sur le français plutôt que de lever :
    une préférence exotique ne doit jamais empêcher un code d'arriver.
    """
    if not preferred:
        return DEFAULT_LANGUAGE

    base = re.split(r"[-_]", preferred.strip())[0].lower()
    try:
        return Language(base)
    except ValueError:
        return DEFAULT_LANGUAGE


def from_accept_language(header: str | None) -> Language:
    """Déduit la langue de l'en-tête `Accept-Language` d'une requête.

    Sert au seul moment où l'on ne peut pas faire autrement : le tout
    premier code, envoyé à une adresse dont aucun compte ne dépend
    encore. Dès qu'un compte existe, c'est **sa** préférence qui prime —
    le navigateur d'un cybercafé ne décide pas de la langue de quelqu'un.

    L'en-tête ressemble à `en-GB,en;q=0.9,fr;q=0.8`. On prend la première
    langue qu'on sait écrire, dans l'ordre où elle est proposée : les
    poids `q` sont déjà décroissants dans la pratique, et les trier
    ajouterait du code pour un cas qui ne se présente pas.
    """
    if not header:
        return DEFAULT_LANGUAGE

    for part in header.split(","):
        tag = part.split(";")[0].strip()
        if not tag or tag == "*":
            continue
        base = re.split(r"[-_]", tag)[0].lower()
        if base in set(Language):
            return Language(base)
    return DEFAULT_LANGUAGE
