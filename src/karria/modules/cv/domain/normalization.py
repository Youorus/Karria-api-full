"""Normalisation des valeurs d'un CV : dates, niveaux, puces.

Contrat
-------
Ces fonctions ramènent ce qu'un humain dit — ou ce qu'un modèle écrit —
aux formes **fixées par le contrat** : une date en `AAAA-MM` (ou `AAAA`
seul, quand c'est tout ce qu'on sait : inventer un mois serait mentir),
un niveau de compétence et un niveau de langue parmi les énumérations de
l'application.

Elles lèvent `ValueError` et rien d'autre : elles sont appelées depuis des
validateurs Pydantic, qui traduisent la `ValueError` en erreur de champ.
Pure bibliothèque standard, testable en une milliseconde.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Final


class SkillLevel(StrEnum):
    """Niveau d'une compétence. Les valeurs sont celles du contrat."""

    BEGINNER = "Débutant"
    INTERMEDIATE = "Intermédiaire"
    ADVANCED = "Avancé"
    EXPERT = "Expert"


class LanguageLevel(StrEnum):
    """Niveau d'une langue (CECRL, plus « Natif »). Valeurs du contrat."""

    NATIVE = "Natif"
    C2 = "C2"
    C1 = "C1"
    B2 = "B2"
    B1 = "B1"
    A2 = "A2"
    A1 = "A1"


_MIN_YEAR: Final = 1900
_MAX_YEAR: Final = 2100

#: Ce qui veut dire « pas de date de fin » : la période court encore.
_OPEN_ENDED: Final[frozenset[str]] = frozenset(
    {
        "",
        "present",
        "present.",
        "aujourd'hui",
        "aujourdhui",
        "en cours",
        "actuel",
        "actuellement",
        "now",
        "current",
    }
)

#: Préfixes des mois, en français et en anglais, dans l'ordre.
_MONTH_PREFIXES: Final[tuple[tuple[str, ...], ...]] = (
    ("jan",),
    ("fev", "feb"),
    ("mar",),
    ("avr", "apr"),
    ("mai", "may"),
    ("juin", "jun"),
    ("juil", "jul"),
    ("aou", "aug"),
    ("sep",),
    ("oct",),
    ("nov",),
    ("dec",),
)

#: `2021-09`, `2021-09-15`, et aussi `2021-09-01T00:00:00Z` : un modèle
#: à qui l'on dit « date » répond parfois en ISO 8601 complet.
_ISO_LIKE: Final = re.compile(r"^(\d{4})(?:[-/.](\d{1,2}))?(?:[-/.](\d{1,2}))?(?:[t ].*)?$")
_FRENCH_NUMERIC: Final = re.compile(r"^(?:(\d{1,2})[-/.])?(\d{1,2})[-/.](\d{4})$")
_MONTH_NAME: Final = re.compile(r"^([a-z]+)\.?\s+(\d{4})$")

#: Puces et tirets qu'un modèle ou un copier-coller laisse en tête de ligne.
_BULLETS: Final = re.compile(r"^[\s•\-–—▪*·●]+")  # noqa: RUF001 - tirets voulus


