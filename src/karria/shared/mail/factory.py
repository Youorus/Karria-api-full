"""Choix de l'adaptateur d'envoi, à partir de la configuration.

**Le seul endroit du dépôt qui teste le fournisseur.** Un `if provider ==`
ailleurs signifierait qu'un module connaît Gmail — et le jour où l'on
change de service, il faudrait aller le rechercher. Même règle que pour le
stockage objet (`shared/storage/factory.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from karria.shared.mail.console import ConsoleMailer
from karria.shared.mail.ports import Mailer
from karria.shared.mail.smtp import SmtpMailer

if TYPE_CHECKING:
    from karria.shared.config import MailSettings


def build_mailer(settings: MailSettings) -> Mailer:
    """Construit l'adaptateur correspondant à la configuration."""
    from karria.shared.config import MailProvider  # noqa: PLC0415 - cycle d'import

    if settings.provider is MailProvider.SMTP:
        return SmtpMailer(settings)
    return ConsoleMailer(settings.app_url, settings.from_address or settings.username or None)
