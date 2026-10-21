"""L'API push d'Expo — implémentation du port `PushGateway`.

Pourquoi Expo et non Firebase directement
-----------------------------------------
L'app est une app Expo : le jeton qu'elle obtient sans configuration
Firebase est un jeton Expo, et c'est le service d'Expo qui le traduit en
APNs ou FCM. La V1 branchait Firebase Admin avec un fichier de compte de
service : une dépendance lourde et un secret de plus, pour un chemin que
l'app ne produit plus.

Le protocole, en deux appels
----------------------------
- `POST /push/send` avec jusqu'à cent messages → un **ticket** par
  message, dans l'ordre : `{"status": "ok", "id": "…"}` ou
  `{"status": "error", "message": "…", "details": {"error": "…"}}`.
- `POST /push/getReceipts` avec des identifiants de tickets → un **reçu**
  par ticket déjà traité, même forme.

Le client HTTP est celui du socle : délai et reprise sur 5xx compris. Un
envoi n'est **pas** idempotent, donc une seule tentative — retenter un
lot accepté enverrait deux fois chaque notification.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from karria.modules.devices.domain import PushEnvelope, PushError, PushReceipt, PushTicket
from karria.shared.errors import DependencyUnavailableError
from karria.shared.http.outbound import JsonClient, OutboundError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Priorité « haute » : la notification est remise tout de suite, écran
#: éteint compris. C'est ce qu'on veut pour une offre fraîche.
_PRIORITY: Final = "high"


class ExpoPushGateway:
    """Achemine les notifications par le service push d'Expo."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    @classmethod
    def from_settings(
        cls, *, api_url: str, access_token: str, timeout_seconds: float
    ) -> ExpoPushGateway:
        """Construit la passerelle depuis la configuration.

        Une seule tentative (`attempts=1`) : voir l'en-tête du module.
        """
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        return cls(
            JsonClient(
                base_url=api_url, headers=headers, timeout_seconds=timeout_seconds, attempts=1
            )
        )

    async def send(self, envelopes: Sequence[PushEnvelope]) -> Sequence[PushTicket]:
        """Soumet un lot et rend un ticket par enveloppe, dans l'ordre."""
        payload = [
            {
                "to": envelope.token,
                "title": envelope.notification.title,
                "body": envelope.notification.body,
                "data": dict(envelope.notification.data),
                "sound": "default",
                "priority": _PRIORITY,
            }
            for envelope in envelopes
        ]
        try:
            response = await self._client.post_json("/push/send", payload)
        except OutboundError as exc:
            logger.error("devices.expo_unavailable", detail=exc.detail, status=exc.status)
            raise DependencyUnavailableError("expo_push", exc.detail) from exc

        raw = response.mapping.get("data")
        if not isinstance(raw, list) or len(raw) != len(envelopes):
            logger.error("devices.expo_malformed_response", tickets=raw)
            raise DependencyUnavailableError("expo_push", "réponse inattendue")

        return [
            _ticket(envelope.token, entry) for envelope, entry in zip(envelopes, raw, strict=True)
        ]

    async def receipts(self, ticket_ids: Sequence[str]) -> Sequence[PushReceipt]:
        """Interroge les reçus des tickets indiqués."""
        try:
            response = await self._client.post_json("/push/getReceipts", {"ids": list(ticket_ids)})
        except OutboundError as exc:
            logger.error("devices.expo_unavailable", detail=exc.detail, status=exc.status)
            raise DependencyUnavailableError("expo_push", exc.detail) from exc

        raw = response.mapping.get("data")
        if not isinstance(raw, dict):
            logger.error("devices.expo_malformed_response", receipts=raw)
            raise DependencyUnavailableError("expo_push", "réponse inattendue")
        return [_receipt(ticket_id, entry) for ticket_id, entry in raw.items()]


def _status_and_error(entry: object) -> tuple[bool, PushError | None]:
    if not isinstance(entry, dict):
        return False, PushError.UNKNOWN
    if entry.get("status") == "ok":
        return True, None
    details: Any = entry.get("details")
    code = details.get("error") if isinstance(details, dict) else None
    return False, PushError.parse(code)


def _ticket(token: str, entry: object) -> PushTicket:
    ok, error = _status_and_error(entry)
    ticket_id = entry.get("id") if ok and isinstance(entry, dict) else None
    return PushTicket(
        token=token,
        ok=ok and isinstance(ticket_id, str),
        ticket_id=ticket_id if isinstance(ticket_id, str) else None,
        error=error,
    )


def _receipt(ticket_id: str, entry: object) -> PushReceipt:
    ok, error = _status_and_error(entry)
    return PushReceipt(ticket_id=ticket_id, ok=ok, error=error)
