"""Le centre d'envoi d'e-mails.

Ce qui est vérifié ici tient en une phrase : **aucun message ne peut
partir vide, cassé, ou avec une variable non substituée** — et le jour où
l'on ajoute un e-mail, on ne peut pas oublier son gabarit.

Aucun de ces tests ne joint un serveur. Le transport SMTP est un
adaptateur ; ce qui se teste, c'est le rendu et le registre.
"""

from __future__ import annotations

import html
import re
import smtplib
import ssl
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any, Final

import pytest
from pydantic import SecretStr

from karria.shared.config import MailProvider, MailSettings
from karria.shared.i18n import Language, from_accept_language, resolve
from karria.shared.mail import (
    REGISTRY,
    MailDeliveryError,
    MailMessage,
    OutgoingMail,
    RenderedMail,
    build_mailer,
    layout,
    render_for,
    theme,
)
from karria.shared.mail.console import ConsoleMailer
from karria.shared.mail.errors import (
    MailAuthenticationError,
    MailQuotaExhaustedError,
    MailRecipientRefusedError,
    MailServerUnavailableError,
)
from karria.shared.mail.rendering import TEMPLATES_DIR, _deeplink
from karria.shared.mail.smtp import SmtpMailer, _is_quota_exhausted

pytestmark = pytest.mark.unit

#: Ce qu'un **appelant** fournit. `recipient` et `app_url` n'y figurent
#: pas : ils sont ajoutés par `render_for`, et un gabarit qui en aurait
#: besoin sans les recevoir disparaîtrait en silence.
CONTEXTE: Final[dict[str, Any]] = {
    "code": "418293",
    # Ce que `scripts/test_smtp.py` passe pour le message de vérification
    # SMTP. Ce message-là ne part jamais tout seul, mais il traverse le
    # même rendu que les autres : la boucle des tests le couvre comme le
    # reste, et c'est ce qui garantit que le test manuel ne casse pas en
    # silence le jour où le gabarit commun change.
    "subject": "Test SMTP Karria",
    "body": "La configuration SMTP de Karria fonctionne correctement.",
    "expires_in": 10,
    "first_name": "Awa",
}


def _rendre(
    message: MailMessage,
    langue: Language = Language.FR,
    **surcharges: Any,
) -> RenderedMail:
    """Rend comme le ferait un adaptateur, contexte complété compris."""
    return render_for(
        OutgoingMail(
            message=message,
            to="awa@exemple.fr",
            context={**CONTEXTE, **surcharges},
            language=langue,
        ),
        app_url="https://app.karria.fr",
    )


#: Chaque message, dans chaque langue. Le produit cartésien plutôt qu'une
#: liste écrite à la main : ajouter une langue étend automatiquement la
#: couverture, au lieu de laisser un trou que personne ne remarque.
TOUTES_COMBINAISONS = [(m, lg) for m in MailMessage for lg in Language]


class TestRegistre:
    """Le registre est la source de vérité : il doit rester cohérent."""

    def test_chaque_message_a_une_entree(self) -> None:
        """Un membre de l'énumération sans entrée serait un envoi impossible."""
        assert set(REGISTRY) == set(MailMessage)

    @pytest.mark.parametrize(("message", "langue"), TOUTES_COMBINAISONS)
    def test_chaque_message_a_un_objet_et_un_gabarit_par_langue(
        self, message: MailMessage, langue: Language
    ) -> None:
        """Le test qui rend une traduction impossible à oublier.

        Sans lui, une langue ajoutée à l'énumération passerait inaperçue
        jusqu'à ce qu'un utilisateur reçoive un `KeyError` — c'est-à-dire
        rien du tout — au moment de se connecter.
        """
        rendu = _rendre(message, langue)

        assert rendu.subject
        assert rendu.html_body
        assert rendu.text_body

    @pytest.mark.parametrize("message", list(MailMessage))
    def test_aucune_variable_ne_survit_au_rendu(self, message: MailMessage) -> None:
        """Un `$code` littéral dans un e-mail de sécurité est une panne."""
        rendu = _rendre(message)

        for partie in (rendu.subject, rendu.html_body, rendu.text_body):
            assert not re.search(r"\$[a-z_]+", partie), partie


