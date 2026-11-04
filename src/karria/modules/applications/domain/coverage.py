"""La couverture des mots-clés : ce que l'offre demande, ce que le CV adapté dit.

Contrat
-------
Deux règles pures, sans modèle de langage :

- `extract_keywords(title, description)` tire d'une annonce les termes
  qu'un logiciel de tri (ATS) cherchera : sigles et outils (« SAP »,
  « C++ », « CACES »), groupes de deux mots (« gestion de stock »,
  « tableaux de bord »), puis mots seuls fréquents. Les mots vides du
  français et les verbes à l'infinitif sont écartés.
- `coverage(keywords, cv)` dit quels mots-clés se lisent dans le texte du
  CV, mot entier, au singulier ou au pluriel, casse et accents indifférents.

La liste est figée dans le dossier à sa création (`OfferSnapshot.keywords`),
et la couverture se recalcule à chaque modification du CV adapté. Ce n'est
pas le score ATS du module `documents` : c'est l'indicateur de l'atelier,
« ce que l'offre dit et que ton CV ne dit pas encore ».
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

#: Mots-clés gardés au plus par offre.
MAX_KEYWORDS: Final = 15

_TOKEN: Final = re.compile(r"[A-Za-zÀ-ÿ0-9+#]+(?:[-'’][A-Za-zÀ-ÿ0-9+#]+)*")
_CLAUSE: Final = re.compile(r"[;,.:!?()\n•|/]|\s[—–-]\s")
_TECH: Final = re.compile(r"^(?:[A-Z]{2,6}|[A-Za-z]+[0-9+#][A-Za-z0-9+#.]*|[a-z]+\+\+)$")

STOPWORDS: Final[frozenset[str]] = frozenset(
    [
        "a",
        "au",
        "aux",
        "avec",
        "ce",
        "ces",
        "cet",
        "cette",
        "dans",
        "de",
        "des",
        "du",
        "elle",
        "elles",
        "en",
        "et",
        "un",
        "une",
        "pour",
        "par",
        "sur",
        "le",
        "la",
        "les",
        "leur",
        "leurs",
        "lui",
        "ma",
        "mes",
        "mon",
        "ne",
        "ni",
        "nos",
        "notre",
        "nous",
        "on",
        "ou",
        "où",
        "pas",
        "plus",
        "que",
        "qui",
        "quoi",
        "sa",
        "ses",
        "son",
        "sont",
        "sous",
        "te",
        "tes",
        "toi",
        "ton",
        "tu",
        "vos",
        "votre",
        "vous",
        "y",
        "est",
        "etre",
        "ete",
        "sera",
        "seront",
        "etes",
        "avez",
        "avons",
        "ont",
        "ayant",
        "avoir",
        "comme",
        "tres",
        "tout",
        "tous",
        "toute",
        "toutes",
        "sans",
        "aussi",
        "afin",
        "ainsi",
        "alors",
        "apres",
        "avant",
        "chez",
        "dont",
        "entre",
        "vers",
        "selon",
        "depuis",
        "meme",
        "autres",
        "autre",
        "dont",
        "cela",
        "ceci",
        "celui",
        "celle",
        "ceux",
        "celles",
        "il",
        "ils",
        "je",
        "nous",
        "vous",
        "se",
        "si",
        "poste",
        "offre",
        "mission",
        "missions",
        "profil",
        "entreprise",
        "societe",
        "groupe",
        "equipe",
        "equipes",
        "candidat",
        "candidate",
        "candidats",
        "recherche",
        "recherchons",
        "recrute",
        "recrutons",
        "type",
        "contrat",
        "cdi",
        "cdd",
        "interim",
        "temps",
        "plein",
        "partiel",
        "salaire",
        "remuneration",
        "euros",
        "annee",
        "annees",
        "mois",
        "jour",
        "jours",
        "heure",
        "heures",
        "semaine",
        "lieu",
        "ville",
        "departement",
        "region",
        "france",
        "h/f",
        "hf",
        "niveau",
        "experience",
        "experiences",
        "experimente",
        "souhaite",
        "souhaitee",
        "souhaitez",
        "exige",
        "exigee",
        "requis",
        "requise",
        "idealement",
        "minimum",
        "maximum",
        "bac",
        "bts",
        "master",
        "licence",
        "vous",
        "nous",
        "votre",
        "notre",
        "leurs",
        "etc",
        "dans",
        "rejoindre",
        "rejoignez",
        "integrer",
        "bon",
        "bonne",
        "bons",
        "bonnes",
        "grand",
        "grande",
        "petit",
        "petite",
        "nouveau",
        "nouvelle",
        "nouveaux",
        "capacite",
        "capacites",
        "sens",
        "qualite",
        "qualites",
        "esprit",
        "forte",
        "fort",
    ]
)
_NOT_VERBS: Final[frozenset[str]] = frozenset(
    {
        "cuisinier",
        "infirmier",
        "pompier",
        "plombier",
        "conseiller",
        "chauffeur",
        "serveur",
        "vendeur",
        "docker",
        "manager",
        "developer",
        "designer",
        "server",
        "atelier",
        "cahier",
        "dossier",
        "metier",
        "courrier",
        "fichier",
        "premier",
        "dernier",
        "financier",
        "hospitalier",
        "particulier",
        "regulier",
        "mobilier",
        "immobilier",
        "chantier",
        "quartier",
        "calendrier",
        "cellier",
        "foyer",
        "livre",
        "offre",
        "ordre",
        "titre",
        "cadre",
        "centre",
        "partenaire",
        "affaire",
        "affaires",
        "salaire",
        "secretaire",
        "comptable",
        "responsable",
        "notaire",
        "stagiaire",
        "gestionnaire",
        "commissionnaire",
        "fonctionnaire",
        "logiciel",
        "materiel",
        "industriel",
        "menuisier",
        "charpentier",
        "boulanger",
        "boucher",
        "patissier",
        "banquier",
        "caissier",
        "pharmacien",
        "technicien",
        "magasinier",
        "cariste",
        "operateur",
        "ingenieur",
        "electricien",
        "jardinier",
        "ouvrier",
        "soudeur",
    }
)


def fold(text: str) -> str:
    """Minuscules, sans accents, apostrophes et traits d'union ramenés à des espaces."""
    stripped = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"[’'\-]+", " ", plain.lower()).strip()


