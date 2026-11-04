"""La mise en page des e-mails refaite (ADR-0032) : sans étiquette, sombre réel, logo toujours visible."""

from __future__ import annotations

import re

import pytest

from karria.shared.i18n import Language
from karria.shared.mail import MailMessage, OutgoingMail, layout, render_for, theme

pytestmark = pytest.mark.unit

CONTEXTE = {
    "code": "418293",
    "expires_in": 10,
    "first_name": "Awa",
    "subject": "Test",
    "body": "Corps.",
    "user_email": "awa@exemple.fr",
    "preview": "Aperçu.",
    "kind_label": "texte",
    "console_url": "https://api.karria.fr/console",
    "sender_name": "Awa Diallo",
    "documents": "- CV - Awa Diallo.pdf",
    "count": 1,
}


def _html(message: MailMessage, langue: Language = Language.FR) -> str:
    return render_for(
        OutgoingMail(message=message, to="awa@exemple.fr", context=CONTEXTE, language=langue),
        app_url="https://karria.fr",
    ).html_body


class TestSansEtiquette:
    @pytest.mark.parametrize("message", list(MailMessage))
    def test_aucune_barre_laterale_ni_boite_bordee_ni_pastille(self, message: MailMessage) -> None:
        html = _html(message)
        assert "border-left" not in html
        assert "border-right" not in html
        # Le seul filet autorisé : celui du pied, au-dessus de la ligne de marque.
        assert html.count("border-top") == 1
        assert "background:#fdecec" not in html

    def test_le_code_est_un_grand_nombre_sans_boite(self) -> None:
        html = _html(MailMessage.LOGIN_CODE)
        start = html.index("font-size:36px")
        block = html[start - 120 : start + 260]
        assert "418293" in block
        assert "border:" not in block
        assert "<table" not in block
        assert "<p " in block

    def test_l_avertissement_est_une_phrase(self) -> None:
        html = _html(MailMessage.LOGIN_CODE)
        assert "pas demandé ce code" in html
        assert layout.warning("x").startswith("<p ")


class TestSombreEtClair:
    def test_le_document_declare_et_style_les_deux_themes(self) -> None:
        html = _html(MailMessage.WELCOME)
        assert '<meta name="color-scheme" content="light dark">' in html
        assert "@media (prefers-color-scheme: dark)" in html
        assert "[data-ogsc]" in html
        assert f".bg{{background:{theme.SOMBRE_FOND}!important}}" in html
        assert f"background:{theme.FOND}" in html

    def test_chaque_bloc_de_texte_porte_une_classe_que_le_sombre_recolore(self) -> None:
        html = _html(MailMessage.WELCOME)
        for tag in re.findall(r"<(?:p|h1)\b[^>]*>", html):
            assert 'class="' in tag, tag

    def test_les_deux_logos_sont_poses_le_sombre_masque_en_clair(self) -> None:
        html = _html(MailMessage.WELCOME)
        assert theme.LOGO_CLAIR_URL in html
        assert theme.LOGO_SOMBRE_URL in html
        dark = html[html.index(theme.LOGO_SOMBRE_URL) : html.index(theme.LOGO_SOMBRE_URL) + 400]
        assert "display:none" in dark
        assert ".logo-dark{display:block!important" in html
        assert theme.LOGO_CLAIR_URL.startswith("https://storage.karria.fr/")

    def test_la_largeur_est_fluide_et_bornee(self) -> None:
        html = _html(MailMessage.WELCOME)
        assert f"max-width:{theme.LARGEUR}px" in html
        assert "@media only screen and (max-width:480px)" in html
        assert 'name="viewport"' in html


class TestContenu:
    def test_le_message_de_documents_liste_les_pieces_et_signe_du_nom(self) -> None:
        html = _html(MailMessage.DOCUMENTS_SENT)
        assert "CV - Awa Diallo.pdf" in html
        assert "L’équipe Karria" not in html
        assert html.count("Awa Diallo") >= 2
        assert "$" not in html.replace("$", "", 0) or "$sender_name" not in html

    def test_la_version_anglaise_existe(self) -> None:
        html = _html(MailMessage.DOCUMENTS_SENT, Language.EN)
        assert "Documents from Awa Diallo" in html
        assert "You receive this message" in html
