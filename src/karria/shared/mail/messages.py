"""Registre des e-mails que l'application sait envoyer.

    ┌────────────────────────────────────────────────────────────────┐
    │  AJOUTER UN E-MAIL = UNE VALEUR ICI, UNE ENTRÉE AU REGISTRE,   │
    │  UN FICHIER DANS `templates/`. RIEN D'AUTRE.                   │
    └────────────────────────────────────────────────────────────────┘

Même dispositif que le registre de buckets (`shared/storage/buckets.py`),
et pour la même raison : ce qui se déclare **en données** ne se disperse
pas dans le code. Nul besoin de toucher au module d'envoi, à la
configuration, ni au câblage.

Ce que le registre porte, et pourquoi
-------------------------------------
L'objet du message vit ici et non dans le gabarit : c'est la seule ligne
que le destinataire lit avant d'ouvrir, elle mérite d'être relue au même
endroit que les autres. La **catégorie** décide de la mise en page — un
code de sécurité n'a pas la même urgence qu'une notification.

Ce que le registre ne porte pas
-------------------------------
Aucun contenu personnalisé : ni prénom, ni code, ni lien. Ces valeurs
arrivent à l'envoi. Les inscrire ici les figerait dans un fichier lu au
démarrage, donc partagé par tous les destinataires.

Ce qu'il contient aujourd'hui
-----------------------------
Le strict socle : les deux codes à six chiffres, la bienvenue et le
message de vérification SMTP. Les messages métier — une offre analysée,
un CV prêt, un dossier à relancer — arrivent avec le module qui les
émet, dans le même commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from karria.shared.i18n import Language


class MailCategory(StrEnum):
    """Nature d'un message, qui décide de sa mise en page.

    Distinguée du sujet parce qu'elle est **structurelle** : un code de
    sécurité s'affiche en gros, centré, avec sa durée de validité ; une
    notification se lit comme une phrase. Traiter les deux pareil produit
    soit des codes illisibles, soit des notifications criardes.
    """

    #: Un code à recopier. Urgent, court, sans lien sur lequel cliquer.
    SECURITE = "securite"
    #: Un fait qui vient de se produire. Informatif.
    NOTIFICATION = "notification"
    #: Une action attendue de la personne. Porte un bouton.
    ACTION = "action"


@dataclass(frozen=True, slots=True)
class MailTemplate:
    """Définition d'un e-mail.

    Attributes:
        subject: Objet du message, **par langue**. Peut contenir des
            variables `$nom`, substituées à l'envoi — un objet qui porte
            le code lui-même permet de le lire depuis la liste, sans
            ouvrir.

            Le prénom ne s'y écrit jamais tel quel : `$name_suffix`
            (« , Awa » ou rien) le remplace, pour qu'un prénom absent ne
            laisse pas une virgule orpheline. Voir
            `rendering._personnaliser`.

            Une entrée par langue de `Language`, sans exception : un test
            le vérifie, et une langue oubliée ferait échouer un envoi au
            lieu de retomber silencieusement sur le français. Personne ne
            doit découvrir qu'une traduction manque en lisant un courriel
            à moitié anglais.
        category: Décide de la mise en page.
        template: Nom du gabarit, sans extension. Le fichier est cherché
            dans `templates/<langue>/`.
        preview: Texte d'aperçu, affiché par les clients à côté de l'objet.
            Souvent négligé — et le client affiche alors les premiers mots
            du HTML, ce qui donne « Voir ce message dans un navigateur ».
        footer_note: Pourquoi ce message-ci arrive, en une phrase, au pied.

            C'est la première question de quelqu'un qui ne se souvient pas
            de nous avoir donné son adresse, et la réponse décide s'il
            signale le message comme indésirable — auquel cas c'est la
            délivrabilité de tous les envois suivants qui se dégrade.

            À défaut, une mention générique parle d'« une action sur votre
            compte ». Un message adressé à quelqu'un qui n'a pas encore de
            compte doit renseigner la sienne.
    """

    subject: dict[Language, str]
    template: str
    category: MailCategory
    preview: dict[Language, str] = field(default_factory=dict)
    footer_note: dict[Language, str] = field(default_factory=dict)


class MailMessage(StrEnum):
    """E-mails de l'application."""

    #: Code de connexion : la seule porte d'entrée de Karria. L'inscription
    #: ne collecte que l'adresse, et c'est ce code qui la prouve.
    LOGIN_CODE = "login_code"
    #: Code de vérification d'une adresse, hors connexion — au changement
    #: d'adresse d'un compte existant.
    EMAIL_VERIFICATION = "email_verification"
    #: Premier message après création du compte.
    WELCOME = "welcome"
    #: Message de vérification de la configuration SMTP.
    #:
    #: Il n'est envoyé que par `scripts/test_smtp.py`, jamais par
    #: l'application. Il existe pour que le test manuel emprunte le
    #: chemin réel — rendu, composition MIME, remise — plutôt qu'un
    #: `smtplib.sendmail` écrit pour l'occasion, qui ne prouverait rien
    #: du code que les utilisateurs déclenchent.
    SMTP_TEST = "smtp_test"
    #: Une personne vient d'écrire à l'équipe dans « Parler à l'équipe »
    #: (module `support`). Adressé à l'équipe, jamais à la personne.
    SUPPORT_NEW_MESSAGE = "support_new_message"


