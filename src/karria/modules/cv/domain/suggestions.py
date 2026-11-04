"""Les suggestions à la saisie d'un CV : ce qu'on propose, et comment on le choisit.

Contrat
-------
Quand une personne tape un intitulé de poste, une entreprise, une ville,
une école, un diplôme, une compétence ou une langue, on lui propose huit
valeurs connues au plus, par **préfixe** — sur le libellé entier ou sur
l'un de ses mots — insensible à la casse et aux accents. La saisie libre
reste toujours possible : une suggestion est une aide, jamais une
contrainte.

Deux provenances, une seule règle
---------------------------------
- Les **intitulés, entreprises et villes** viennent de l'index d'offres,
  matérialisés dans une table par le worker (`MATERIALIZED_KINDS`) : on ne
  lit jamais 54 000 offres à chaque frappe.
- Les **diplômes, écoles, compétences et langues** sont des listes de
  référence **embarquées** (`references/*.txt`, une valeur par ligne, en
  français), lues une fois au premier usage : l'index n'en sait rien, et
  elles ne changent qu'avec une livraison.

Le classement est le même pour les deux (`rank`) : ce qui commence par la
saisie avant ce dont un mot commence par la saisie, puis le poids (la
fréquence dans l'index, ou l'ordre de la liste), puis l'alphabet. La
règle est pure : elle se teste sans base.

Le nettoyage du vocabulaire (`clean_vocabulary`) est aussi ici : ce qui
sort de l'index est bruyant — « H/F », majuscules, doublons à l'accent
près — et ce qu'on propose doit être propre.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Final

from karria.modules.cv.domain.normalization import fold


class SuggestionKind(StrEnum):
    """Ce qu'on peut compléter."""

    TITLE = "title"
    COMPANY = "company"
    CITY = "city"
    SCHOOL = "school"
    DEGREE = "degree"
    SKILL = "skill"
    LANGUAGE = "language"


#: Les familles matérialisées depuis l'index d'offres.
MATERIALIZED_KINDS: Final[frozenset[SuggestionKind]] = frozenset(
    {SuggestionKind.TITLE, SuggestionKind.COMPANY, SuggestionKind.CITY}
)
#: Les familles servies par une liste embarquée.
REFERENCE_KINDS: Final[frozenset[SuggestionKind]] = frozenset(
    {SuggestionKind.SCHOOL, SuggestionKind.DEGREE, SuggestionKind.SKILL, SuggestionKind.LANGUAGE}
)
#: Suggestions rendues au plus : au-delà, on ne lit plus, on cherche.
SUGGESTION_LIMIT: Final = 8
#: Une saisie plus longue n'est plus une recherche de suggestion.
QUERY_MAX_LENGTH: Final = 60
#: Un libellé matérialisé plus court est du bruit (« RH », « IT » restent).
LABEL_MIN_LENGTH: Final = 2
LABEL_MAX_LENGTH: Final = 150
#: Une entreprise ou un intitulé vu moins de fois que cela dans l'index
#: n'est pas assez « connu » pour être proposé.
MIN_OCCURRENCES: Final[dict[SuggestionKind, int]] = {
    SuggestionKind.TITLE: 2,
    SuggestionKind.COMPANY: 2,
    SuggestionKind.CITY: 1,
}

#: Ce que les annonces accolent aux intitulés et qu'un CV ne porte pas.
_TITLE_NOISE: Final = re.compile(
    r"\s*[-–—(/|]?\s*\b(h\s*/\s*f|f\s*/\s*h|h/f/x|f/h/x|m/f|m/w|x/f/h)\b\s*[)/|]?\s*$",
    re.IGNORECASE,
)
_SEPARATED: Final = re.compile(r"\s+[-–—|/]\s+")
_PARENTHESES: Final = re.compile(r"\s*\([^)]*\)\s*$")
_DIGITS_ONLY: Final = re.compile(r"^[\d\s.,/-]+$")
_SPACES: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class VocabularyEntry:
    """Un libellé prêt à être proposé : sa forme affichée, sa clé pliée, son poids."""

    label: str
    folded: str
    weight: int


def normalize_query(raw: str) -> str:
    """La saisie telle qu'on la compare : pliée, bornée. Vide si rien d'utile."""
    return fold(raw)[:QUERY_MAX_LENGTH]


def matches(folded_label: str, folded_query: str) -> int | None:
    """Le rang d'un libellé pour une saisie : 0 en préfixe, 1 en début de mot, `None` sinon."""
    if folded_label.startswith(folded_query):
        return 0
    if f" {folded_query}" in folded_label:
        return 1
    return None


