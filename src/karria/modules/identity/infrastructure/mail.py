"""Acheminement du code de connexion par le centre d'e-mails du socle.

Implémentation du port `LoginCodeSender`. Elle ne connaît ni SMTP ni
gabarit : elle remet un `OutgoingMail` au `Mailer`, qui porte le registre
(`MailMessage.LOGIN_CODE`), la mise en page et le transport.
"""

from __future__ import annotations

from karria.shared.errors import DependencyUnavailableError
from karria.shared.i18n import Language
from karria.shared.logging import get_logger
from karria.shared.mail import MailDeliveryError, Mailer, MailMessage, OutgoingMail

logger = get_logger(__name__)


class MailLoginCodeSender:
    """Envoie le code par e-mail, via le gabarit `login_code` du socle."""

    def __init__(self, mailer: Mailer) -> None:
        self._mailer = mailer

    async def send(
        self, *, email: str, code: str, expires_in_minutes: int, language: Language
    ) -> None:
        """Achemine le code.

        Raises:
            DependencyUnavailableError: Le transport a refusé le message.
                Traduit ici pour que l'appelant n'ait pas à connaître les
                erreurs du centre d'e-mails.
        """
        try:
            await self._mailer.send(
                OutgoingMail(
                    message=MailMessage.LOGIN_CODE,
                    to=email,
                    context={"code": code, "expires_in": expires_in_minutes},
                    language=language,
                )
            )
        except MailDeliveryError as exc:
            logger.error("auth.login_code_delivery_failed", error=type(exc).__name__)
            raise DependencyUnavailableError("e-mail", type(exc).__name__) from exc
