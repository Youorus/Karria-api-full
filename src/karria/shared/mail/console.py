"""Adaptateur de développement — écrit le message au lieu de l'envoyer.

Sert deux usages :

- **en local**, où l'on veut lire le code sans configurer un serveur ;
- **en test**, où aucun courriel réel ne doit partir (AGENTS.md : « aucun
  appel réel à un fournisseur externe dans les tests »).

Il **rend réellement** le message avant de l'afficher : un gabarit cassé,
une variable manquante ou un HTML mal formé échouent donc en développement,
et non le jour de la mise en production.
"""

from __future__ import annotations

from karria.shared.logging import get_logger
from karria.shared.mail.ports import OutgoingMail
from karria.shared.mail.rendering import render_for

logger = get_logger(__name__)


class ConsoleMailer:
    """Affiche le message dans les journaux applicatifs."""

    def __init__(
        self, app_url: str = "https://karria.app", contact_email: str | None = None
    ) -> None:
        self._app_url = app_url
        self._contact_email = contact_email
        #: Messages rendus depuis le démarrage. Sert aux tests de bout en
        #: bout, qui ont besoin de « lire le courriel » sans boîte mail.
        self.sent: list[tuple[OutgoingMail, str]] = []

    async def send(self, mail: OutgoingMail) -> None:
        """Rend le message et l'écrit dans les journaux."""
        rendered = (
            render_for(mail, app_url=self._app_url, contact_email=self._contact_email)
            if self._contact_email
            else render_for(mail, app_url=self._app_url)
        )
        self.sent.append((mail, rendered.text_body))

        logger.warning(
            "mail.rendered_to_console",
            message=mail.message.value,
            subject=rendered.subject,
            recipient=mail.to,
            body=rendered.text_body,
        )

    async def check_health(self) -> None:
        """Toujours disponible : il n'y a rien à joindre."""
        return
