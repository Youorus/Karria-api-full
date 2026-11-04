"""Les pièces jointes du centre d'e-mails et le message `documents_sent` (ADR-0031)."""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from pydantic import SecretStr

from karria.modules.documents.application import DocumentFile
from karria.modules.documents.infrastructure import MailDocumentMailer
from karria.shared.config import MailProvider, MailSettings
from karria.shared.i18n import Language
from karria.shared.mail import (
    MAX_ATTACHMENTS_BYTES,
    Attachment,
    MailDeliveryError,
    MailMessage,
    OutgoingMail,
    render_for,
)
from karria.shared.mail.console import ConsoleMailer
from karria.shared.mail.smtp import SmtpMailer, _safe_file_name

pytestmark = pytest.mark.unit

SETTINGS = MailSettings(
    provider=MailProvider.SMTP,
    host="smtp.example.org",
    port=587,
    username="karria",
    password=SecretStr("secret"),
    from_address="bonjour@karria.fr",
)
CONTEXT = {"sender_name": "Awa Diallo", "documents": "- CV - Awa Diallo.pdf", "count": 1}


def _mail(*attachments: Attachment) -> OutgoingMail:
    return OutgoingMail(
        message=MailMessage.DOCUMENTS_SENT,
        to="rh@acme.fr",
        context=CONTEXT,
        attachments=attachments,
    )


class TestComposition:
    def test_les_pieces_jointes_suivent_le_texte_et_le_html(self) -> None:
        mail = _mail(
            Attachment("CV - Awa Diallo.pdf", "application/pdf", b"%PDF"),
            Attachment("Lettre de motivation - Acme.docx", "application/x-truc", b"PK"),
        )
        message: EmailMessage = SmtpMailer(SETTINGS)._build(mail, "Objet", "<p>x</p>", "x")

        assert message.get_content_type() == "multipart/mixed"
        parts = list(message.iter_parts())
        assert parts[0].get_content_type() == "multipart/alternative"
        names = [p.get_filename() for p in parts[1:]]
        assert names == ["CV - Awa Diallo.pdf", "Lettre de motivation - Acme.docx"]
        assert parts[1].get_content_type() == "application/pdf"
        assert parts[1].get_payload(decode=True) == b"%PDF"

    def test_sans_piece_jointe_le_message_reste_une_alternative(self) -> None:
        message = SmtpMailer(SETTINGS)._build(_mail(), "Objet", "<p>x</p>", "x")
        assert message.get_content_type() == "multipart/alternative"

    def test_des_pieces_trop_lourdes_sont_refusees_avant_le_reseau(self) -> None:
        heavy = Attachment("cv.pdf", "application/pdf", b"0" * (MAX_ATTACHMENTS_BYTES + 1))
        with pytest.raises(MailDeliveryError):
            SmtpMailer(SETTINGS)._build(_mail(heavy), "Objet", "<p>x</p>", "x")

    def test_un_nom_de_fichier_est_nettoye(self) -> None:
        assert _safe_file_name("../CV: Awa\\x.pdf") == "..CV Awax.pdf"
        assert _safe_file_name("  ") == "document"
        assert _safe_file_name("CV - Awa Diallo.pdf") == "CV - Awa Diallo.pdf"


class TestMessage:
    def test_le_message_est_signe_du_nom_de_la_personne_et_liste_les_pieces(self) -> None:
        rendered = render_for(_mail(), app_url="https://karria.fr")
        assert rendered.subject == "Documents de candidature de Awa Diallo"
        assert "Awa Diallo" in rendered.text_body
        assert "- CV - Awa Diallo.pdf" in rendered.text_body
        assert "L’équipe Karria" not in rendered.text_body
        assert rendered.text_body.count("Awa Diallo") >= 2

    async def test_la_console_garde_les_pieces_jointes(self) -> None:
        console = ConsoleMailer()
        await console.send(_mail(Attachment("cv.pdf", "application/pdf", b"%PDF")))
        [(mail, text)] = console.sent
        assert [a.file_name for a in mail.attachments] == ["cv.pdf"]
        assert "Awa Diallo" in text

    async def test_l_adaptateur_de_documents_remet_un_message_du_registre(self) -> None:
        console = ConsoleMailer()
        await MailDocumentMailer(console, language=Language.EN).send(
            to="rh@acme.fr",
            sender_name="Awa Diallo",
            documents=[
                DocumentFile("CV - Awa Diallo.pdf", "application/pdf", b"%PDF"),
                DocumentFile("Lettre de motivation - Acme.pdf", "application/pdf", b"%PDF2"),
            ],
        )
        [(mail, text)] = console.sent
        assert mail.message is MailMessage.DOCUMENTS_SENT
        assert mail.to == "rh@acme.fr"
        assert mail.language is Language.EN
        assert mail.context["count"] == 2
        assert [a.file_name for a in mail.attachments] == [
            "CV - Awa Diallo.pdf",
            "Lettre de motivation - Acme.pdf",
        ]
        assert "Lettre de motivation - Acme.pdf" in text
        assert (
            "Application documents from Awa Diallo" not in text
        )  # l'objet n'est pas dans le corps
