"""Prévenir l'équipe par le centre d'e-mails du socle.

Implémentation du port `TeamAlerter`. Elle ne connaît ni SMTP ni gabarit :
elle remet un `OutgoingMail` au `Mailer`, qui porte le registre
(`MailMessage.SUPPORT_NEW_MESSAGE`), la mise en page et le transport. Un
échec se journalise et ne remonte pas : le message de la personne est déjà
écrit, et c'est lui qui compte.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from karria.modules.support.domain import MessageKind
from karria.shared.i18n import Language
from karria.shared.logging import get_logger
from karria.shared.mail import MailDeliveryError, Mailer, MailMessage, OutgoingMail

logger = get_logger(__name__)

_KIND_LABELS: Final[dict[MessageKind, str]] = {
    MessageKind.TEXT: "texte",
    MessageKind.VOICE: "message vocal",
    MessageKind.IMAGE: "image",
}


class MailTeamAlerter:
    """Envoie « un mot de … » à l'adresse de garde, avec le lien de la console."""

    def __init__(self, mailer: Mailer, *, team_email: str, console_url: str) -> None:
        self._mailer = mailer
        self._team_email = team_email
        self._console_url = console_url

    async def alert(
        self, *, conversation_id: UUID, user_email: str | None, preview: str, kind: MessageKind
    ) -> None:
        """Achemine l'alerte ; journalise si le transport refuse."""
        try:
            await self._mailer.send(
                OutgoingMail(
                    message=MailMessage.SUPPORT_NEW_MESSAGE,
                    to=self._team_email,
                    context={
                        "user_email": user_email or "un compte sans adresse",
                        "preview": preview,
                        "kind_label": _KIND_LABELS[kind],
                        "console_url": f"{self._console_url}#{conversation_id}",
                    },
                    language=Language.FR,
                )
            )
        except MailDeliveryError as exc:
            logger.error(
                "support.team_alert_failed",
                conversation_id=str(conversation_id),
                error=type(exc).__name__,
            )


class SilentTeamAlerter:
    """Ne prévient personne : l'adresse de garde est vide."""

    async def alert(
        self, *, conversation_id: UUID, user_email: str | None, preview: str, kind: MessageKind
    ) -> None:
        """Journalise seulement."""
        _ = (user_email, preview, kind)
        logger.warning("support.team_alert_skipped_no_email", conversation_id=str(conversation_id))
