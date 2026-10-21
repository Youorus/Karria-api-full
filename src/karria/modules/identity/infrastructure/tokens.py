"""Fabrique de secrets et empreintes — implémentation du port `TokenFactory`.

Aucune dépendance externe : `secrets`, `hashlib` et `hmac` suffisent. Un
jeton opaque n'a besoin ni de chiffrement ni de signature (ADR-0010).

Pourquoi SHA-256 nu, et pas Argon2
----------------------------------
Un mot de passe se hache lentement parce qu'il est devinable. Un jeton tiré
sur 256 bits d'aléa ne l'est pas : aucune force brute ne l'atteindra. Argon2
coûterait des dizaines de millisecondes **à chaque requête**, pour rien.

Le **poivre** de la configuration (`SECURITY__TOKEN_PEPPER`) s'ajoute avant
le condensat. Il protège surtout les codes à six chiffres, eux parfaitement
énumérables : sans poivre, une fuite de la table des parcours suffirait à
retrouver un code en un million d'essais hors ligne.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

#: 32 octets = 256 bits : hors de portée de toute recherche exhaustive.
_TOKEN_BYTES: Final = 32


class Sha256TokenFactory:
    """Produit des secrets imprévisibles et leurs empreintes."""

    def __init__(self, pepper: str = "") -> None:
        self._pepper = pepper.encode()

    def new_token(self) -> str:
        """Produit un secret de 256 bits, encodé pour tenir dans une URL."""
        return secrets.token_urlsafe(_TOKEN_BYTES)

    def fingerprint(self, secret: str) -> str:
        """Le condensat sous lequel le secret est stocké."""
        return hashlib.sha256(self._pepper + secret.encode()).hexdigest()

    def new_numeric_code(self, digits: int) -> str:
        """Produit un code numérique imprévisible, zéros de tête compris.

        `secrets.randbelow` et non `random.randint` : le générateur par
        défaut est reproductible dès qu'on observe assez de ses sorties.
        """
        return str(secrets.randbelow(10**digits)).zfill(digits)

    def matches(self, secret: str, fingerprint: str) -> bool:
        """Compare à temps constant : la durée ne révèle rien."""
        return hmac.compare_digest(self.fingerprint(secret), fingerprint)