class TestRendu:
    """Le rendu produit bien deux versions, et échoue plutôt que de mentir."""

    def test_le_code_figure_dans_les_deux_versions(self) -> None:
        """Quelqu'un qui lit en texte brut doit voir son code."""
        rendu = _rendre(MailMessage.LOGIN_CODE)

        assert "418293" in rendu.html_body
        assert "418293" in rendu.text_body
        assert "418293" in rendu.subject

    def test_la_version_texte_ne_contient_pas_de_balise(self) -> None:
        """Sinon un client texte afficherait le HTML tel quel."""
        rendu = _rendre(MailMessage.WELCOME)

        assert "<" not in rendu.text_body

    def test_une_variable_manquante_fait_echouer_l_envoi(self) -> None:
        """Mieux vaut ne rien envoyer qu'envoyer un gabarit à trous."""
        with pytest.raises(KeyError):
            render_for(
                OutgoingMail(
                    message=MailMessage.LOGIN_CODE,
                    to="awa@exemple.fr",
                    context={"expires_in": 10},
                ),
                app_url="https://app.karria.fr",
            )

    def test_le_contexte_est_echappe(self) -> None:
        """Une valeur venue d'un formulaire ne doit pas injecter de HTML."""
        rendu = _rendre(MailMessage.WELCOME, first_name="<b>Awa")

        assert "<b>Awa" not in rendu.html_body
        assert "&lt;b&gt;Awa" in rendu.html_body

    def test_le_gras_d_un_gabarit_est_rendu_sans_ouvrir_d_injection(self) -> None:
        """`**...**` devient du gras ; la substitution passe après l'échappement."""
        rendu = _rendre(MailMessage.WELCOME, first_name="**<b>Awa</b>**")

        assert "<strong" in rendu.html_body
        assert "**" not in rendu.text_body
        assert "<b>Awa</b>" not in rendu.html_body
        assert "&lt;b&gt;Awa&lt;/b&gt;" in rendu.html_body


class TestGabaritDeMarque:
    """L'en-tête, le pied de page et la langue du document."""

    def test_l_en_tete_porte_le_logo_et_le_mot(self) -> None:
        """Les deux, parce qu'un client sur deux bloque les images.

        Un en-tête réduit à un rectangle vide est le premier signe d'un
        message qu'on classe en indésirable sans le lire. Le mot est du
        texte : il s'affiche toujours.
        """
        rendu = _rendre(MailMessage.WELCOME)

        assert theme.LOGO_URL in rendu.html_body
        assert f'alt="{layout.BRAND}"' in rendu.html_body
        assert rendu.html_body.count(layout.BRAND) >= 2

    def test_le_pied_porte_les_quatre_pages_contractuelles(self) -> None:
        """On les cherche le jour d'un litige, pas avant."""
        rendu = _rendre(MailMessage.WELCOME)

        for label, chemin in layout.LIENS_LEGAUX:
            assert label in rendu.html_body
            assert f"{theme.SITE_URL}{chemin}" in rendu.html_body

    def test_le_pied_ne_promet_pas_une_fiche_de_magasin_absente(self) -> None:
        """Un lien de téléchargement mort est pire que pas de lien.

        Tant que `APP_STORE_URL` vaut `None`, le pied annonce sans rien
        rendre cliquable.
        """
        rendu = _rendre(MailMessage.WELCOME)

        assert "App Store" in rendu.html_body
        if layout.APP_STORE_URL is None:
            assert "apps.apple.com" not in rendu.html_body

    def test_le_destinataire_est_rappele(self) -> None:
        """Ce qu'un hameçonnage ne sait pas faire : nommer l'adresse."""
        rendu = _rendre(MailMessage.WELCOME)

        assert "awa@exemple.fr" in rendu.html_body

    def test_la_langue_du_document_suit_celle_du_message(self) -> None:
        """Un lecteur d'écran prononce le message anglais en anglais."""
        anglais = _rendre(MailMessage.WELCOME, Language.EN)

        assert '<html lang="en"' in anglais.html_body


