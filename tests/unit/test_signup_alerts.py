"""L'équipe est prévenue de chaque inscription, sans saturer la boîte d'envoi (Marc, 2026-11-20)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from karria.bootstrap.wiring import SIGNUP_ALERTS_PER_HOUR, SignupAlerts
from karria.modules.identity import UserRegistered
from karria.shared.http import SlidingWindowLimiter
from karria.shared.mail import MailDeliveryError, MailMessage, OutgoingMail

pytestmark = pytest.mark.unit


class Directory:
    async def account(self, user_id: UUID) -> tuple[str | None, datetime]:
        return "awa@exemple.fr", datetime(2026, 11, 20, 20, 5, tzinfo=UTC)

    async def counts(self) -> tuple[int, int]:
        return 37, 4


class Mailer:
    def __init__(self, *, down: bool = False) -> None:
        self.sent: list[OutgoingMail] = []
        self.down = down

    async def send(self, mail: OutgoingMail) -> None:
        if self.down:
            raise MailDeliveryError("smtp")
        self.sent.append(mail)


def alerts(mailer: Mailer, recipient: str = "equipe@exemple.fr", cap: int = 3) -> SignupAlerts:
    return SignupAlerts(
        users=Directory(),
        mailer=mailer,
        recipient=recipient,
        limiter=SlidingWindowLimiter(max_hits=cap, window_seconds=3_600),
    )


class TestAlerteDInscription:
    async def test_chaque_inscription_previent_lequipe(self) -> None:
        mailer = Mailer()

        await alerts(mailer).on_registered(UserRegistered(user_id=uuid4()))

        [mail] = mailer.sent
        assert mail.message is MailMessage.SIGNUP_ALERT
        assert mail.to == "equipe@exemple.fr"
        assert mail.context["email"] == "awa@exemple.fr"
        assert mail.context["signed_up_at"] == "20/11/2026 à 21:05 (heure de Paris)"
        assert (mail.context["total"], mail.context["last_day"]) == (37, 4)

    async def test_au_dela_du_plafond_horaire_les_alertes_sarretent(self) -> None:
        mailer = Mailer()
        team = alerts(mailer, cap=3)

        for _ in range(10):
            await team.on_registered(UserRegistered(user_id=uuid4()))

        assert len(mailer.sent) == 3  # les codes de connexion gardent la boîte d'envoi
        assert SIGNUP_ALERTS_PER_HOUR <= 30

    async def test_sans_destinataire_ou_smtp_en_panne_rien_ne_casse(self) -> None:
        silent = Mailer()
        await alerts(silent, recipient=" ").on_registered(UserRegistered(user_id=uuid4()))
        assert silent.sent == []

        # L'inscription est déjà faite : une panne d'envoi ne remonte pas.
        await alerts(Mailer(down=True)).on_registered(UserRegistered(user_id=uuid4()))
