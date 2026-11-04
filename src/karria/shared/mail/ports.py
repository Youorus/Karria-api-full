"""Le port d'envoi : ce dont l'application a besoin, sans dire par qui.

Décrit un **besoin** — « acheminer ce message à cette adresse » — et non
une technologie. C'est ce qui permet de passer de Gmail à un service
transactionnel sans qu'aucun module métier ne change, et d'exercer tout
le parcours de connexion sans jamais envoyer un courriel réel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from karria.shared.i18n import DEFAULT_LANGUAGE, Language
from karria.shared.mail.messages import MailMessage


@dataclass(frozen=True, slots=True)
class Attachment:
    """Une pièce jointe : un fichier produit par Karria (CV, lettre), jamais reçu d'un tiers.

    Le nom est celui que le destinataire verra ; il est nettoyé par le
    transport (pas de séparateur de chemin, pas de retour à la ligne).
    """

    file_name: str
    content_type: str
    data: bytes


#: Taille totale des pièces jointes d'un message : au-delà, beaucoup de
#: serveurs receveurs refusent (10 Mo est le plancher le plus courant).
MAX_ATTACHMENTS_BYTES: int = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    """Un message prêt à partir.

    Attributes:
        to: Adresse du destinataire. Un seul : un envoi transactionnel
            s'adresse à une personne, et grouper des destinataires les
            exposerait les uns aux autres.
        context: Valeurs substituées dans l'objet et le gabarit.
        reply_to: Adresse de réponse, quand elle diffère de l'expéditeur.
        attachments: Pièces jointes (ADR-0031). Des fichiers que Karria a
            produits elle-même ; leur taille totale est bornée.
    """

    message: MailMessage
    to: str
    context: dict[str, Any] = field(default_factory=dict)
    reply_to: str | None = None
    attachments: tuple[Attachment, ...] = ()

    #: Langue de rédaction. Décidée par l'appelant — qui seul sait s'il
    #: s'adresse à un compte dont la préférence est connue, ou à une
    #: adresse dont on ne sait rien.
    language: Language = DEFAULT_LANGUAGE


class Mailer(Protocol):
    """Port : achemine un e-mail.

    Asynchrone parce qu'un envoi SMTP est un aller-retour réseau de
    plusieurs centaines de millisecondes. Exécuté dans la boucle
    d'événements, il bloquerait **toutes** les requêtes du processus
    pendant sa durée.
    """

    async def send(self, mail: OutgoingMail) -> None:
        """Achemine le message.

        Raises:
            MailDeliveryError: Le serveur n'a pas accepté le message.
        """
        ...

    async def check_health(self) -> None:
        """Vérifie que le service d'envoi répond.

        Sert la sonde d'exploitation : une panne SMTP rend la connexion
        impossible, et il vaut mieux l'apprendre d'un diagnostic que d'un
        utilisateur bloqué.

        Raises:
            MailDeliveryError: Le service est injoignable.
        """
        ...