#: L'objet attendu de chaque message, en français, avec `CONTEXTE`.
#:
#: Écrit en toutes lettres plutôt que vérifié par un motif : c'est la
#: seule ligne que le destinataire lit avant d'ouvrir, et un objet
#: changé doit se voir en revue, pas passer parce qu'il « contient le
#: bon mot ».
OBJETS_FR: Final[dict[MailMessage, str]] = {
    MailMessage.LOGIN_CODE: "418293 — votre code de connexion Karria",
    MailMessage.EMAIL_VERIFICATION: "418293 — confirmez votre adresse Karria",
    MailMessage.WELCOME: "Bienvenue sur Karria, Awa",
    MailMessage.SMTP_TEST: "Test SMTP Karria",
}

#: Ce qui trahit une variable mal rendue, quelle que soit sa syntaxe.
_RESIDUS: Final = re.compile(r"\$\w|\$\{|\{\{|\bNone\b|\bundefined\b|\bnull\b")

#: Éléments HTML qui ne se referment pas.
_VIDES: Final = frozenset({"meta", "img", "br", "hr", "link", "input"})


class _Equilibre(HTMLParser):
    """Relève les balises ouvertes et jamais refermées, ou l'inverse."""

    def __init__(self) -> None:
        super().__init__()
        self.pile: list[str] = []
        self.erreurs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VIDES:
            self.pile.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.pile or self.pile[-1] != tag:
            self.erreurs.append(f"</{tag}> inattendue après {self.pile[-3:]}")
            return
        self.pile.pop()