def rank(
    entries: Iterable[VocabularyEntry], folded_query: str, *, limit: int = SUGGESTION_LIMIT
) -> list[str]:
    """Les libellés à proposer, dans l'ordre : préfixe, début de mot, poids, alphabet."""
    if not folded_query:
        return []
    scored: list[tuple[int, int, str, str]] = []
    for entry in entries:
        tier = matches(entry.folded, folded_query)
        if tier is None:
            continue
        scored.append((tier, -entry.weight, entry.folded, entry.label))
    scored.sort()
    return [label for _, _, _, label in scored[:limit]]


def clean_label(kind: SuggestionKind, raw: str) -> str | None:
    """Le libellé tel qu'un CV le porterait, ou `None` s'il ne vaut rien.

    Un intitulé d'annonce perd son « H/F », sa parenthèse finale et ce qui
    suit un séparateur (« Vendeur - CDI - Lyon ») ; tout libellé perd ses
    espaces en trop. Un libellé en majuscules (« CARREFOUR ») est remis en
    capitales de mot. Un libellé trop court, trop long ou numérique est
    écarté.
    """
    text = _SPACES.sub(" ", raw).strip(" \t\r\n-–—:;,.")
    if kind is SuggestionKind.TITLE:
        text = _SEPARATED.split(text, maxsplit=1)[0]
        text = _PARENTHESES.sub("", text)
        text = _TITLE_NOISE.sub("", text).strip(" -–—(/|")
    if text.isupper() and len(text) > 3:  # noqa: PLR2004 - un sigle reste un sigle
        text = text.title()
    if not LABEL_MIN_LENGTH <= len(text) <= LABEL_MAX_LENGTH or _DIGITS_ONLY.match(text):
        return None
    return text


def clean_vocabulary(
    kind: SuggestionKind, raw_entries: Iterable[tuple[str, int]]
) -> list[VocabularyEntry]:
    """Ce que l'index donne, prêt pour la table : nettoyé, plié, dédoublonné, filtré.

    Deux libellés qui se plient pareil (« Développeur » / « developpeur »)
    ne font qu'une entrée : on garde la forme la plus fréquente, et les
    occurrences s'additionnent. Le seuil `MIN_OCCURRENCES` s'applique après.
    """
    merged: dict[str, tuple[str, int, int]] = {}
    for raw, count in raw_entries:
        label = clean_label(kind, raw)
        if label is None:
            continue
        folded = fold(label)
        best_label, best_count, total = merged.get(folded, (label, 0, 0))
        if count > best_count:
            best_label, best_count = label, count
        merged[folded] = (best_label, best_count, total + count)
    floor = MIN_OCCURRENCES.get(kind, 1)
    return sorted(
        (
            VocabularyEntry(label=label, folded=folded, weight=total)
            for folded, (label, _, total) in merged.items()
            if total >= floor
        ),
        key=lambda entry: (-entry.weight, entry.folded),
    )


# ---------------------------------------------------------------------
# Les listes de référence embarquées
# ---------------------------------------------------------------------

_REFERENCE_FILES: Final[dict[SuggestionKind, str]] = {
    SuggestionKind.SCHOOL: "schools.txt",
    SuggestionKind.DEGREE: "degrees.txt",
    SuggestionKind.SKILL: "skills.txt",
    SuggestionKind.LANGUAGE: "languages.txt",
}


def parse_reference(lines: Iterable[str]) -> list[VocabularyEntry]:
    """Une liste de référence : une valeur par ligne, `#` commente, l'ordre fait le poids."""
    entries: list[VocabularyEntry] = []
    seen: set[str] = set()
    for line in lines:
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        folded = fold(text)
        if folded in seen:
            continue
        seen.add(folded)
        entries.append(VocabularyEntry(label=text, folded=folded, weight=-len(entries)))
    return entries


@cache
def reference_entries(kind: SuggestionKind) -> tuple[VocabularyEntry, ...]:
    """Les entrées d'une liste embarquée, lues une fois par processus."""
    filename = _REFERENCE_FILES[kind]
    text = (resources.files(__package__) / "references" / filename).read_text(encoding="utf-8")
    return tuple(parse_reference(text.splitlines()))


def suggest_from_reference(
    kind: SuggestionKind, folded_query: str, *, limit: int = SUGGESTION_LIMIT
) -> list[str]:
    """Les suggestions d'une famille embarquée, classées comme les autres."""
    return rank(reference_entries(kind), folded_query, limit=limit)


def suggest_from(entries: Sequence[VocabularyEntry], folded_query: str) -> list[str]:
    """Même classement, pour une liste quelconque (un dépôt en mémoire, un test)."""
    return rank(entries, folded_query)
