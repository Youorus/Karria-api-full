"""Hiérarchie d'erreurs de l'application.

Contrat
-------
- Ces exceptions **ne connaissent pas HTTP**. Aucun code 404 ou 403 ici.
  Elles portent une intention sémantique (`NOT_FOUND`, `FORBIDDEN`) ; la
  traduction en statut HTTP vit dans `karria.shared.errors.handlers`.
  C'est ce qui permet au métier d'être appelé depuis un worker, une tâche
  planifiée ou une commande CLI sans traîner FastAPI avec lui.
- Le domaine lève des exceptions **typées**, jamais `HTTPException` ni
  `ValueError`. Une erreur métier est une valeur de retour légitime du
  domaine ; elle doit être aussi précise que le succès.
- Ce module fait partie du **noyau partagé** : il ne dépend de rien, ce qui
  autorise les couches `domain/` à l'importer sans violer les frontières.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar


class ErrorCode(StrEnum):
    """Nature sémantique d'une erreur, indépendante du transport.

    Ces valeurs font partie du **contrat public de l'API** : un client peut
    brancher sa logique dessus. Elles se renomment donc comme une route,
    c'est-à-dire jamais sans version.
    """

    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    INVALID_INPUT = "invalid_input"
    RULE_VIOLATION = "rule_violation"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    #: La ressource a existé et n'existe plus — un parcours de connexion
    #: périmé, par exemple. Distinct de `NOT_FOUND` : le client sait qu'il
    #: doit **recommencer**, pas corriger un identifiant.
    GONE = "gone"
    #: Trop de tentatives ou de demandes. `details.retry_after` dit, en
    #: secondes, quand réessayer.
    RATE_LIMITED = "rate_limited"
    DEPENDENCY_FAILURE = "dependency_failure"
    INTERNAL = "internal_error"


class KarriaError(Exception):
    """Racine de toutes les erreurs maîtrisées de l'application."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        #: Contexte structuré destiné au client. Ne doit jamais contenir de
        #: secret, de trace d'exécution ni de détail d'implémentation.
        self.details: dict[str, Any] = dict(details or {})


class DomainError(KarriaError):
    """Violation d'une règle métier ou d'un invariant du domaine.

    Imputable à l'appelant ou à l'état du système, jamais à une panne.
    """


class InfrastructureError(KarriaError):
    """Défaillance d'une dépendance externe (base, file, service tiers).

    Distincte de `DomainError` car elle appelle une réaction différente :
    on réessaie une panne, on ne réessaie pas une règle métier.
    """


# ---------------------------------------------------------------------
# Erreurs de domaine
# ---------------------------------------------------------------------


class EntityNotFoundError(DomainError):
    """Une ressource désignée explicitement n'existe pas."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND

    def __init__(self, entity: str, identifier: object) -> None:
        super().__init__(
            f"{entity} introuvable.",
            details={"entity": entity, "identifier": str(identifier)},
        )


class EntityAlreadyExistsError(DomainError):
    """La création violerait une contrainte d'unicité métier."""

    code: ClassVar[ErrorCode] = ErrorCode.ALREADY_EXISTS

    def __init__(self, entity: str, field: str, value: object) -> None:
        super().__init__(
            f"{entity} existe déjà.",
            details={"entity": entity, "field": field, "value": str(value)},
        )


class InvalidInputError(DomainError):
    """Une donnée est syntaxiquement valide mais métier-invalide.

    À distinguer d'une erreur de validation Pydantic : ici la forme est
    correcte, c'est le sens qui ne l'est pas (une date de fin antérieure
    à la date de début, par exemple).
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_INPUT


class BusinessRuleViolationError(DomainError):
    """Une règle du domaine interdit l'opération demandée."""

    code: ClassVar[ErrorCode] = ErrorCode.RULE_VIOLATION


class AuthenticationError(DomainError):
    """L'appelant n'a pas prouvé son identité."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHENTICATED


class AuthorizationError(DomainError):
    """L'appelant est identifié mais n'a pas le droit d'agir.

    Le message reste volontairement vague : préciser *pourquoi* l'accès est
    refusé renseigne un attaquant sur la structure des ressources.
    """

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN


class ResourceGoneError(DomainError):
    """Une ressource a existé et n'est plus utilisable.

    Un parcours périmé, un lien consommé : le client ne peut pas le
    corriger, il doit recommencer. C'est ce qui le distingue d'un
    `EntityNotFoundError`, où un identifiant faux est en cause.
    """

    code: ClassVar[ErrorCode] = ErrorCode.GONE


class RateLimitedError(DomainError):
    """L'appelant a dépassé le rythme autorisé.

    Porte toujours `details.retry_after`, en secondes : un client qui ne
    sait pas quand réessayer réessaie tout de suite, ce qui est exactement
    ce qu'on cherche à empêcher.
    """

    code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED

    def __init__(
        self, message: str, *, retry_after: int, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message, details={**(details or {}), "retry_after": retry_after})
        self.retry_after = retry_after


# ---------------------------------------------------------------------
# Erreurs d'infrastructure
# ---------------------------------------------------------------------


class DependencyUnavailableError(InfrastructureError):
    """Une dépendance externe est injoignable ou en échec."""

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_FAILURE

    def __init__(self, dependency: str, reason: str | None = None) -> None:
        super().__init__(
            f"Le service « {dependency} » est temporairement indisponible.",
            details={"dependency": dependency} | ({"reason": reason} if reason else {}),
        )
