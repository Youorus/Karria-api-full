"""Câblage du module `app` : les seuils viennent de la configuration, le quota du socle."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends

from karria.shared.auth import Principal, allow_anonymous
from karria.shared.config import Settings, get_settings
from karria.shared.http import SlidingWindowLimiter

#: Lectures par origine sur la fenêtre : l'app demande une fois au démarrage.
RATE_LIMIT: Final = 60
RATE_WINDOW_SECONDS: Final = 600

_limiter = SlidingWindowLimiter(max_hits=RATE_LIMIT, window_seconds=RATE_WINDOW_SECONDS)

#: Une personne connectée, ou une origine anonyme admise sous quota (ADR-0025).
GuestPrincipalDep = Annotated[Principal | None, Depends(allow_anonymous(_limiter, scope="app"))]
SettingsDep = Annotated[Settings, Depends(get_settings)]
