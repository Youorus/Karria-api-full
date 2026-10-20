"""L'implémentation réelle : l'horloge du système."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Implémentation du port `Clock` : l'horloge du système, en UTC.

    Toujours conscient du fuseau. Un `datetime` naïf comparé à un
    horodatage venu de PostgreSQL lève à l'exécution, et le fait toujours
    dans le cas qu'on n'a pas testé.
    """

    def now(self) -> datetime:
        """Retourne l'instant courant en UTC."""
        return datetime.now(UTC)
