"""Adaptateur SMTP — l'envoi réel.

Pourquoi `smtplib` et non une bibliothèque asynchrone
------------------------------------------------------
`aiosmtplib` serait le choix naturel dans une application asynchrone. Elle
n'est pas indispensable : la bibliothèque standard sait parler SMTP, et
l'appel se déporte hors de la boucle d'événements par `asyncio.to_thread`
— exactement le procédé déjà employé pour Argon2, dont le coût est du même
ordre.

Une dépendance de moins à suivre, à mettre à jour, et à auditer
(AGENTS.md §6.11).

Ce qui distingue un envoi qui arrive d'un envoi qui part
---------------------------------------------------------
Trois détails décident souvent du dossier de courrier indésirable :

- une **version texte** à côté du HTML — un message qui n'en a pas est
  suspect pour la plupart des filtres ;
- un **nom d'expéditeur** lisible plutôt qu'une adresse nue ;
- un en-tête **`Auto-Submitted`**, qui dit qu'aucun humain n'attend de
  réponse et évite les réponses automatiques en boucle.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import TYPE_CHECKING, Final

from karria.shared.logging import get_logger
from karria.shared.mail.errors import (
    MailAuthenticationError,
    MailDeliveryError,
    MailQuotaExhaustedError,
    MailRecipientRefusedError,
    MailServerUnavailableError,
)
from karria.shared.mail.ports import OutgoingMail
from karria.shared.mail.rendering import render_for

if TYPE_CHECKING:
    from karria.shared.config import MailSettings

logger = get_logger(__name__)

#: Délai au-delà duquel on renonce. Un serveur SMTP qui ne répond pas en
#: quinze secondes ne répondra pas : mieux vaut rendre la main et laisser
#: la personne réessayer que tenir une requête HTTP ouverte.
_TIMEOUT_SECONDS = 15

#: Le contexte TLS, construit une fois.
#:
#: `starttls()` sans argument en construit déjà un équivalent : la
#: bibliothèque standard vérifie le certificat et le nom d'hôte depuis
#: Python 3.6. Il est écrit explicitement ici pour une raison
#: d'auditabilité, pas de comportement — la question « est-ce que la
#: vérification est active ? » doit se répondre en lisant cette ligne,
#: sans avoir à connaître les valeurs par défaut de `smtplib`.
#:
#: Ce qu'il ne faut jamais faire, et qu'on écrit pour que ce soit
#: refusé en revue : `check_hostname = False`, `verify_mode =
#: CERT_NONE`. Un canal chiffré sans certificat vérifié protège de
#: l'écoute passive, pas de l'interception — et c'est justement
#: l'interception qui livrerait le mot de passe du compte d'envoi.
_TLS_CONTEXT: Final = ssl.create_default_context()


class SmtpMailer:
    """Envoie par un serveur SMTP."""

    def __init__(self, settings: MailSettings) -> None:
        self._settings = settings

    async def send(self, mail: OutgoingMail) -> None:
        """Rend le message et le remet au serveur.

        Raises:
            MailDeliveryError: Le serveur n'a pas accepté le message.
        """
        # L'adresse de contact du pied de page est l'expéditeur : c'est la
        # boîte où « répondre à ce message » arrive réellement.
        rendered = render_for(
            mail,
            app_url=self._settings.app_url,
            contact_email=self._settings.from_address or self._settings.username,
        )
        message = self._build(mail, rendered.subject, rendered.html_body, rendered.text_body)

        # Hors de la boucle d'événements : un aller-retour SMTP dure des
        # centaines de millisecondes, pendant lesquelles toutes les autres
        # requêtes du processus attendraient.
        await asyncio.to_thread(self._deliver, message)

        logger.info(
            "mail.sent",
            message=mail.message.value,
            # L'adresse n'est jamais journalisée en clair : un journal finit
            # dans un agrégateur, un ticket, une capture d'écran.
            recipient=_mask(mail.to),
        )

    async def check_health(self) -> None:
        """Vérifie que le serveur répond et accepte l'authentification."""
        await asyncio.to_thread(self._probe)

    def _build(
        self, mail: OutgoingMail, subject: str, html_body: str, text_body: str
    ) -> EmailMessage:
        """Compose le message MIME."""
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self._settings.from_name, self._settings.from_address))
        message["To"] = mail.to
        message["Message-ID"] = make_msgid(domain=self._settings.message_id_domain)
        # Dit aux serveurs receveurs qu'aucun humain n'attend de réponse :
        # évite les réponses d'absence, et les boucles qu'elles provoquent.
        message["Auto-Submitted"] = "auto-generated"
        if mail.reply_to or self._settings.reply_to:
            message["Reply-To"] = mail.reply_to or self._settings.reply_to

        # L'ordre compte : le texte d'abord, le HTML ensuite. Un client
        # affiche la **dernière** partie qu'il sait rendre.
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        return message

    def _deliver(self, message: EmailMessage) -> None:
        """Remet le message. Exécuté hors de la boucle d'événements."""
        try:
            with smtplib.SMTP(
                self._settings.host, self._settings.port, timeout=_TIMEOUT_SECONDS
            ) as server:
                server.starttls(context=_TLS_CONTEXT)
                server.login(self._settings.username, self._settings.password.get_secret_value())
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise _classify(exc) from exc

    def _probe(self) -> None:
        """Ouvre une connexion et s'authentifie, sans rien envoyer."""
        try:
            with smtplib.SMTP(
                self._settings.host, self._settings.port, timeout=_TIMEOUT_SECONDS
            ) as server:
                server.starttls(context=_TLS_CONTEXT)
                server.login(self._settings.username, self._settings.password.get_secret_value())
        except (smtplib.SMTPException, OSError) as exc:
            raise _classify(exc) from exc


