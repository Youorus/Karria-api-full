"""Le courriel de remerciement d'un paiement, par le centre d'e-mails du socle (ADR-0024 § 2026-11-05).

Implémentation du port `ThankYouMailer`. Elle choisit le message du registre
selon le produit (`PURCHASE_COFFEE`, `PURCHASE_TEMPLATE`,
`SUBSCRIPTION_STARTED`) et ne remet au `Mailer` que des valeurs produites
par Karria : le montant formaté, le nombre de cafés, le nom du modèle tel
que `documents` le vend. Aucun texte ni lien venu de la personne n'entre
dans le message ; les objets sont fixes (registre) et le rendu échappe tout
le contexte de toute façon.
"""

from __future__ import annotations

from typing import Final

from karria.modules.payments.domain import ProductKind, ThankYou
from karria.shared.i18n import Language
from karria.shared.mail import Mailer, MailMessage, OutgoingMail

#: Quel message pour quel produit.
_MESSAGE: Final[dict[ProductKind, MailMessage]] = {
    ProductKind.COFFEE: MailMessage.PURCHASE_COFFEE,
    ProductKind.TEMPLATE: MailMessage.PURCHASE_TEMPLATE,
    ProductKind.AD_FREE: MailMessage.SUBSCRIPTION_STARTED,
}

#: Ce qu'on dit d'un modèle dont le nom n'a pas pu être relu.
_TEMPLATE_FALLBACK_LABEL: Final = "Premium"


class MailThankYouMailer:
    """Envoie « Merci pour le café », « Votre modèle est à vous » ou « Karria sans pub est actif »."""

    def __init__(self, mailer: Mailer, *, language: Language = Language.FR) -> None:
        self._mailer = mailer
        self._language = language

    async def send(self, *, to: str, thanks: ThankYou) -> None:
        """Achemine ; les erreurs du centre d'e-mails remontent telles quelles (`MailDeliveryError`)."""
        await self._mailer.send(
            OutgoingMail(
                message=_MESSAGE[thanks.kind],
                to=to,
                context={
                    "amount": thanks.amount,
                    "coffee_label": thanks.coffee_label,
                    "template_label": thanks.template_label or _TEMPLATE_FALLBACK_LABEL,
                },
                language=self._language,
            )
        )
