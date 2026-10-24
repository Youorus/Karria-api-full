"""Les polices d'un modèle : reconnaître, choisir, et dire ce qu'on a substitué.

Contrat
-------
Un modèle Canva nomme ses polices (`ABCDEF+Raleway-Bold`) et en incorpore un
**sous-ensemble** : les seuls glyphes de son exemple. Rendre le CV d'une autre
personne exige la police complète. La résolution suit un ordre fixe, et
chaque étape dit pourquoi elle s'arrête :

1. **La police incorporée**, si sa licence le permet (OFL, Apache, CC0,
   domaine public, ou drapeau d'incorporation « installable » / « modifiable »)
   **et** si elle couvre le jeu de caractères du français. Un sous-ensemble ne
   le couvre presque jamais : il est alors seulement une **mesure** (largeurs,
   hauteur d'x) pour l'étape 3.
2. **La même famille** dans la bibliothèque libre (Google Fonts, Fontsource),
   téléchargée une fois et gardée.
3. **Une remplaçante** de la bibliothèque, choisie par la classe (empattement,
   linéale, chasse fixe, manuscrite), puis par la ressemblance des métriques :
   hauteur d'x, hauteur des capitales, chasse moyenne. Le nom ne décide de rien
   ici : il ne sert qu'à l'étape 2.

Une substitution est **toujours** déclarée (`FontResolution.origin`), journalisée
et montrée dans la revue du modèle : jamais une police ne change en silence.

Les polices servies sont des instances **statiques** : une police variable
sort en Type3 de Chromium, illisible par un ATS (ADR-0020 § 2).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

#: Ce qu'un CV français doit pouvoir écrire. Une police qui n'en a pas tous
#: les glyphes laisserait des carrés dans le nom d'une personne.
FRENCH_CHARSET: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "àâäæçéèêëîïôöœùûüÿÀÂÄÆÇÉÈÊËÎÏÔÖŒÙÛÜŸ"
    " .,;:!?'’\"«»()[]/&@%+-–—#*_€$•·"
)

#: Graisses nommées, de la plus spécifique à la plus générale : « SemiBold »
#: doit gagner sur « Bold », « ExtraLight » sur « Light ».
_WEIGHT_WORDS: Final = (
    ("extrablack", 950),
    ("ultrablack", 950),
    ("extrabold", 800),
    ("ultrabold", 800),
    ("semibold", 600),
    ("demibold", 600),
    ("extralight", 200),
    ("ultralight", 200),
    ("hairline", 100),
    ("black", 900),
    ("heavy", 900),
    ("bold", 700),
    ("medium", 500),
    ("regular", 400),
    ("normal", 400),
    ("book", 400),
    ("light", 300),
    ("thin", 100),
)
_SUFFIXES: Final = re.compile(
    r"(extrablack|ultrablack|extrabold|ultrabold|semibold|demibold|extralight|ultralight|hairline|"
    r"black|heavy|bold|medium|regular|normal|book|light|thin|italic|oblique|psmt|mt|ps|std|pro)$"
)


class FontClass(StrEnum):
    """La grande classe d'une police : ce qu'une remplaçante doit garder."""

    SANS = "sans-serif"
    SERIF = "serif"
    MONO = "monospace"
    DISPLAY = "display"
    HANDWRITING = "handwriting"


class FontOrigin(StrEnum):
    """D'où vient la police servie."""

    #: La police incorporée au PDF, licence et couverture vérifiées.
    EMBEDDED = "embedded"
    #: La même famille, de la bibliothèque libre.
    LIBRARY = "library"
    #: Une autre famille, la plus proche par la classe et les métriques.
    SUBSTITUTE = "substitute"