class TestQualiteRedactionnelle:
    """Ce qu'un destinataire verrait d'un envoi bâclé.

    Chaque message est rendu comme il part, puis relu comme le lirait
    quelqu'un : objet, salutation, typographie, lien, signature, pied.
    """

    def test_chaque_message_a_un_objet_attendu(self) -> None:
        """La table couvre tout le registre : un message ajouté s'y inscrit."""
        assert set(OBJETS_FR) == set(MailMessage)

    @pytest.mark.parametrize("message", list(MailMessage))
    def test_l_objet_est_celui_prevu(self, message: MailMessage) -> None:
        assert _rendre(message).subject == OBJETS_FR[message]

    @pytest.mark.parametrize(("message", "langue"), TOUTES_COMBINAISONS)
    def test_aucun_residu_de_variable(self, message: MailMessage, langue: Language) -> None:
        """Ni `$x`, ni `{x}`, ni « None » : rien qui trahisse le gabarit."""
        rendu = _rendre(message, langue)

        for partie in (rendu.subject, rendu.html_body, rendu.text_body):
            assert not _RESIDUS.search(partie), _RESIDUS.search(partie)

    @pytest.mark.parametrize(("message", "langue"), TOUTES_COMBINAISONS)
    @pytest.mark.parametrize("prenom", ["", "   ", None])
    def test_un_prenom_vide_ne_laisse_pas_de_trace(
        self, message: MailMessage, langue: Language, prenom: str | None
    ) -> None:
        """« Bonjour , » ou « , votre CV » : le défaut classique.

        Un compte Karria n'a pas de prénom à l'inscription — seule
        l'adresse est collectée. Le message doit rester impeccable, pas
        seulement partir.
        """
        rendu = _rendre(message, langue, first_name=prenom)

        assert not re.search(r"\s,|,\s*(—|$)|^\s*,", rendu.subject), rendu.subject
        assert rendu.subject == rendu.subject.strip()
        assert "  " not in rendu.subject
        for partie in (rendu.html_body, rendu.text_body):
            assert "Bonjour ," not in partie
            assert "Hello ," not in partie
            assert "None" not in partie

    @pytest.mark.parametrize(
        "message",
        [
            m
            for m in MailMessage
            if "$greeting" in (TEMPLATES_DIR / "fr" / f"{m.value}.html").read_text("utf-8")
        ],
    )
    def test_la_salutation_sans_prenom_reste_propre(self, message: MailMessage) -> None:
        rendu = _rendre(message, first_name="")

        assert rendu.text_body.splitlines()[2] == "Bonjour,"

    def test_le_prenom_quand_il_existe_figure_dans_la_salutation(self) -> None:
        rendu = _rendre(MailMessage.WELCOME)

        assert "Awa" in rendu.subject
        assert "Awa" in rendu.text_body

    def test_l_objet_n_est_pas_echappe_comme_du_html(self) -> None:
        """Un objet est un en-tête : « O'Neil » ne doit pas y devenir `O&#x27;Neil`."""
        rendu = _rendre(MailMessage.WELCOME, first_name="Awa O'Neil")

        assert "&#x27;" not in rendu.subject
        assert "O\u2019Neil" in rendu.subject or "O'Neil" in rendu.subject
        # Dans le document, en revanche, il est échappé.
        assert "<title>Bienvenue sur Karria, Awa O" in rendu.html_body

    @pytest.mark.parametrize("message", list(MailMessage))
    def test_la_typographie_francaise_est_respectee(self, message: MailMessage) -> None:
        """Espace insécable avant « : ; ! ? », apostrophe courbe.

        Écrites à la main, les insécables sont oubliées une fois sur deux :
        « Motif » en fin de ligne, les deux-points seuls à la suivante.
        """
        rendu = _rendre(message)

        for partie in (rendu.subject, rendu.text_body):
            assert not re.search(r" [:;!?]", partie), partie
            assert not re.search(r"[A-Za-zÀ-ÿ]'[A-Za-zÀ-ÿ]", partie.split("-- \n")[0]), partie

    @pytest.mark.parametrize(("message", "langue"), TOUTES_COMBINAISONS)
    def test_le_html_est_bien_forme(self, message: MailMessage, langue: Language) -> None:
        analyse = _Equilibre()
        analyse.feed(_rendre(message, langue).html_body)
        analyse.close()

        assert analyse.erreurs == []
        assert analyse.pile == []

    @pytest.mark.parametrize(("message", "langue"), TOUTES_COMBINAISONS)
    def test_signature_et_pied_dans_les_deux_versions(
        self, message: MailMessage, langue: Language
    ) -> None:
        """Qui signe, pourquoi ce message, à qui, et comment nous joindre."""
        rendu = _rendre(message, langue)
        signature = "L\u2019équipe Karria" if langue is Language.FR else "The Karria team"

        for partie in (rendu.html_body, rendu.text_body):
            assert signature in partie
            assert theme.CONTACT_EMAIL in partie
            assert "awa@exemple.fr" in partie
        assert f"mailto:{theme.CONTACT_EMAIL}" in rendu.html_body

    def test_l_adresse_de_contact_vient_de_la_configuration(self) -> None:
        """Le pied de page cite la boîte réellement relevée (l'expéditeur),
        et non une constante qui divergerait un jour de la configuration."""
        rendu = render_for(
            OutgoingMail(
                to="awa@exemple.fr",
                message=MailMessage.WELCOME,
                context={"first_name": "Awa"},
            ),
            app_url="https://app.karria.fr",
            contact_email="aide@karria.fr",
        )
        assert "mailto:aide@karria.fr" in rendu.html_body
        assert "aide@karria.fr" in rendu.text_body
        assert theme.CONTACT_EMAIL not in rendu.text_body

    def test_le_pied_d_un_message_anglais_est_en_anglais(self) -> None:
        rendu = _rendre(MailMessage.WELCOME, Language.EN)

        assert "This message was sent to" in rendu.html_body
        assert "Ce message a" not in rendu.html_body

    @pytest.mark.parametrize(
        "message",
        [
            m
            for m in MailMessage
            if "[button:" in (TEMPLATES_DIR / "fr" / f"{m.value}.html").read_text("utf-8")
        ],
    )
    def test_une_seule_action_principale_et_son_adresse_en_texte(
        self, message: MailMessage
    ) -> None:
        """Un bouton, qui mène sur l'application — et que la version texte donne."""
        rendu = _rendre(message)
        boutons = re.findall(r'<a href="([^"]+)"\s+style="display:inline-block', rendu.html_body)

        assert len(boutons) == 1
        assert boutons[0].startswith("https://app.karria.fr")
        # Échappée dans l'attribut (`&amp;`), en clair dans le texte.
        assert html.unescape(boutons[0]) in rendu.text_body

    def test_une_liste_se_lit_en_texte(self) -> None:
        """Chaque puce occupe sa ligne, collée à son texte."""
        texte = _rendre(MailMessage.WELCOME).text_body

        assert "\n•\n" not in texte
        assert "- Dites à Karria" in texte

    @pytest.mark.parametrize(("message", "langue"), TOUTES_COMBINAISONS)
    def test_le_message_s_encode_en_utf8_sans_mojibake(
        self, message: MailMessage, langue: Language
    ) -> None:
        rendu = _rendre(message, langue)

        for partie in (rendu.subject, rendu.html_body, rendu.text_body):
            partie.encode("utf-8")
            assert "Ã" not in partie
            assert "�" not in partie