def _is_verb(word: str) -> bool:
    return len(word) > 4 and word.endswith(("er", "ir")) and word not in _NOT_VERBS  # noqa: PLR2004


def _is_good(word: str) -> bool:
    return len(word) > 2 and word not in STOPWORDS and not word.isdigit()  # noqa: PLR2004


def _is_tech(token: str) -> bool:
    return _TECH.match(token) is not None and fold(token) not in STOPWORDS


def extract_keywords(title: str, description: str, *, top: int = MAX_KEYWORDS) -> list[str]:
    """Les mots-clés d'une annonce, du plus discriminant au moins, `top` au plus.

    Le titre compte double : c'est ce que l'ATS compare en premier à
    l'intitulé du CV.
    """
    grams: defaultdict[str, float] = defaultdict(float)
    unis: defaultdict[str, float] = defaultdict(float)
    for weight, text in ((2.0, title), (1.0, description)):
        for clause in _CLAUSE.split(text):
            tokens = _TOKEN.findall(clause)
            folded = [fold(t).replace(" ", "") for t in tokens]
            for i, (token, word) in enumerate(zip(tokens, folded, strict=True)):
                tech = _is_tech(token)
                if tech or _is_good(word):
                    unis[word] += weight * (3.0 if tech else 1.0)
                if i + 1 < len(tokens):
                    nxt, nxt_word = tokens[i + 1], folded[i + 1]
                    if (
                        (tech or _is_good(word))
                        and (_is_tech(nxt) or _is_good(nxt_word))
                        and not _is_verb(word)
                        and not _is_verb(nxt_word)
                    ):
                        grams[f"{word} {nxt_word}"] += weight * (
                            2.0 if (tech or _is_tech(nxt)) else 1.0
                        )
                if (
                    i + 2 < len(tokens)
                    and folded[i + 1] in {"de", "d", "des", "du"}
                    and _is_good(word)
                    and _is_good(folded[i + 2])
                    and not _is_verb(word)
                ):
                    grams[f"{word} {folded[i + 1]} {folded[i + 2]}"] += weight * 1.5
    out: list[str] = []
    for gram, count in _ranked(grams):
        if count >= 2.0 and not any(gram in o or o in gram for o in out):  # noqa: PLR2004
            out.append(gram)
    for uni, _ in _ranked(unis):
        if len(out) >= top:
            break
        if any(uni in o.split() for o in out) or _is_verb(uni):
            continue
        out.append(uni)
    return out[:top]


def _ranked(weights: Mapping[str, float]) -> list[tuple[str, float]]:
    """Du plus lourd au plus léger ; à poids égal, l'ordre d'apparition."""
    return sorted(weights.items(), key=lambda kv: -kv[1])


def cv_text(cv: Mapping[str, Any]) -> str:
    """Tout le texte d'un CV, à plat, pour y chercher des mots."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, Mapping):
            for key, value in node.items():
                if key != "id":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(cv)
    return "\n".join(parts)


def _word_pattern(part: str) -> str:
    """Un mot entier, au singulier ou au pluriel ; « c++ » et « c# » compris."""
    escaped = re.escape(part)
    head = r"(?<![a-z0-9])" if part[:1].isalnum() else ""
    tail = r"(?:s|x)?(?![a-z0-9])" if part[-1:].isalnum() else ""
    return f"{head}{escaped}{tail}"


def mentions(keyword: str, folded_text: str) -> bool:
    """Le mot-clé se lit-il dans le texte plié ?"""
    pattern = r"\s+".join(_word_pattern(part) for part in keyword.split())
    return re.search(pattern, folded_text) is not None


@dataclass(frozen=True, slots=True)
class Coverage:
    """Les mots-clés de l'offre présents et absents du CV adapté."""

    present: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def ratio(self) -> float:
        """La part couverte, entre 0 et 1 ; 1 sans mot-clé."""
        total = len(self.present) + len(self.missing)
        return 1.0 if total == 0 else round(len(self.present) / total, 2)

    def as_json(self) -> dict[str, Any]:
        """La forme stockée et servie."""
        return {"present": list(self.present), "missing": list(self.missing), "ratio": self.ratio}

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> Coverage:
        """Relit la forme stockée ; tolère un document vide."""
        if not data:
            return cls()
        return cls(
            present=tuple(str(k) for k in data.get("present", [])),
            missing=tuple(str(k) for k in data.get("missing", [])),
        )


def coverage(keywords: Iterable[str], cv: Mapping[str, Any]) -> Coverage:
    """Quels mots-clés de l'offre se lisent dans le CV."""
    folded = fold(cv_text(cv))
    present: list[str] = []
    missing: list[str] = []
    for keyword in keywords:
        (present if mentions(keyword, folded) else missing).append(keyword)
    return Coverage(present=tuple(present), missing=tuple(missing))
