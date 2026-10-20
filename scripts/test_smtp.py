"""Envoie un vrai message par le serveur SMTP configuré.

    uv run python scripts/test_smtp.py <destinataire>
    SMTP_TEST_TO=vous@exemple.fr uv run python scripts/test_smtp.py

Pourquoi ce script existe
--------------------------
Les tests unitaires doublent le serveur SMTP : ils vérifient que le
message est bien composé et que chaque échec est bien classé, mais ils ne
prouvent rien de la configuration réelle. Un mot de passe expiré, un port
fermé par un pare-feu, un `From:` refusé par le fournisseur : rien de tout
cela n'apparaît avant le premier envoi véritable — c'est-à-dire, sans ce
script, avant le premier code de connexion d'un utilisateur.

Il utilise **exactement** le chemin de production : `build_mailer` choisit
l'adaptateur depuis la configuration, `SmtpMailer` compose et remet le
message. Écrire un envoi ad hoc avec `smtplib` testerait un code que
personne n'exécute jamais.

Pourquoi pas une route HTTP
----------------------------
Une route « envoyer un e-mail de test » est une route qui envoie des
e-mails à la demande. Sans authentification c'est un relais ouvert ; avec
authentification c'est une permission de plus à définir, à tester et à
révoquer — pour un geste qu'on fait trois fois par an, depuis une machine
qui a déjà accès à la configuration.

Ce qu'il ne fait pas
---------------------
Il ne fabrique aucun secret. Si `MAIL__PASSWORD` est absent, il le dit et
s'arrête : un mot de passe inventé produirait un échec d'authentification
qu'on mettrait dix minutes à distinguer d'un vrai.
"""

from __future__ import annotations

import asyncio
import os
import sys

from karria.shared.config import MailProvider, get_settings
from karria.shared.mail import MailMessage, OutgoingMail, build_mailer
from karria.shared.mail.rendering import render_for

#: Ce que le message doit dire, et rien de plus.
SUJET = "Test SMTP Karria"
CORPS = "La configuration SMTP de Karria fonctionne correctement."


def _destinataire() -> str:
    """L'adresse visée, en argument ou par l'environnement."""
    if len(sys.argv) > 1:
        return sys.argv[1].strip()
    depuis_env = os.environ.get("SMTP_TEST_TO", "").strip()
    if depuis_env:
        return depuis_env
    print(
        "Indiquez le destinataire :\n"
        "  uv run python scripts/test_smtp.py vous@exemple.fr\n"
        "  SMTP_TEST_TO=vous@exemple.fr uv run python scripts/test_smtp.py",
        file=sys.stderr,
    )
    raise SystemExit(2)


async def main() -> None:
    destinataire = _destinataire()
    settings = get_settings()
    mail = settings.mail

    if mail.provider is not MailProvider.SMTP:
        print(
            f"MAIL__PROVIDER vaut « {mail.provider.value} » : rien ne partira sur le réseau.\n"
            "Posez MAIL__PROVIDER=smtp pour un envoi réel.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not mail.password.get_secret_value():
        # Aucun secret n'est fabriqué : voir l'en-tête du module.
        print(
            "MAIL__PASSWORD est absent de l'environnement.\n"
            "Posez-le dans le `.env` local, ou exportez-le le temps de la commande.\n"
            "Aucun envoi n'a été tenté.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"Serveur      : {mail.host}:{mail.port} (STARTTLS, certificat vérifié)")
    print(f"Compte       : {mail.username}")
    print(f"Expéditeur   : {mail.from_name} <{mail.from_address}>")
    print(f"Destinataire : {destinataire}")
    print("Mot de passe : présent (jamais affiché)")
    print()

    envoi = OutgoingMail(
        message=MailMessage.SMTP_TEST,
        to=destinataire,
        context={"subject": SUJET, "body": CORPS},
    )

    # Rendu d'abord : une erreur de gabarit doit se voir avant qu'on
    # ouvre une connexion, pas au milieu d'un échange SMTP.
    rendu = render_for(envoi, app_url=mail.app_url)
    print(f"Objet rendu  : {rendu.subject}")

    await build_mailer(mail).send(envoi)
    print("\n✅ Message remis au serveur.")
    print(
        "   Le serveur l'a accepté — cela ne garantit pas la réception.\n"
        "   Vérifiez la boîte, y compris les indésirables."
    )


if __name__ == "__main__":
    asyncio.run(main())