#: Le registre. Une entrée par message, et rien de plus à toucher ailleurs.
REGISTRY: Final[dict[MailMessage, MailTemplate]] = {
    MailMessage.LOGIN_CODE: MailTemplate(
        # Le code figure dans l'objet : sur mobile, il se lit dans la liste
        # des messages, sans ouvrir — un aller-retour de moins au moment où
        # la personne attend le plus.
        subject={
            Language.FR: "$code — votre code de connexion Karria",
            Language.EN: "$code — your Karria sign-in code",
        },
        template="login_code",
        category=MailCategory.SECURITE,
        preview={
            Language.FR: "Ce code expire dans $expires_in minutes.",
            Language.EN: "This code expires in $expires_in minutes.",
        },
    ),
    MailMessage.EMAIL_VERIFICATION: MailTemplate(
        subject={
            Language.FR: "$code — confirmez votre adresse Karria",
            Language.EN: "$code — confirm your Karria address",
        },
        template="email_verification",
        category=MailCategory.SECURITE,
        preview={
            Language.FR: "Ce code expire dans $expires_in minutes.",
            Language.EN: "This code expires in $expires_in minutes.",
        },
    ),
    MailMessage.WELCOME: MailTemplate(
        subject={
            Language.FR: "Bienvenue sur Karria$name_suffix",
            Language.EN: "Welcome to Karria$name_suffix",
        },
        template="welcome",
        category=MailCategory.ACTION,
        preview={
            Language.FR: "Votre compte est prêt. Voici comment démarrer.",
            Language.EN: "Your account is ready. Here is how to start.",
        },
    ),
    MailMessage.SUPPORT_NEW_MESSAGE: MailTemplate(
        subject={
            Language.FR: "Karria — un mot de $user_email",
            Language.EN: "Karria — a message from $user_email",
        },
        template="support_new_message",
        category=MailCategory.ACTION,
        preview={
            Language.FR: "$preview",
            Language.EN: "$preview",
        },
        footer_note={
            Language.FR: (
                "Vous recevez ce message parce que vous êtes de garde pour « Parler à "
                "l'équipe » (SUPPORT__TEAM_EMAIL)."
            ),
            Language.EN: (
                "You receive this message because you are on duty for « Talk to the "
                "team » (SUPPORT__TEAM_EMAIL)."
            ),
        },
    ),
    MailMessage.SMTP_TEST: MailTemplate(
        subject={Language.FR: "$subject", Language.EN: "$subject"},
        template="smtp_test",
        category=MailCategory.ACTION,
        preview={
            Language.FR: "Vérification de la configuration d'envoi.",
            Language.EN: "Delivery configuration check.",
        },
        footer_note={
            Language.FR: (
                "Ce message a été déclenché à la main par "
                "<code>scripts/test_smtp.py</code>. Il ne part jamais tout seul."
            ),
            Language.EN: (
                "This message was triggered by hand from "
                "<code>scripts/test_smtp.py</code>. It never goes out on its own."
            ),
        },
    ),
}
