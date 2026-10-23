"""La porte d'intitulé : une offre n'entre dans un fil que si son titre dit le métier demandé.

Pourquoi une règle, et pas seulement un modèle
-----------------------------------------------
Mesuré le 2026-10-22 sur 8 400 offres réelles et 35 demandes :

- la similarité dense seule rapproche des **domaines**, pas des métiers :
  « serveuse » et « chef de partie » sont à 0,86 de cosinus, « serveuse »
  et « serveur » à 0,87 — aucun seuil ne les sépare ;
- le reclasseur multilingue léger (`mmarco-mMiniLMv2`) note « Assistant
  RH » 0,95 et « ÉLECTRICIEN CONFIRMÉ » 0,00 pour « électricien » : sur des
  intitulés courts, il ne vaut guère mieux que le hasard ;
- le reclasseur qui classe juste (`bge-reranker-v2-m3`) lit 40 intitulés
  en 6,7 s sur ce CPU — seize fois le budget de latence.

La V1 avait fait le même constat autrement : 60 à 80 % de son fil était
hors sujet tant qu'aucune « porte taxonomique » n'écartait les offres sans
lien de métier. Ici, la porte est une règle **explicable** : le titre de
l'offre contient tous les mots d'un des intitulés que Karria a compris
(« chef de rang » exige « chef » **et** « rang » : « Chef de partie »
ne passe pas). La compréhension — les synonymes, le féminin, « hôte de
caisse » pour « caissière » — est le travail du modèle de langage, en
amont ; la décision d'entrer dans le fil est celle du domaine.

La racinisation est volontairement **grossière et symétrique** : pliage
des accents, minuscules, mots vides retirés, marques du féminin et du
pluriel ramenées au masculin singulier. Elle s'applique pareil des deux
côtés ; elle n'a pas besoin d'être juste, seulement cohérente.

Les mots de niveau
------------------
La porte par inclusion laissait passer un titre d'un **autre niveau** :
« Chef cuisinier » et « Cuisinier chef de partie » pour « cuisinier »
(3 des 10 premières de la requête nationale, jugées hors sujet le
2026-10-22). Un mot de niveau (« chef », « second », « adjoint », « aide »,
« assistant », « responsable », « directeur », « stagiaire »…) présent dans
le titre de l'offre et absent de **tous** les intitulés demandés élimine :
qui demande « cuisinier » ne demande pas un chef ni un aide ; qui demande
« chef de rang » a dit « chef ». C'est le **repli déterministe** : la
première décision vient du modèle de dialogue, qui rend avec les intitulés
acceptés les intitulés voisins à **exclure** (`excluded_titles`), là où une
liste de mots ne porte pas le sens (« commis de cuisine » n'a aucun mot de
niveau et n'est pourtant pas « cuisinier »).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from karria.modules.offers.domain.normalization import fold

_WORD: Final = re.compile(r"[a-z0-9+#]+")
#: Mots qui ne disent pas le métier.
STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a", "au", "aux", "d", "de", "des", "du", "en", "et", "l", "la", "le", "les",
        "ou", "par", "pour", "sur", "un", "une", "h", "f", "hf", "fh", "x", "m", "e",
        "se", "euse", "rice", "ere", "ne", "ve", "ice", "trice", "fe", "te", "re",
    }
)  # fmt: skip
#: Terminaisons ramenées au masculin singulier, de la plus longue à la plus courte.
_SUFFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("trices", "teur"),
    ("trice", "teur"),
    ("euses", "eur"),
    ("euse", "eur"),
    ("iennes", "ien"),
    ("ienne", "ien"),
    ("ieres", "ier"),
    ("iere", "ier"),
    ("eres", "er"),
    ("ere", "er"),
    ("ives", "if"),
    ("ive", "if"),
    ("ales", "al"),
    ("aux", "al"),
)
_MIN_STEM: Final = 3
#: Les mots qui disent un niveau hiérarchique ou un statut, pas un métier.
LEVEL_WORDS: Final[frozenset[str]] = frozenset(
    {
        "chef", "cheffe", "sous", "second", "seconde", "adjoint", "adjointe", "aide", "assistant",
        "assistante", "responsable", "directeur", "directrice", "manager", "lead", "head",
        "senior", "junior", "stagiaire", "apprenti", "apprentie", "alternant", "alternante",
        "superviseur", "coordinateur", "coordinatrice", "chargé", "charge", "chargée",
    }
)  # fmt: skip


def stem(word: str) -> str:
    """Le masculin singulier approché d'un mot déjà plié."""
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)] + replacement
    if word.endswith(("s", "x")) and len(word) > _MIN_STEM + 1:
        word = word[:-1]
    for _ in range(2):  # « spécialisée » et « spécialisé » se rejoignent
        if word.endswith("e") and len(word) > _MIN_STEM + 1:
            word = word[:-1]
    return word


def title_tokens(text: str) -> frozenset[str]:
    """Les mots porteurs d'un intitulé, racinisés."""
    return frozenset(stem(word) for word in _WORD.findall(fold(text)) if word not in STOPWORDS)


LEVEL_STEMS: Final[frozenset[str]] = frozenset(stem(fold(word)) for word in LEVEL_WORDS)


@dataclass(frozen=True, slots=True)
class TitleMatcher:
    """Les intitulés demandés, prêts à être confrontés à des titres d'offres."""

    phrases: tuple[frozenset[str], ...]
    #: Les mots de niveau que la demande contient : seuls ceux-là sont admis dans un titre.
    allowed_levels: frozenset[str] = frozenset()
    #: Les intitulés voisins que le modèle de dialogue a dits à exclure
    #: (« chef cuisinier », « second de cuisine » pour « cuisinier ») : un
    #: titre qui en contient un est écarté avant tout.
    excluded: tuple[frozenset[str], ...] = ()

    @classmethod
    def of(cls, job_titles: Sequence[str], excluded: Sequence[str] = ()) -> TitleMatcher:
        """Un intitulé vide (que des mots vides) est ignoré."""
        phrases = tuple(tokens for t in job_titles if (tokens := title_tokens(t)))
        return cls(
            phrases,
            frozenset().union(*phrases) & LEVEL_STEMS if phrases else frozenset(),
            tuple(tokens for t in excluded if (tokens := title_tokens(t))),
        )

    def match(self, title: str) -> int | None:
        """Le rang du premier intitulé dont tous les mots sont dans ce titre, ou `None`.

        `None` aussi si le titre contient un intitulé exclu par le modèle, ou
        — repli déterministe — un mot de niveau que la demande ne porte pas :
        « Chef cuisinier » n'est pas « cuisinier ».
        """
        words = title_tokens(title)
        if any(phrase <= words for phrase in self.excluded):
            return None
        if (words & LEVEL_STEMS) - self.allowed_levels:
            return None
        for index, phrase in enumerate(self.phrases):
            if phrase <= words:
                return index
        return None