@dataclass(frozen=True, slots=True)
class FontMetrics:
    """Métriques d'une police, en fraction du corps (em).

    Attributes:
        x_height: Hauteur de l'œil (le « x »).
        cap_height: Hauteur des capitales.
        avg_width: Chasse moyenne sur les caractères comparés.
        widths: Chasse de chaque caractère mesuré : la comparaison porte sur
            les caractères **communs**, pas sur une moyenne de catalogue.
    """

    x_height: float | None = None
    cap_height: float | None = None
    avg_width: float | None = None
    widths: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceFont:
    """Une police nommée par le PDF du modèle, et ce qu'on en a mesuré."""

    name: str
    family: str
    weight: int
    italic: bool
    font_class: FontClass | None
    metrics: FontMetrics
    #: L'incorporation est permise (licence libre lue dans la police, ou
    #: drapeau `fsType` installable/modifiable).
    reuse_allowed: bool = False
    #: Les caractères du français manquants dans la police incorporée.
    missing_chars: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class LibraryFamily:
    """Une famille de la bibliothèque libre, candidate à la substitution."""

    family: str
    font_class: FontClass
    licence: str
    metrics: FontMetrics
    weights: tuple[int, ...] = (400, 700)
    italics: bool = True


@dataclass(frozen=True, slots=True)
class FontResolution:
    """La police servie pour une police du modèle, et pourquoi."""

    source: str
    family: str
    weight: int
    italic: bool
    origin: FontOrigin
    licence: str
    #: Distance de métriques (0 = identique) — `None` hors substitution.
    distance: float | None = None
    note: str = ""

    @property
    def substituted(self) -> bool:
        """Une autre famille que celle du modèle est servie."""
        return self.origin is FontOrigin.SUBSTITUTE


def weight_from_name(name: str, default: int = 400) -> int:
    """La graisse que dit le nom (`Raleway-SemiBold` → 600)."""
    lower = re.sub(r"[^a-z]", "", name.lower())
    for word, weight in _WEIGHT_WORDS:
        if word in lower:
            return weight
    return default


def italic_from_name(name: str) -> bool:
    """`…Italic`, `…Oblique`, `…It` en fin de nom."""
    lower = name.lower()
    return "italic" in lower or "oblique" in lower or bool(re.search(r"[-_ ]it$", lower))


def family_from_name(name: str) -> str:
    """Le nom de famille lisible : `ABCDEF+OpenSans-SemiBoldItalic` → `Open Sans`.

    Le préfixe de sous-ensemble, la graisse et le style sont retirés ; les mots
    collés en CamelCase sont séparés.
    """
    base = re.sub(r"^[A-Z]{6}\+", "", name).split("-")[0].split(",")[0]
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).strip()
    words = spaced.split()
    while len(words) > 1 and _SUFFIXES.fullmatch(words[-1].lower()):
        words.pop()
    return " ".join(words) or base


def family_key(name: str) -> str:
    """La clé de comparaison d'un nom de famille : sans accents, casse ni blancs."""
    folded = (
        unicodedata.normalize("NFKD", family_from_name(name)).encode("ascii", "ignore").decode()
    )
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def nearest_weight(wanted: int, available: tuple[int, ...]) -> int:
    """La graisse disponible la plus proche ; à égalité, la plus grasse au-delà de 500.

    C'est la règle de correspondance des navigateurs (CSS Fonts § 5.2) : un
    600 absent tombe sur 700 plutôt que 500.
    """
    if not available:
        return wanted
    return min(available, key=lambda w: (abs(w - wanted), -w if wanted > 500 else w))  # noqa: PLR2004


def metric_distance(source: FontMetrics, candidate: FontMetrics) -> float:
    """Distance relative de deux polices, pondérée par ce que l'œil voit d'abord.

    La chasse compte le plus (elle décide des retours à la ligne), puis la
    hauteur d'x (la taille apparente), puis la hauteur des capitales. Une
    métrique absente d'un côté ne compte pas ; la chasse se compare sur les
    caractères communs quand les deux côtés en ont mesuré.
    """
    terms: list[tuple[float, float]] = []
    common = sorted(set(source.widths) & set(candidate.widths))
    if len(common) >= 5:  # noqa: PLR2004
        ratios = [
            abs(candidate.widths[c] - source.widths[c]) / max(source.widths[c], 0.05)
            for c in common
        ]
        terms.append((0.5, sum(ratios) / len(ratios)))
    elif source.avg_width and candidate.avg_width:
        terms.append((0.5, abs(candidate.avg_width - source.avg_width) / source.avg_width))
    if source.x_height and candidate.x_height:
        terms.append((0.3, abs(candidate.x_height - source.x_height) / source.x_height))
    if source.cap_height and candidate.cap_height:
        terms.append((0.2, abs(candidate.cap_height - source.cap_height) / source.cap_height))
    if not terms:
        return 1.0
    total = sum(w for w, _ in terms)
    return round(sum(w * d for w, d in terms) / total, 4)