def fold(text: str) -> str:
    """Minuscules, sans accents, espaces réduits : la clé de comparaison."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def normalize_year_month(value: str | None) -> str | None:
    """Ramène une date à `AAAA-MM`, ou `AAAA` si le mois est inconnu.

    Accepte ce qu'on rencontre réellement : `2021-09`, `2021-09-15`,
    `09/2021`, `15/09/2021`, `2021`, `septembre 2021`, `sept. 2021`,
    `Sep 2021`. « Aujourd'hui », « en cours » et le vide valent `None`.

    Raises:
        ValueError: La forme n'est pas reconnue, ou l'année est invraisemblable.
    """
    if value is None:
        return None
    text = fold(value)
    if text in _OPEN_ENDED:
        return None

    if match := _ISO_LIKE.match(text):
        return _compose(match.group(1), match.group(2))
    if match := _FRENCH_NUMERIC.match(text):
        return _compose(match.group(3), match.group(2))
    if match := _MONTH_NAME.match(text):
        month = _month_from_name(match.group(1))
        if month is not None:
            return _compose(match.group(2), str(month))
    msg = "Date illisible : attendu AAAA-MM (par exemple 2021-09)."
    raise ValueError(msg)


def _compose(year_text: str, month_text: str | None) -> str:
    year = int(year_text)
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        msg = f"Année invraisemblable : {year}."
        raise ValueError(msg)
    if month_text is None:
        return f"{year:04d}"
    month = int(month_text)
    if not 1 <= month <= 12:  # noqa: PLR2004 - douze mois, c'est le calendrier
        msg = f"Mois invalide : {month}."
        raise ValueError(msg)
    return f"{year:04d}-{month:02d}"


def _month_from_name(name: str) -> int | None:
    for index, prefixes in enumerate(_MONTH_PREFIXES, start=1):
        if any(name.startswith(prefix) for prefix in prefixes):
            return index
    return None


_SKILL_SYNONYMS: Final[dict[str, SkillLevel]] = {
    "debutant": SkillLevel.BEGINNER,
    "beginner": SkillLevel.BEGINNER,
    "notions": SkillLevel.BEGINNER,
    "basique": SkillLevel.BEGINNER,
    "basic": SkillLevel.BEGINNER,
    "1": SkillLevel.BEGINNER,
    "2": SkillLevel.BEGINNER,
    "intermediaire": SkillLevel.INTERMEDIATE,
    "intermediate": SkillLevel.INTERMEDIATE,
    "moyen": SkillLevel.INTERMEDIATE,
    "bon": SkillLevel.INTERMEDIATE,
    "bonne": SkillLevel.INTERMEDIATE,
    "3": SkillLevel.INTERMEDIATE,
    "avance": SkillLevel.ADVANCED,
    "advanced": SkillLevel.ADVANCED,
    "confirme": SkillLevel.ADVANCED,
    "maitrise": SkillLevel.ADVANCED,
    "courant": SkillLevel.ADVANCED,
    "4": SkillLevel.ADVANCED,
    "expert": SkillLevel.EXPERT,
    "experte": SkillLevel.EXPERT,
    "excellent": SkillLevel.EXPERT,
    "5": SkillLevel.EXPERT,
}

_LANGUAGE_SYNONYMS: Final[dict[str, LanguageLevel]] = {
    "natif": LanguageLevel.NATIVE,
    "native": LanguageLevel.NATIVE,
    "maternelle": LanguageLevel.NATIVE,
    "langue maternelle": LanguageLevel.NATIVE,
    "bilingue": LanguageLevel.NATIVE,
    "bilingual": LanguageLevel.NATIVE,
    "c2": LanguageLevel.C2,
    "c1": LanguageLevel.C1,
    "courant": LanguageLevel.C1,
    "fluent": LanguageLevel.C1,
    "b2": LanguageLevel.B2,
    "avance": LanguageLevel.B2,
    "advanced": LanguageLevel.B2,
    "professionnel": LanguageLevel.B2,
    "b1": LanguageLevel.B1,
    "intermediaire": LanguageLevel.B1,
    "intermediate": LanguageLevel.B1,
    "scolaire": LanguageLevel.B1,
    "a2": LanguageLevel.A2,
    "elementaire": LanguageLevel.A2,
    "notions": LanguageLevel.A2,
    "debutant": LanguageLevel.A2,
    "beginner": LanguageLevel.A2,
    "basique": LanguageLevel.A2,
    "a1": LanguageLevel.A1,
}


def coerce_skill_level(value: object) -> SkillLevel:
    """Ramène une valeur libre (« avancé », « 4 », « Expert ») à l'énumération.

    Raises:
        ValueError: Rien de connu ne correspond.
    """
    if isinstance(value, SkillLevel):
        return value
    key = fold(str(value))
    for level in SkillLevel:
        if fold(level.value) == key:
            return level
    if key in _SKILL_SYNONYMS:
        return _SKILL_SYNONYMS[key]
    msg = "Niveau de compétence inconnu : attendu Débutant, Intermédiaire, Avancé ou Expert."
    raise ValueError(msg)


def coerce_language_level(value: object) -> LanguageLevel:
    """Ramène une valeur libre (« courant », « langue maternelle ») à l'énumération.

    Raises:
        ValueError: Rien de connu ne correspond.
    """
    if isinstance(value, LanguageLevel):
        return value
    key = fold(str(value))
    for level in LanguageLevel:
        if fold(level.value) == key:
            return level
    if key in _LANGUAGE_SYNONYMS:
        return _LANGUAGE_SYNONYMS[key]
    msg = "Niveau de langue inconnu : attendu Natif, C2, C1, B2, B1, A2 ou A1."
    raise ValueError(msg)


def clean_bullets(lines: list[str]) -> list[str]:
    """Retire les puces en tête, les blancs et les lignes vides. L'ordre est conservé."""
    cleaned: list[str] = []
    for line in lines:
        text = _BULLETS.sub("", line).strip()
        if text:
            cleaned.append(text)
    return cleaned
