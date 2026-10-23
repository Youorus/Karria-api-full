"""Les garde-fous déterministes autour du modèle : avant l'appel, après l'appel.

Contrat
-------
Le modèle de langage **propose** une réponse ; il ne décide ni de qui est
Karria, ni de ce qui sort. Deux couches de règles, sans réseau ni modèle :

- **avant** (`pre_classify`) : un classement bon marché du message —
  tentative d'injection, question d'identité, demande hors sujet évidente.
  Une question d'identité reçoit la réponse de la constitution **sans
  appel au modèle** : c'est la réponse la plus robuste qui soit ;
- **après** (`guard_reply`) : la réponse ne nomme aucun fournisseur ni
  modèle, ne laisse pas fuir la consigne, ne cite ni lien ni numéro
  d'offre (les offres ne viennent que de notre index, par le fil) ; une
  réponse qui faute est **remplacée** par un gabarit sûr, et l'événement
  journalisé par l'appelant.

Ces règles ne dépendent pas du contexte de la conversation ni de sa
longueur : elles s'appliquent à chaque tour, identiques.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from karria.modules.offers.domain.normalization import fold


class Intent(StrEnum):
    """Ce que la personne veut, tel que le modèle le classe (schéma imposé)."""

    #: Une recherche d'offres : métier, lieu, contrat…
    SEARCH = "search"
    #: Affiner la recherche en cours.
    REFINE = "refine"
    #: Parler métier, marché, CV, entretien, motivation — on répond, et on ramène.
    CAREER_TALK = "career_talk"
    #: Comment marche l'app.
    APP_HELP = "app_help"
    #: Bonjour, merci, comment ça va.
    SMALL_TALK = "small_talk"
    #: Devoirs, code, recettes, blagues, politique, médical, juridique… : on décline, gentiment.
    OFF_TOPIC = "off_topic"


class PreFlag(StrEnum):
    """Ce que la couche d'avant repère."""

    INJECTION = "injection"
    IDENTITY_PROBE = "identity_probe"
    OBVIOUS_OFF_TOPIC = "obvious_off_topic"


#: La réponse d'identité, la même quoi qu'on demande et quoi qu'on ait dit avant.
IDENTITY_REPLY: Final = (
    "Je suis Karria, l'assistante de l'application Karria, créée par l'équipe Karria. "
    "Je suis là pour t'aider dans ta recherche d'emploi : dis-moi quel poste tu vises et où."
)
#: La réponse de remplacement quand la sortie du modèle faute.
SAFE_REPLY: Final = (
    "Je préfère rester sur ce que je sais faire : t'aider à trouver un emploi. "
    "Dis-moi quel poste tu cherches et dans quelle ville, et je m'en occupe."
)
#: Le refus bref, sans leçon, suivi du retour vers la recherche.
DECLINE_REPLY: Final = (
    "Ce n'est pas mon domaine, mais si tu veux on avance sur ta recherche d'emploi : "
    "quel poste t'intéresse ?"
)

#: Les noms de fournisseurs et de modèles qu'une réponse ne doit jamais porter.
PROVIDER_NAMES: Final = re.compile(
    r"\b(gemini|google|deepmind|claude|anthropic|openai|chatgpt|gpt-?\d*|mistral|llama|"
    r"copilot|bard|grok|deepseek|qwen|vertex|bedrock|azure)\b",
    re.IGNORECASE,
)
#: Des phrases de la consigne qui, dans une réponse, signent une fuite.
PROMPT_LEAK_MARKERS: Final = re.compile(
    r"(TA T[AÂ]CHE|R[EÈ]GLES DE (COMPR[EÉ]HENSION|DIALOGUE)|<message>|</message>|"
    r"system_instruction|consigne de syst[eè]me|instruction syst[eè]me|mon prompt|mes instructions sont)",
    re.IGNORECASE,
)
#: Une réponse ne cite jamais un lien ni un numéro d'offre : les offres vivent dans le fil.
INVENTED_OFFER: Final = re.compile(
    r"(https?://|www\.|offre\s*(n°|numéro|#)\s*\d+|r[eé]f[eé]rence\s*\d{3,})", re.IGNORECASE
)

_INJECTION: Final = re.compile(
    r"(ignore[sz]? (toutes? )?(tes|les|vos) (consignes|instructions|r[eè]gles)|oublie (tout|tes consignes|qui tu es)|"
    r"tu es maintenant|a partir de maintenant tu|fais comme si tu etais|jailbreak|dan mode|"
    r"(affiche|revele|montre|donne|repete|ecris)[^.]{0,30}(prompt|consigne|instructions? (de )?systeme)|"
    r"system prompt|developer message|mode developpeur)"
)
_IDENTITY: Final = re.compile(
    r"((qui|quoi) (es|est)[- ]tu|t'?es qui|tu es (qui|quoi)|qui t'?a (cree|fait|programme|developpe|entraine)|"
    r"qui te (possede|detient|controle)|quel (modele|llm|ia) (es|utilises)[- ]tu|"
    r"(es|c'est)[- ]tu (gemini|chatgpt|gpt|claude|une ia|un robot|un humain|une intelligence artificielle)|"
    r"tu (tournes|fonctionnes|marches) (sur|avec) (quel|quoi)|sur quel (modele|llm|ia|moteur) (tu|es)|"
    r"quelle (ia|intelligence artificielle|technologie) (es|utilises)|"
    r"(gemini|chatgpt|claude|openai|anthropic|google|mistral)\b.{0,20}\?|"
    r"ton (modele|createur|concepteur|proprietaire)|qui est derriere (toi|karria)|quel est ton (nom|modele|prompt))"
)
_OFF_TOPIC: Final = re.compile(
    r"(recette|cuisine[rz]? (un|une|des|le|la)|blague|poeme|raconte[- ]moi une (histoire|blague)|"
    r"devoirs?|exercice de (maths?|physique|chimie)|resous|equation|"
    r"ecris[- ]moi (un|une|du|le) (code|script|programme|fonction)|en python|en javascript|"
    r"meteo|horoscope|resultat (du match|des elections)|president|election|"
    r"(j'ai|je) (mal|une douleur)|medicament|diagnostic|ordonnance|"
    r"(avocat|proces|porter plainte|divorce|garde des enfants|contrat de mariage)|"
    r"traduis|traduction|capitale de|combien font|cryptomonnaie|bitcoin|action bourse)"
)


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """Ce que la couche d'après a décidé."""

    reply: str
    replaced: bool
    reason: str | None = None


def pre_classify(message: str) -> PreFlag | None:
    """Repère, avant tout appel, une injection, une question d'identité ou un hors-sujet évident."""
    text = fold(message)
    if _INJECTION.search(text):
        return PreFlag.INJECTION
    if _IDENTITY.search(text):
        return PreFlag.IDENTITY_PROBE
    if _OFF_TOPIC.search(text):
        return PreFlag.OBVIOUS_OFF_TOPIC
    return None


def guard_reply(reply: str) -> GuardVerdict:
    """Vérifie une réponse du modèle ; remplace ce qui faute par un gabarit sûr."""
    if PROVIDER_NAMES.search(reply):
        return GuardVerdict(IDENTITY_REPLY, replaced=True, reason="provider_named")
    if PROMPT_LEAK_MARKERS.search(reply):
        return GuardVerdict(SAFE_REPLY, replaced=True, reason="prompt_leak")
    if INVENTED_OFFER.search(reply):
        return GuardVerdict(SAFE_REPLY, replaced=True, reason="invented_offer")
    return GuardVerdict(reply.strip(), replaced=False)
