"""Le tour de conversation : ce que Karria répond, et ce qu'il en fait.

Le modèle de langage **propose** (une réponse, des critères, une question) ;
le domaine **décide** : on cherche quand les critères sont complets, on pose
une question sinon, et une seule à la fois. Le modèle ne lance rien.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Final

from karria.modules.offers.domain.criteria import SearchCriteria
from karria.modules.offers.domain.guardrails import DECLINE_REPLY, Intent

#: Tours de conversation par personne et par jour. Au-delà, 429.
ASSISTANT_DAILY_LIMIT: Final = 120
ASSISTANT_RETRY_AFTER: Final = timedelta(hours=1)
#: Messages passés relus par le modèle à chaque tour.
HISTORY_WINDOW: Final = 12
#: Longueur maximale d'un message de la personne.
MESSAGE_MAX_LENGTH: Final = 2_000
#: Longueur maximale d'une réponse de Karria, avant la phrase sur les résultats.
REPLY_MAX_LENGTH: Final = 600
#: Réponses rapides proposées sous la réponse : peu, et courtes. Elles
#: affinent sans jamais devenir un formulaire ; la personne peut les ignorer.
SUGGESTIONS_MAX: Final = 4
SUGGESTION_MAX_LENGTH: Final = 40


class DialogueStatus(StrEnum):
    """Ce que le tour a produit."""

    #: Les critères sont complets : la recherche a été lancée, le fil mis à jour.
    SEARCH = "search"
    #: Il manque quelque chose : la réponse pose une question.
    CLARIFY = "clarify"
    #: Hors recherche d'emploi : une réponse, rien d'autre.
    CHAT = "chat"


@dataclass(frozen=True, slots=True)
class DialogueProposal:
    """Ce que le modèle rend, une fois validé contre le schéma."""

    reply: str
    criteria: SearchCriteria | None
    #: Le modèle juge le message hors sujet (ou tente de le détourner).
    off_topic: bool = False
    #: Réponses rapides suggérées (« Uniquement en CDI », « Jusqu'à 50 km »).
    suggestions: tuple[str, ...] = ()
    #: Ce que la personne veut, classé par le modèle (schéma imposé).
    intent: Intent | None = None

    @property
    def resolved_intent(self) -> Intent:
        """L'intention, déduite des drapeaux quand le modèle ne l'a pas dite."""
        if self.intent is not None:
            return self.intent
        if self.off_topic:
            return Intent.OFF_TOPIC
        return Intent.SEARCH if self.criteria is not None else Intent.SMALL_TALK


@dataclass(frozen=True, slots=True)
class DialogueOutcome:
    """La décision du domaine pour ce tour."""

    status: DialogueStatus
    reply: str
    criteria: SearchCriteria | None
    suggestions: tuple[str, ...] = ()
    intent: Intent = Intent.SEARCH


def clean_suggestions(suggestions: tuple[str, ...]) -> tuple[str, ...]:
    """Au plus quatre, courtes, sans doublon ni vide."""
    seen: dict[str, str] = {}
    for suggestion in suggestions:
        text = " ".join(suggestion.split())
        if text and len(text) <= SUGGESTION_MAX_LENGTH and text.lower() not in seen:
            seen[text.lower()] = text
    return tuple(seen.values())[:SUGGESTIONS_MAX]


def decide(proposal: DialogueProposal, previous: SearchCriteria | None) -> DialogueOutcome:
    """Tranche : chercher, préciser, ou simplement répondre.

    Le modèle peut avoir oublié un critère d'un tour précédent (une ville
    dite au premier message) : les critères précédents comblent les trous.
    """
    reply = proposal.reply.strip()[:REPLY_MAX_LENGTH]
    suggestions = clean_suggestions(proposal.suggestions)
    intent = proposal.resolved_intent
    if intent is Intent.OFF_TOPIC:
        return DialogueOutcome(
            status=DialogueStatus.CHAT,
            reply=reply or DECLINE_REPLY,
            criteria=previous,
            suggestions=suggestions,
            intent=intent,
        )
    if proposal.criteria is None or intent in (
        Intent.CAREER_TALK,
        Intent.APP_HELP,
        Intent.SMALL_TALK,
    ):
        return DialogueOutcome(
            status=DialogueStatus.CHAT,
            reply=reply,
            criteria=previous,
            suggestions=suggestions,
            intent=intent,
        )
    criteria = merge(previous, proposal.criteria)
    if criteria.is_searchable:
        return DialogueOutcome(
            status=DialogueStatus.SEARCH,
            reply=reply,
            criteria=criteria,
            suggestions=suggestions,
            intent=intent,
        )
    return DialogueOutcome(
        status=DialogueStatus.CLARIFY,
        reply=reply or fallback_question(criteria),
        criteria=criteria,
        suggestions=suggestions,
        intent=intent,
    )


def merge(previous: SearchCriteria | None, proposed: SearchCriteria) -> SearchCriteria:
    """Les critères proposés, complétés par les précédents là où ils sont vides."""
    if previous is None:
        return proposed
    data = proposed.model_dump()
    if not data["job_titles"]:
        data["job_titles"] = previous.job_titles
    if not data["keywords"]:
        data["keywords"] = previous.keywords
    if not data["semantic_query"]:
        data["semantic_query"] = previous.semantic_query
    if data["location"] is None and not data["whole_france"]:
        data["location"] = previous.location.model_dump() if previous.location else None
        data["whole_france"] = previous.whole_france
    return SearchCriteria.model_validate(data)


def fallback_question(criteria: SearchCriteria) -> str:
    """La question à poser si le modèle n'en a pas formulé."""
    missing = criteria.missing()
    if "job" in missing:
        return "Quel métier ou quel type de poste cherches-tu ?"
    return "Dans quelle ville, ou quelle zone, cherches-tu ? Tu peux aussi dire « partout en France » ou « en télétravail »."


def announce_results(count: int) -> str:
    """La phrase ajoutée à la réponse quand une recherche a tourné."""
    if count == 0:
        return "Je n'ai trouvé aucune offre qui corresponde pour l'instant ; je lance une recherche et je vous préviens dès qu'il en arrive."
    if count == 1:
        return "J'ai trouvé une offre, elle est dans votre fil."
    return f"J'ai trouvé {count} offres, elles sont dans votre fil."
