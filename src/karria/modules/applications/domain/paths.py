"""Les chemins JSON d'une proposition : où, dans le CV, elle s'applique.

Contrat
-------
Un chemin est une suite de segments séparés par des points :
`identity.headline`, `summary`, `experiences.1.missions.0`, `skills`. Un
segment fait de chiffres désigne un index de liste. Le chemin **désigne
un emplacement qui existe déjà** : une proposition reformule ou réordonne,
elle n'ajoute jamais un élément — c'est une garantie de la règle « Karria
n'invente rien », pas seulement une commodité.

`read` et `write` travaillent sur des documents JSON (dictionnaires et
listes) ; `write` rend un **nouveau** document, l'original n'est pas touché.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any, Final

#: Un chemin : des segments alphanumériques, séparés par des points.
PATH_PATTERN: Final = re.compile(r"^[a-z_]+(?:\.[a-z_0-9]+)*$")
PATH_MAX_LENGTH: Final = 80


class PathError(LookupError):
    """Le chemin ne désigne rien dans ce document."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def is_well_formed(path: str) -> bool:
    """La forme seule : lettres, chiffres, points. Dit rien de l'existence."""
    return bool(path) and len(path) <= PATH_MAX_LENGTH and PATH_PATTERN.match(path) is not None


def _step(node: Any, segment: str, path: str) -> Any:
    if isinstance(node, Mapping):
        if segment not in node:
            raise PathError(path)
        return node[segment]
    if isinstance(node, list) and segment.isdigit():
        index = int(segment)
        if index >= len(node):
            raise PathError(path)
        return node[index]
    raise PathError(path)


def read(document: Mapping[str, Any], path: str) -> Any:
    """La valeur au chemin.

    Raises:
        PathError: Le chemin est mal formé ou ne désigne rien.
    """
    if not is_well_formed(path):
        raise PathError(path)
    node: Any = document
    for segment in path.split("."):
        node = _step(node, segment, path)
    return node


def write(document: Mapping[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Un nouveau document, la valeur posée au chemin — qui doit déjà exister.

    Raises:
        PathError: Le chemin est mal formé ou ne désigne rien.
    """
    if not is_well_formed(path):
        raise PathError(path)
    updated = copy.deepcopy(dict(document))
    segments = path.split(".")
    node: Any = updated
    for segment in segments[:-1]:
        node = _step(node, segment, path)
    last = segments[-1]
    _step(node, last, path)  # existence
    if isinstance(node, list):
        node[int(last)] = copy.deepcopy(value)
    else:
        node[last] = copy.deepcopy(value)
    return updated