def _mask(address: str) -> str:
    """Masque une adresse pour les journaux, sans la rendre méconnaissable."""
    local, _, domain = address.partition("@")
    if not domain:
        return "…"
    return f"{local[:1]}{'•' * max(len(local) - 1, 1)}@{domain}"


#: Ce que disent les fournisseurs quand la limite du jour est atteinte.
#: Le code SMTP seul ne suffit pas — 550 couvre aussi bien un
#: destinataire inconnu qu'un quota — d'où la lecture du texte.
_QUOTA_MARKERS: Final = (
    "daily user sending limit",
    "quota exceeded",
    "message rate exceeded",
    "too many messages",
)


def _is_quota_exhausted(exc: Exception) -> bool:
    """Le refus vient-il d'un quota plutôt que d'une panne ?

    La distinction n'est pas cosmétique : elle décide de ce qu'on
    propose à la personne — patienter et réessayer, ou recevoir son code
    par un autre canal.
    """
    message = str(exc).lower()
    return any(marqueur in message for marqueur in _QUOTA_MARKERS)


def _classify(exc: Exception) -> MailDeliveryError:
    """Range un échec SMTP dans la bonne catégorie, et le journalise.

    ═══ Pourquoi distinguer, alors que tout finit en 503 ═══

    Le client reçoit le même message dans tous les cas — il n'a pas à
    apprendre que notre compte d'envoi est mal configuré. La distinction
    sert à **l'exploitation**, et elle décide de ce qu'on fait :

    - une authentification refusée ne se résout jamais en réessayant, et
      insister fait bloquer le compte par le fournisseur ;
    - un destinataire refusé ne se résout pas davantage : l'adresse est
      morte, c'est le cas d'un relais Apple dont l'accès a été révoqué ;
    - un quota se libère au lendemain ;
    - une panne ou un délai dépassé, eux, se réessaient.

    Sans cette distinction, les quatre apparaissent au journal sous le
    même « mail.delivery_failed », et l'on cherche une panne réseau là où
    il manquait une variable d'environnement.

    ═══ Ce que le journal ne contient jamais ═══

    Le mot de passe n'apparaît nulle part : ni dans `str(exc)` — les
    serveurs SMTP ne le renvoient pas — ni dans les champs journalisés,
    qui sont choisis un par un. `detail` porte le message du serveur, qui
    peut nommer l'hôte et le compte ; c'est la raison pour laquelle il
    reste au journal et ne part pas dans la réponse HTTP.
    """
    detail = str(exc)
    raison: str
    erreur: MailDeliveryError

    if isinstance(exc, smtplib.SMTPAuthenticationError):
        raison, erreur = "authentification refusée", MailAuthenticationError(detail)
    elif isinstance(exc, smtplib.SMTPRecipientsRefused | smtplib.SMTPNotSupportedError):
        raison, erreur = "destinataire refusé", MailRecipientRefusedError(detail)
    elif _is_quota_exhausted(exc):
        raison, erreur = "quota épuisé", MailQuotaExhaustedError(detail)
    elif isinstance(exc, TimeoutError):
        # `socket.timeout` est un alias de `TimeoutError` depuis 3.10.
        raison, erreur = "délai dépassé", MailServerUnavailableError(detail)
    elif isinstance(
        exc, smtplib.SMTPConnectError | smtplib.SMTPServerDisconnected | ConnectionError
    ):
        raison, erreur = "serveur injoignable", MailServerUnavailableError(detail)
    elif isinstance(exc, ssl.SSLError):
        # Un échec TLS n'est pas une panne ordinaire : il signale un
        # certificat invalide, donc potentiellement une interception.
        raison, erreur = "échec TLS", MailServerUnavailableError(detail)
    elif isinstance(exc, OSError):
        raison, erreur = "erreur réseau", MailServerUnavailableError(detail)
    else:
        raison, erreur = "refus du serveur", MailDeliveryError(detail)

    logger.error(
        "mail.delivery_failed",
        reason=erreur.details.get("reason", "mail_delivery_failed"),
        cause=raison,
        error=type(exc).__name__,
        detail=detail,
    )
    return erreur
