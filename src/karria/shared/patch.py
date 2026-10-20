"""La sémantique d'un `PATCH` : absent, effacé, remplacé.

Un `PATCH` doit distinguer trois situations : le champ est absent, le champ
vaut `null`, le champ vaut une valeur. Modéliser l'absence par `None`
confond les deux premières — c'est ainsi qu'un client qui envoie
`{"first_name": "Ada"}` efface silencieusement la date de naissance.

    commande.date_of_birth is UNSET   → ne pas y toucher
    commande.date_of_birth is None    → effacer
    commande.date_of_birth == date(…) → remplacer

Ce fichier vit dans `shared/` et non dans un module : tout contexte
exposant un `PATCH` a le même besoin, et deux marqueurs `UNSET` distincts
ne seraient pas interchangeables — `is UNSET` deviendrait faux selon
l'origine de l'import, avec un effacement silencieux à la clé.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

__all__ = ["UNSET", "Patch", "is_set"]


class _Unset(Enum):
    """Type habité par une seule valeur : le marqueur d'absence.

    Un `Enum` à un membre plutôt qu'un objet sentinelle : mypy sait
    restreindre le type sur `is not UNSET`, ce qu'il ne fait pas avec un
    `object()` quelconque.
    """

    MARKER = "unset"


UNSET: Final = _Unset.MARKER

type Patch[T] = T | _Unset


def is_set[T](value: Patch[T]) -> bool:
    """Indique si le champ a été fourni par l'appelant.

    Utile pour repérer une requête vide — celle qui ne demande rien — sans
    répéter `is not UNSET` sur chaque champ.
    """
    return value is not UNSET
