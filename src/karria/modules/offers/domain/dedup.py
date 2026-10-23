"""Doublons exacts et quasi-doublons — **avant** tout plongement.

Pourquoi c'est la première étape, et pas une finition
------------------------------------------------------
Chaque jour, la collecte rapporte 50 000 à 70 000 annonces ; une bonne part
sont les mêmes offres revues (même source, même identifiant), republiées
sous un autre identifiant, ou reprises d'un site à l'autre. Un plongement
coûte environ une demi-seconde de CPU : calculer celui d'une annonce déjà
connue est l'erreur que la V1 a payée en argent (ré-embedding du corpus
entier) et qu'on paierait ici en heures de calcul.

Trois niveaux, du moins cher au plus cher :

1. `(source, source_id)` — l'offre est **revue** : on rafraîchit
   `last_seen_at`, rien d'autre.
2. `content_hash` — le même texte sous un autre identifiant ou sur un
   autre site : l'offre est un **doublon exact** de la canonique.
3. SimHash 64 bits sur les 3-grammes de mots — une annonce **retouchée**
   (une ligne de plus, un salaire changé) : quasi-doublon si la distance
   de Hamming est au plus `NEAR_DUPLICATE_MAX_DISTANCE`. La recherche des
   candidats passe par les quatre bandes de 16 bits : deux empreintes à
   distance ≤ 3 partagent au moins une bande intacte (principe des tiroirs).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Final

from karria.modules.offers.domain.normalization import (
    RawPosting,
    clean_text,
    clean_title,
    fold,
)

#: Au-delà de cette distance de Hamming, deux annonces sont distinctes.
NEAR_DUPLICATE_MAX_DISTANCE: Final = 3
#: Les quasi-doublons ne se cherchent que parmi les offres du même
#: employeur et des dernières semaines : une annonce identique republiée
#: trois mois plus tard est une nouvelle offre.
NEAR_DUPLICATE_WINDOW_DAYS: Final = 45

_TOKEN: Final = re.compile(r"[a-z0-9]+")
_BAND_MASK: Final = 0xFFFF
_SHINGLE: Final = 3


def canonical_text(title: str, company: str | None, description: str) -> str:
    """Le texte sur lequel on compare : plié, sans ponctuation, borné.

    La description est bornée : au-delà, ce sont des paragraphes
    d'entreprise identiques d'une annonce à l'autre, qui rapprocheraient
    des postes différents.
    """
    words = _TOKEN.findall(fold(f"{title} {company or ''} {description[:6_000]}"))
    return " ".join(words)


def content_hash(title: str, company: str | None, description: str) -> str:
    """Condensat SHA-256 du texte canonique."""
    return hashlib.sha256(canonical_text(title, company, description).encode()).hexdigest()


def posting_hash(raw: RawPosting) -> str:
    """Le condensat qu'aurait l'offre normalisée de cette annonce, sans la normaliser.

    Il permet de reconnaître une annonce **revue à l'identique** — le cas
    de loin le plus fréquent — sans géocoder ni relire son texte : mêmes
    nettoyages que `normalize_posting`, même condensat.
    """
    company = (
        clean_title(raw.company_name) if raw.company_name and raw.company_name.strip() else None
    )
    return content_hash(clean_title(raw.title), company, clean_text(raw.description))


def simhash64(text: str) -> int:
    """Empreinte SimHash sur les 3-grammes de mots du texte canonique."""
    words = text.split()
    if not words:
        return 0
    if len(words) < _SHINGLE:
        shingles: Iterable[str] = (" ".join(words),)
    else:
        shingles = (" ".join(words[i : i + _SHINGLE]) for i in range(len(words) - _SHINGLE + 1))
    weights = [0] * 64
    for shingle in shingles:
        digest = int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if digest >> bit & 1 else -1
    value = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            value |= 1 << bit
    return value


def bands(simhash: int) -> tuple[int, int, int, int]:
    """Les quatre bandes de 16 bits, indexées en base pour trouver les candidats."""
    return (
        simhash & _BAND_MASK,
        (simhash >> 16) & _BAND_MASK,
        (simhash >> 32) & _BAND_MASK,
        (simhash >> 48) & _BAND_MASK,
    )


def hamming(a: int, b: int) -> int:
    """Nombre de bits qui diffèrent."""
    return (a ^ b).bit_count()


def is_near_duplicate(a: int, b: int) -> bool:
    """Deux empreintes désignent-elles la même annonce, à une retouche près ?"""
    return hamming(a, b) <= NEAR_DUPLICATE_MAX_DISTANCE
