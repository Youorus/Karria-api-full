"""Remettre des documents par le centre d'e-mails du socle, en pièces jointes (ADR-0031).

Implémentation du port `DocumentMailer`. Elle ne connaît ni SMTP ni
gabarit : elle remet un `OutgoingMail` au `Mailer`, qui porte le registre
(`MailMessage.DOCUMENTS_SENT`), la mise en page, la signature au nom de la
personne et le transport. Rien de ce que la personne tape n'entre dans le
message : le contexte ne porte que son nom et les noms des pièces.
"""

from __future__ import annotations

from karria.modules.documents.application.ports import DocumentFile
from karria.shared.i18n import Language
from karria.shared.mail import Attachment, Mailer, MailMessage, OutgoingMail


class MailDocumentMailer:
    """Envoie « Prénom Nom vous transmet ses documents », pièces jointes comprises."""

    def __init__(self, mailer: Mailer, *, language: Language = Language.FR) -> None:
        self._mailer = mailer
        self._language = language

    async def send(self, *, to: str, sender_name: str, documents: list[DocumentFile]) -> None:
        """Achemine ; les erreurs du centre d'e-mails remontent telles quelles (`MailDeliveryError`)."""
        await self._mailer.send(
            OutgoingMail(
                message=MailMessage.DOCUMENTS_SENT,
                to=to,
                context={
                    "sender_name": sender_name,
                    "documents": "\n".join(f"- {d.file_name}" for d in documents),
                    "count": len(documents),
                },
                language=self._language,
                attachments=tuple(
                    Attachment(file_name=d.file_name, content_type=d.content_type, data=d.data)
                    for d in documents
                ),
            )
        )
