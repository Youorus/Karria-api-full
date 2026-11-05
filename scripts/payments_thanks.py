"""Rejoue le merci d'un achat enregistré : courriel et notification, une seule fois.

    uv run python -m scripts.payments_thanks <purchase_id>          # dit ce qui partirait
    uv run python -m scripts.payments_thanks <purchase_id> --send   # envoie

Pourquoi ce script existe
--------------------------
Le merci d'un paiement part depuis le webhook, **après** l'enregistrement
de l'achat (ADR-0024 § 2026-11-05). Un achat enregistré avant que cette
fonction existe — ou pendant une panne SMTP ou Expo, que le webhook absorbe
sans rejouer Stripe — n'a donc rien reçu. Ce script remet ce merci par le
**même** chemin que le webhook (`ThankForPurchase`, branchements de
`bootstrap/wiring.py`), pour un achat désigné par son identifiant
(`payments_purchases.id`).

Une seule fois
--------------
Le registre des notifications du module (`payments_webhook_events`) note
l'événement synthétique `thanks:<purchase_id>` **avant** l'envoi : relancer
le script pour le même achat s'arrête là. C'est le même registre qui
empêche Stripe de livrer deux fois ; un second registre pour un geste
d'exploitation serait une table de plus à oublier.

**Sans `--send`, rien ne part** : le script relit l'achat, dit à quelle
adresse (masquée) le courriel irait et s'arrête.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from uuid import UUID

from karria.bootstrap.orm import TORTOISE_ORM
from karria.bootstrap.wiring import build_thank_for_purchase_wired
from karria.modules.identity.api.dependencies import get_user_repository
from karria.modules.payments import ThankYou, get_purchase_repository, get_webhook_ledger
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

#: Le type d'événement noté au registre : il ne vient pas de Stripe.
LEDGER_EVENT_TYPE = "manual.thanks"


def ledger_event_id(purchase_id: UUID) -> str:
    """La clé d'idempotence d'un rejeu, dans le registre des notifications."""
    return f"thanks:{purchase_id}"


def mask_email(email: str | None) -> str:
    """« m***@gmail.com » — assez pour reconnaître l'adresse, jamais pour la lire."""
    if not email or "@" not in email:
        return "(aucune adresse)"
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


@dataclass(frozen=True, slots=True)
class Report:
    """Ce que le script a fait, pour le journal et la sortie."""

    purchase_id: UUID
    kind: str
    to: str
    sent: bool
    emailed: bool = False
    pushed: int = 0
    reason: str | None = None


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rejoue le merci d'un achat, une seule fois.")
    parser.add_argument("purchase_id", type=UUID, help="payments_purchases.id")
    parser.add_argument("--send", action="store_true", help="Envoie réellement (sinon simule).")
    return parser.parse_args(argv)


async def _run(purchase_id: UUID, *, send: bool) -> Report:  # pragma: no cover - base et réseau
    settings = get_settings()
    configure_logging(settings.observability)
    await connect(TORTOISE_ORM)
    try:
        purchase = await get_purchase_repository().find(purchase_id)
        if purchase is None:
            return Report(
                purchase_id, "?", "(introuvable)", sent=False, reason="purchase_not_found"
            )
        user = await get_user_repository().find_by_id(purchase.user_id)
        to = mask_email(user.email.value if user is not None else None)
        if not send:
            return Report(purchase_id, purchase.kind.value, to, sent=False, reason="dry_run")
        if not await get_webhook_ledger().record(ledger_event_id(purchase_id), LEDGER_EVENT_TYPE):
            return Report(purchase_id, purchase.kind.value, to, sent=False, reason="already_sent")
        delivery = await build_thank_for_purchase_wired().execute(ThankYou.for_purchase(purchase))
        logger.info(
            "payments.thanks_replayed",
            purchase_id=str(purchase_id),
            kind=purchase.kind.value,
            emailed=delivery.emailed,
            pushed=delivery.pushed,
        )
        return Report(
            purchase_id,
            purchase.kind.value,
            to,
            sent=True,
            emailed=delivery.emailed,
            pushed=delivery.pushed,
        )
    finally:
        await disconnect()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - point d'entrée
    args = _parse(sys.argv[1:] if argv is None else argv)
    report = asyncio.run(_run(args.purchase_id, send=args.send))
    sys.stdout.write(
        f"achat {report.purchase_id} ({report.kind}) → {report.to} : "
        + (
            f"envoyé — courriel {'oui' if report.emailed else 'non'}, {report.pushed} appareil(s)\n"
            if report.sent
            else f"rien n'est parti ({report.reason})\n"
        )
    )
    return 0 if report.sent or report.reason == "dry_run" else 1


if __name__ == "__main__":
    raise SystemExit(main())
