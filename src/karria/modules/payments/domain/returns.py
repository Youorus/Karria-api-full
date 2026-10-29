"""Où l'on revient après le paiement : la règle de retour.

Stripe ne renvoie que vers le site de Karria (`https://karria.fr/…`) ou vers
l'application par son schéma (`karria://…`). Une adresse quelconque ferait
de la page Stripe aux couleurs de Karria un relais d'hameçonnage.

Stripe n'accepte pas un schéma d'application comme adresse de retour : c'est
une page `https` de l'API qui reçoit la personne, puis la renvoie vers la
destination **validée ici** — jamais vers ce qu'une requête dirait.
"""

from __future__ import annotations

import urllib.parse
from typing import Final

from karria.shared.errors.exceptions import InvalidInputError

ALLOWED_RETURN_HOSTS: Final[frozenset[str]] = frozenset({"karria.fr", "www.karria.fr"})
APP_SCHEME: Final = "karria"


def validate_return_url(url: str) -> str:
    """L'adresse de retour, si elle vise Karria ; `InvalidInputError` sinon."""
    candidate = url.strip()
    parts = urllib.parse.urlsplit(candidate)
    if parts.scheme == APP_SCHEME and not parts.username and not parts.password:
        return candidate
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host in ALLOWED_RETURN_HOSTS and not parts.username:
        return candidate
    raise InvalidInputError(
        "L'adresse de retour doit viser Karria.",
        details={"reason": "return_url_not_allowed", "field": "return_url"},
    )


def with_query(url: str, **params: str) -> str:
    """Ajoute des paramètres à une adresse, en respectant ceux qu'elle porte déjà."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urllib.parse.urlencode(params)}"