#: Valeur mise à la place du mot de passe dans les tests.
#:
#: Elle n'est pas nommée « secret » ni « password » : le linter refuse
#: toute affectation qui en a l'air, et c'est la règle qu'on veut — un
#: vrai mot de passe ne doit jamais pouvoir être écrit ici. Ce qu'on
#: cherche est une chaîne reconnaissable dans une trace, pour vérifier
#: qu'elle n'y apparaît pas.
_MARQUEUR_FACTICE = "valeur-factice-reconnaissable"


class TestClassificationDesEchecsSmtp:
    """Un échec SMTP n'est pas l'autre, et la différence décide de l'action."""

    @staticmethod
    def _reglages(marqueur: str = _MARQUEUR_FACTICE) -> MailSettings:
        return MailSettings(
            provider=MailProvider.SMTP,
            host="smtp.exemple.fr",
            port=587,
            username="contact@karria.fr",
            password=SecretStr(marqueur),
            from_address="contact@karria.fr",
        )

    @staticmethod
    def _envoi(destinataire: str = "awa@exemple.fr") -> OutgoingMail:
        return OutgoingMail(
            message=MailMessage.LOGIN_CODE,
            to=destinataire,
            context={"code": "418293", "expires_in": 10},
        )

    @pytest.mark.parametrize(
        ("panne", "attendue", "raison"),
        [
            (
                smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed"),
                MailAuthenticationError,
                "mail_auth_failed",
            ),
            (
                smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"No such user")}),
                MailRecipientRefusedError,
                "mail_recipient_refused",
            ),
            (TimeoutError("timed out"), MailServerUnavailableError, "mail_server_unavailable"),
            (
                smtplib.SMTPConnectError(421, b"Service not available"),
                MailServerUnavailableError,
                "mail_server_unavailable",
            ),
            (
                smtplib.SMTPServerDisconnected("connection lost"),
                MailServerUnavailableError,
                "mail_server_unavailable",
            ),
            (
                ConnectionRefusedError("connection refused"),
                MailServerUnavailableError,
                "mail_server_unavailable",
            ),
            (
                ssl.SSLError("certificate verify failed"),
                MailServerUnavailableError,
                "mail_server_unavailable",
            ),
            (
                smtplib.SMTPDataError(550, b"Daily user sending limit exceeded"),
                MailQuotaExhaustedError,
                "mail_quota_exhausted",
            ),
        ],
    )
    async def test_chaque_panne_a_sa_categorie(
        self,
        monkeypatch: pytest.MonkeyPatch,
        panne: Exception,
        attendue: type[MailDeliveryError],
        raison: str,
    ) -> None:
        """La doublure lève, l'adaptateur classe.

        Aucun octet ne part sur le réseau : `smtplib.SMTP` est remplacé
        avant l'appel. Un test d'envoi qui envoie vraiment est un test
        qu'on finit par désactiver.
        """

        def refuser(*_args: object, **_kwargs: object) -> None:
            raise panne

        monkeypatch.setattr(smtplib, "SMTP", refuser)

        with pytest.raises(attendue) as capture:
            await SmtpMailer(self._reglages()).send(self._envoi())

        assert capture.value.details["reason"] == raison

    async def test_le_mot_de_passe_ne_fuit_dans_aucun_echec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un mot de passe dans une trace finit dans un ticket, puis ailleurs."""

        def refuser(*_args: object, **_kwargs: object) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")

        monkeypatch.setattr(smtplib, "SMTP", refuser)

        with pytest.raises(MailAuthenticationError) as capture:
            await SmtpMailer(self._reglages()).send(self._envoi())

        erreur = capture.value
        assert _MARQUEUR_FACTICE not in str(erreur)
        assert _MARQUEUR_FACTICE not in str(erreur.details)
        assert _MARQUEUR_FACTICE not in (erreur.technical_detail or "")

    async def test_l_expediteur_et_le_chiffrement_sont_ceux_attendus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`From:` porte le compte authentifié, et TLS n'est pas affaibli."""
        captures: list[EmailMessage] = []
        contextes: list[object] = []

        class ServeurDouble:
            def __enter__(self) -> ServeurDouble:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def starttls(self, context: object = None) -> None:
                contextes.append(context)

            def login(self, *_: object) -> None:
                return None

            def send_message(self, message: EmailMessage) -> None:
                captures.append(message)

        monkeypatch.setattr(smtplib, "SMTP", lambda *_a, **_k: ServeurDouble())

        # Une adresse de relais Apple, précisément : elle doit être traitée
        # comme n'importe quelle autre, sans filtrage ni réécriture.
        await SmtpMailer(self._reglages()).send(self._envoi("awa@privaterelay.appleid.com"))

        assert captures[0]["From"] == "Karria <contact@karria.fr>"
        assert captures[0]["To"] == "awa@privaterelay.appleid.com"
        assert "@karria.fr>" in captures[0]["Message-ID"]

        # Le contexte TLS est fourni, et il vérifie le certificat.
        contexte = contextes[0]
        assert isinstance(contexte, ssl.SSLContext)
        assert contexte.check_hostname is True
        assert contexte.verify_mode is ssl.CERT_REQUIRED


