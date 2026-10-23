"""Campagne « Karria est de retour » : une notification, une fois, aux anciens de la V1.

    uv run python -m scripts.push_campaign --in v1_criteria.json --ledger campagne.json
    uv run python -m scripts.push_campaign --in v1_criteria.json --ledger campagne.json --send

**Sans `--send`, rien ne part** : le script dit combien de personnes il
toucherait, et pourquoi il en écarte d'autres. C'est le mode par défaut,
et la seule façon de vérifier la cible avant d'appuyer.

Qui reçoit
----------
Les comptes du fichier produit par `scripts/migrate_v1.py convert` qui
avaient les notifications **activées** en V1, dont l'adresse était
vérifiée, qui existent en V2 et qui ont **au moins un appareil joignable**
(jeton présent, permission accordée — `push_targets_of_user`). L'envoi
passe par `build_user_notifier()`, le même branchement que les alertes
d'offres : les jetons morts se détachent, les tickets attendent leur reçu.

Une seule fois
--------------
Un registre local (`--ledger`, JSON) garde l'identifiant V2 de chaque
personne déjà prévenue. Relancer le script — après une coupure, le
lendemain pour les appareils arrivés depuis — ne renvoie rien à qui l'a
déjà reçue. Le registre est écrit **après chaque envoi accepté**, pas à la
fin : une interruption ne fait perdre que l'envoi en cours.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from karria.bootstrap.orm import TORTOISE_ORM
from karria.bootstrap.wiring import build_user_notifier
from karria.modules.devices.api.dependencies import get_device_repository
from karria.modules.devices.domain import PushNotification
from karria.modules.identity.api.dependencies import get_user_repository
from karria.modules.identity.domain import EmailAddress, InvalidEmailError
from karria.shared.config import get_settings
from karria.shared.database import connect, disconnect
from karria.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

CAMPAIGN_ID: Final = "v1-retour-2026"
CAMPAIGN_TITLE: Final = "Karria est de retour"
CAMPAIGN_BODY: Final = (
    "Ton fil d'offres a été refait à neuf. Ouvre l'app : tes recherches t'attendent, "
    "et les nouvelles offres aussi."
)


def notification() -> PushNotification:
    """Le message de la campagne, validé par les bornes du domaine (titre et corps bornés)."""
    return PushNotification(
        title=CAMPAIGN_TITLE,
        body=CAMPAIGN_BODY,
        data={"screen": "feed", "campaign": CAMPAIGN_ID},
    )


def eligible(record: Mapping[str, Any]) -> bool:
    """La personne avait les notifications activées en V1 et une adresse vérifiée."""
    return bool(record.get("email_verified")) and bool(record.get("notifications_enabled"))


@dataclass(slots=True)
class Ledger:
    """Les identifiants V2 déjà prévenus, et quand. Un fichier JSON, relu et réécrit tel quel."""

    path: Path
    notified: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Ledger:
        """Relit le registre ; un fichier absent est un registre vide."""
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text("utf-8"))
        notified = raw.get("notified") if isinstance(raw, dict) else None
        return cls(path=path, notified=dict(notified or {}))

    def contains(self, user_id: str) -> bool:
        """Cette personne a-t-elle déjà reçu la campagne ?"""
        return user_id in self.notified

    def add(self, user_id: str, *, at: datetime) -> None:
        """Note l'envoi et l'écrit aussitôt."""
        self.notified[user_id] = at.isoformat()
        self.save()

    def save(self) -> None:
        """Écrit le registre sur le disque."""
        payload = {"campaign": CAMPAIGN_ID, "notified": self.notified}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")


def _read_records(path: Path) -> list[dict[str, Any]]:  # pragma: no cover - fichier
    records: list[dict[str, Any]] = json.loads(path.read_text("utf-8"))
    return records


async def _run(
    source: Path, ledger_path: Path, *, send: bool
) -> dict[str, Any]:  # pragma: no cover - base et réseau
    settings = get_settings()
    configure_logging(settings.observability)
    records = _read_records(source)
    ledger = Ledger.load(ledger_path)
    message = notification()
    counts: Counter[str] = Counter()
    await connect(TORTOISE_ORM)
    try:
        users = get_user_repository()
        devices = get_device_repository()
        notifier = build_user_notifier()
        for record in records:
            if not eligible(record):
                counts["ineligible"] += 1
                continue
            try:
                user = await users.find_by_email(EmailAddress.parse(str(record["email"])))
            except InvalidEmailError:
                user = None
            if user is None:
                counts["unknown_in_v2"] += 1
                continue
            if ledger.contains(str(user.id)):
                counts["already_notified"] += 1
                continue
            if not await devices.push_targets_of_user(user.id):
                counts["no_reachable_device"] += 1
                continue
            counts["to_send"] += 1
            if not send:
                continue
            accepted = await notifier.notify(
                user.id, title=message.title, body=message.body, data=message.data
            )
            counts["sent"] += 1
            counts["devices_accepted"] += accepted
            if accepted:
                ledger.add(str(user.id), at=datetime.now(UTC))
            else:
                counts["sent_but_rejected"] += 1
        logger.info("push_campaign.done", campaign=CAMPAIGN_ID, send=send, **dict(counts))
    finally:
        await disconnect()
    return {"campaign": CAMPAIGN_ID, "send": send, **dict(sorted(counts.items()))}


async def main(argv: list[str]) -> int:  # pragma: no cover - point d'entrée
    """Analyse la ligne de commande ; simulation par défaut."""
    parser = argparse.ArgumentParser(prog="scripts.push_campaign", description=__doc__)
    parser.add_argument("--in", dest="source", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--send", action="store_true", help="envoie réellement (sinon simule)")
    args = parser.parse_args(argv)
    print(json.dumps(await _run(args.source, args.ledger, send=args.send), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    sys.exit(asyncio.run(main(sys.argv[1:])))
