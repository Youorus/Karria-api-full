"""Contexte de corrélation propagé sur toute la durée d'une requête.

Chaque requête reçoit un identifiant unique, attaché automatiquement à
**toutes** les lignes de log qu'elle produit et renvoyé au client dans
l'en-tête `X-Request-ID` ainsi que dans le corps des réponses d'erreur.

L'utilisateur qui signale un bug donne cet identifiant ; une seule
recherche suffit alors pour reconstituer la requête entière. C'est la
différence entre un diagnostic de deux minutes et une soirée perdue.

L'implémentation repose sur `contextvars` : le contexte suit la tâche
asyncio courante sans être partagé entre requêtes concurrentes.
"""

from __future__ import annotations

from contextvars import ContextVar

import structlog

CORRELATION_ID_HEADER = "X-Request-ID"

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def bind_correlation_id(correlation_id: str) -> None:
    """Attache un identifiant de corrélation au contexte courant."""
    _correlation_id.set(correlation_id)
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def get_correlation_id() -> str | None:
    """Retourne l'identifiant de corrélation courant, s'il existe."""
    return _correlation_id.get()


def clear_context() -> None:
    """Purge le contexte en fin de requête.

    Indispensable : les workers réutilisent leurs tâches, et un contexte
    résiduel attribuerait les logs d'une requête à la précédente.
    """
    _correlation_id.set(None)
    structlog.contextvars.clear_contextvars()