class TestLienVersLApplication:
    """Le lien qui ouvre l'application, dérivé du lien web."""

    def test_il_reprend_le_chemin_du_lien_web(self) -> None:
        """Deux adresses pour un même écran, dérivées l'une de l'autre."""
        assert _deeplink("https://karria.fr/offres/9f2b1c40") == "karria://offres/9f2b1c40"

    def test_sans_lien_precis_il_ouvre_l_application_tout_court(self) -> None:
        """Mieux vaut l'accueil de l'application que rien du tout."""
        assert _deeplink(None) == "karria://"
        assert _deeplink("pas une adresse") == "karria://"

    def test_il_ignore_le_domaine_du_lien_web(self) -> None:
        """Seul le chemin compte : l'application n'a pas de domaine."""
        assert _deeplink("https://preprod.karria.fr/offres/42") == "karria://offres/42"


class TestAdaptateurConsole:
    """L'adaptateur de développement rend réellement le message."""

    async def test_il_conserve_ce_qu_il_a_rendu(self) -> None:
        """Les tests de bout en bout lisent le code ici, sans boîte mail."""
        mailer = ConsoleMailer()

        await mailer.send(
            OutgoingMail(message=MailMessage.LOGIN_CODE, to="awa@exemple.fr", context=CONTEXTE)
        )

        assert len(mailer.sent) == 1
        envoi, texte = mailer.sent[0]
        assert envoi.to == "awa@exemple.fr"
        assert "418293" in texte

    async def test_il_est_toujours_disponible(self) -> None:
        """Il n'y a rien à joindre : le diagnostic ne doit pas échouer."""
        await ConsoleMailer().check_health()


