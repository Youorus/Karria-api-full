"""Câblage du module `app` : les seuils viennent de la configuration, le quota du socle."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends

from karria.shared.auth import Principal, allow_anonymous
from karria.shared.config import Settings, get_settings
from karria.shared.http import SlidingWindowLimiter

#: Lectures par origine sur la fenêtre : l'app demande une fois au démarrage et
#: l'app n'envoie jamais de jeton ici, donc **tout le monde** est compté par
#: origine. Derrière une adresse partagée (opérateur mobile, campus), 60 lectures
#: refusaient la réponse — et l'app laisse passer un refus : la mise à jour
#: obligatoire ne s'imposait plus à tous (audit du 2026-11-20). La route ne lit
#: que la configuration : 1 200 lectures en dix minutes ne coûtent rien.
RATE_LIMIT: Final = 1200
RATE_WINDOW_SECONDS: Final = 600

_limiter = SlidingWindowLimiter(max_hits=RATE_LIMIT, window_seconds=RATE_WINDOW_SECONDS)

#: Une personne connectée, ou une origine anonyme admise sous quota (ADR-0025).
GuestPrincipalDep = Annotated[Principal | None, Depends(allow_anonymous(_limiter, scope="app"))]
SettingsDep = Annotated[Settings, Depends(get_settings)]
