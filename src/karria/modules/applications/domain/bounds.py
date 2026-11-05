"""Les bornes d'une valeur proposée, par chemin — avant toute écriture dans le CV.

La sortie du modèle est une donnée (AGENTS.md §6.14) : même revalidée par
son schéma, une valeur ne s'écrit à un chemin du CV que si elle a la forme
que ce chemin attend. Un intitulé tient sur une ligne et 150 caractères ;
un « à propos » sur 1 500 ; une mission sur 300 ; un ordre est une liste.
Les bornes sont celles de `cv/domain/schemas.py`, reprises ici parce que le
domaine ne peut pas les importer (règle 4) ; le cas d'usage repasse de
toute façon le CV résultant par le port `CvValidator`.
"""

from __future__ import annotations

import re
from typing import Any, Final

HEADLINE_MAX_LENGTH: Final = 150
POSITION_MAX_LENGTH: Final = 150
SUMMARY_MAX_LENGTH: Final = 1500
MISSION_MAX_LENGTH: Final = 300

_ONE_LINE: Final[dict[str, int]] = {
    "identity.headline": HEADLINE_MAX_LENGTH,
}
_POSITION: Final = re.compile(r"^experiences\.\d+\.position$")
_MISSION: Final = re.compile(r"^experiences\.\d+\.missions\.\d+$")


class BoundsError(ValueError):
    """La valeur n'a pas la forme que ce chemin attend."""

    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"{path}: {why}")
        self.path = path
        self.why = why


def _text(path: str, value: Any, *, max_length: int, one_line: bool) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BoundsError(path, "text_expected")
    if len(value) > max_length:
        raise BoundsError(path, "too_long")
    if one_line and ("\n" in value or "\r" in value):
        raise BoundsError(path, "one_line_expected")


def check_bounds(path: str, value: Any) -> None:
    """Refuse une valeur hors des bornes de son chemin.

    Raises:
        BoundsError: La valeur n'a pas la forme attendue à ce chemin.
    """
    if path in _ONE_LINE:
        _text(path, value, max_length=_ONE_LINE[path], one_line=True)
    elif path == "summary":
        _text(path, value, max_length=SUMMARY_MAX_LENGTH, one_line=False)
    elif _POSITION.match(path):
        _text(path, value, max_length=POSITION_MAX_LENGTH, one_line=True)
    elif _MISSION.match(path):
        _text(path, value, max_length=MISSION_MAX_LENGTH, one_line=True)
    elif path in {"skills", "experiences"}:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise BoundsError(path, "list_expected")
    else:
        raise BoundsError(path, "unknown_path")