class TestFabrique:
    """Le choix du transport se lit dans la configuration, nulle part ailleurs."""

    def test_console_par_defaut(self) -> None:
        assert isinstance(build_mailer(MailSettings()), ConsoleMailer)

    def test_smtp_quand_il_est_demande(self) -> None:
        settings = MailSettings(
            provider=MailProvider.SMTP,
            host="smtp.exemple.fr",
            port=587,
            username="envoi@exemple.fr",
            password=SecretStr("secret"),
            from_address="envoi@exemple.fr",
        )

        assert isinstance(build_mailer(settings), SmtpMailer)

    def test_smtp_incomplet_est_refuse_a_la_configuration(self) -> None:
        """Une configuration à moitié faite échouerait au premier code."""
        with pytest.raises(ValueError, match="MAIL__"):
            MailSettings(provider=MailProvider.SMTP)


class TestLangues:
    """Ce qui garantit qu'un anglophone lit vraiment de l'anglais."""

    def test_l_objet_suit_la_langue(self) -> None:
        assert "connexion" in _rendre(MailMessage.LOGIN_CODE, Language.FR).subject
        assert "sign-in" in _rendre(MailMessage.LOGIN_CODE, Language.EN).subject

    def test_le_corps_suit_la_langue(self) -> None:
        assert "Bonjour" in _rendre(MailMessage.LOGIN_CODE, Language.FR).text_body
        assert "Hello" in _rendre(MailMessage.LOGIN_CODE, Language.EN).text_body

    def test_le_code_est_le_meme_dans_les_deux(self) -> None:
        """La traduction porte sur les mots, jamais sur les données."""
        for langue in Language:
            assert "418293" in _rendre(MailMessage.LOGIN_CODE, langue).text_body

    def test_une_langue_inconnue_retombe_sur_le_francais(self) -> None:
        """Une préférence exotique ne doit jamais empêcher un code d'arriver."""
        assert resolve("wo") is Language.FR
        assert resolve(None) is Language.FR
        assert resolve("EN-gb") is Language.EN
        assert resolve("fr_FR") is Language.FR

    def test_la_langue_du_navigateur_est_comprise(self) -> None:
        assert from_accept_language("en-GB,en;q=0.9,fr;q=0.8") is Language.EN
        assert from_accept_language("fr-CA,fr;q=0.9") is Language.FR
        # Une langue qu'on n'écrit pas ne fait pas échouer : on retombe
        # sur le français plutôt que de refuser d'envoyer.
        assert from_accept_language("ja,ko;q=0.9") is Language.FR
        assert from_accept_language(None) is Language.FR
        assert from_accept_language("*") is Language.FR


class TestQuotaEpuise:
    """Un quota n'est pas une panne, et ne se traite pas pareil."""

    @pytest.mark.parametrize(
        "message",
        [
            "(550, b'5.4.5 Daily user sending limit exceeded.')",
            "Quota exceeded for this account",
            "421 Too many messages, slow down",
            "Message rate exceeded",
        ],
    )
    def test_reconnait_un_refus_pour_quota(self, message: str) -> None:
        """Le code SMTP seul ne suffit pas : c'est le texte qui sépare."""
        assert _is_quota_exhausted(Exception(message))

    @pytest.mark.parametrize(
        "message",
        [
            "Connection refused",
            "(550, b'No such user here')",
            "timed out",
        ],
    )
    def test_ne_confond_pas_une_panne_avec_un_quota(self, message: str) -> None:
        assert not _is_quota_exhausted(Exception(message))

    def test_lerreur_de_quota_porte_sa_raison(self) -> None:
        """L'application s'en sert pour ne pas proposer « réessayez »."""
        assert MailQuotaExhaustedError().details["reason"] == "mail_quota_exhausted"

    def test_elle_reste_une_erreur_de_livraison(self) -> None:
        """Les appelants qui rattrapent l'une doivent rattraper l'autre."""
        assert isinstance(MailQuotaExhaustedError(), MailDeliveryError)
