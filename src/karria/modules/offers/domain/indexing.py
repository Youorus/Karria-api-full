"""Ce qui entre dans l'index : un texte plongé, deux textes lexicaux, une version.

Trois représentations par offre canonique, calculées **une seule fois**
par couple (condensat de contenu, version d'index) :

- un **vecteur dense**, plongement d'un texte court : intitulé, employeur,
  lieu, contrat, puis le début de la description ;
- un **vecteur lexical d'intitulé** : le métier se dit dans le titre, et
  « serveur » dans le titre ne vaut pas « serveur » au détour d'un
  paragraphe ;
- un **vecteur lexical de corps** : titre, employeur et description.

Les deux vecteurs lexicaux sont calculés **par Qdrant** (BM25 côté
serveur, racinisation française, pliage des accents ; vérifié en v1.20.0
le 2026-10-22) : l'index et la requête passent par le même analyseur, ce
qui est la seule propriété qui compte pour une branche lexicale.

Pourquoi un texte dense court
-----------------------------
Mesuré le 2026-10-22 sur le serveur (8 vCPU EPYC Milan, AVX2, sans
AVX-512), `multilingual-e5-base` quantifié int8 : 31,6 annonces/s à 300
caractères, 15,3/s à 600, 9,7/s à 1 100. Le coût est linéaire en longueur,
et le sens d'une annonce tient dans ses premières lignes ; la suite
(avantages, présentation de l'employeur) tire le vecteur vers un
« employeur générique ». La V1 plongeait l'annonce entière, payait
chaque refonte en euros et constatait des voisins hors sujet.
"""

from __future__ import annotations

from typing import Final

from karria.modules.offers.domain.offers import Offer

#: Change quand la **composition** du texte plongé change : toute offre
#: indexée sous une autre version se ré-indexe (commande explicite, jamais
#: au démarrage).
EMBEDDING_TEXT_VERSION: Final = "t2"
#: Caractères de description gardés dans le texte plongé. Avec l'en-tête,
#: le texte fait ~600 caractères (~150 jetons) : 15 annonces/s mesurées.
EMBEDDING_DESCRIPTION_CHARS: Final = 450
#: Caractères de description analysés lexicalement (Qdrant, côté serveur).
LEXICAL_DESCRIPTION_CHARS: Final = 3_000


def index_version(model: str) -> str:
    """Identifie un plongement : le modèle **et** la composition du texte."""
    return f"{model}@{EMBEDDING_TEXT_VERSION}"


def cut(text: str, limit: int) -> str:
    """Coupe à `limit` caractères, sur une frontière de mot."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def traits(offer: Offer) -> list[str]:
    """Contrat, télétravail, temps, expérience : ce qu'on a compris de l'offre."""
    return [
        part
        for part in (
            offer.contract_type.label if offer.contract_type else None,
            offer.remote.value.replace("_", " ") if offer.remote else None,
            offer.schedule.value.replace("_", " ") if offer.schedule else None,
            offer.experience.value if offer.experience else None,
        )
        if part
    ]


def embedding_text(offer: Offer) -> str:
    """Le texte dont on calcule le plongement dense."""
    header = [offer.title]
    context = [
        part
        for part in (
            offer.company.name if offer.company else None,
            offer.location.label if offer.location else None,
        )
        if part
    ]
    if context:
        header.append(" — ".join(context))
    if found := traits(offer):
        header.append(", ".join(found))
    body = cut(" ".join(offer.description.split()), EMBEDDING_DESCRIPTION_CHARS)
    return "\n".join([*header, body]).strip()


def title_text(offer: Offer) -> str:
    """Le texte de la branche lexicale d'intitulé : le titre seul."""
    return offer.title


def body_text(offer: Offer) -> str:
    """Le texte de la branche lexicale de corps : titre, employeur, description."""
    parts = [
        offer.title,
        offer.company.name if offer.company else "",
        cut(" ".join(offer.description.split()), LEXICAL_DESCRIPTION_CHARS),
    ]
    return " ".join(part for part in parts if part)