def choose_substitute(
    source: SourceFont, library: list[LibraryFamily]
) -> tuple[LibraryFamily, float] | None:
    """La famille de la bibliothèque la plus proche de `source`.

    Même classe d'abord (une linéale ne remplace pas une police à
    empattements) ; sans classe connue, toutes les familles concourent ; un
    italique exige une famille qui en a un.
    """
    pool = [f for f in library if source.font_class is None or f.font_class is source.font_class]
    if source.italic:
        pool = [f for f in pool if f.italics] or pool
    if not pool:
        pool = list(library)
    if not pool:
        return None
    best = min(pool, key=lambda f: (metric_distance(source.metrics, f.metrics), f.family))
    return best, metric_distance(source.metrics, best.metrics)


def missing_characters(available: frozenset[str] | set[str]) -> frozenset[str]:
    """Les caractères du français absents d'une police (espaces ignorées)."""
    return frozenset(c for c in FRENCH_CHARSET if c != " " and c not in available)


_FREE_LICENCES: Final = re.compile(
    r"(open font license|ofl|apache license|creative commons zero|cc0|public domain|"
    r"ubuntu font licence|mit license|bitstream vera)",
    re.IGNORECASE,
)


def licence_allows_reuse(licence_text: str, fs_type: int | None) -> bool:
    """Peut-on resservir la police incorporée pour d'autres documents ?

    Une licence libre lue dans la police (table `name`, entrées 13 et 14)
    suffit. À défaut, le drapeau `fsType` de la table `OS/2` : 0 (installable)
    ou le bit 3 (modifiable) l'autorisent ; « aperçu et impression » (bit 2) et
    « restreinte » (bit 1) l'interdisent — ils ne permettent que d'afficher le
    document d'origine.
    """
    if _FREE_LICENCES.search(licence_text or ""):
        return True
    if fs_type is None:
        return False
    if fs_type & 0x0002 or fs_type & 0x0004:
        return False
    return fs_type == 0 or bool(fs_type & 0x0008)


def classify(  # noqa: PLR0911 - une table de décision, lue de haut en bas
    *, panose_serif: int | None, family_class: int | None, monospaced: bool, name: str
) -> FontClass | None:
    """La classe d'une police, lue dans `OS/2` (PANOSE, `sFamilyClass`) puis dans son nom.

    PANOSE `bSerifStyle` 11–13 = linéale ; 2–10 = empattements. `sFamilyClass`
    (octet haut) : 8 = linéale, 1–7 = empattements, 10 = manuscrite,
    9/12 = ornementale. Un nom en « Sans », « Grotesk », « Mono » tranche quand
    les tables se taisent (Canva vide souvent la PANOSE).
    """
    if monospaced:
        return FontClass.MONO
    high = (family_class or 0) >> 8
    if high == 8:  # noqa: PLR2004
        return FontClass.SANS
    if 1 <= high <= 7:  # noqa: PLR2004
        return FontClass.SERIF
    if high == 10:  # noqa: PLR2004
        return FontClass.HANDWRITING
    if high in (9, 12):
        return FontClass.DISPLAY
    if panose_serif is not None:
        if 11 <= panose_serif <= 13:  # noqa: PLR2004
            return FontClass.SANS
        if 2 <= panose_serif <= 10:  # noqa: PLR2004
            return FontClass.SERIF
    lower = name.lower()
    if "mono" in lower or "code" in lower:
        return FontClass.MONO
    if any(k in lower for k in ("sans", "grotesk", "grotesque", "gothic")):
        return FontClass.SANS
    if any(
        k in lower
        for k in ("serif", "garamond", "baskerville", "caslon", "didot", "bodoni", "times")
    ):
        return FontClass.SERIF
    if any(k in lower for k in ("script", "hand", "brush")):
        return FontClass.HANDWRITING
    return None
