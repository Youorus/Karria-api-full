"""L'équipe est prévenue de chaque inscription, sans saturer la boîte d'envoi (Marc, 2026-11-20)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from karria.bootstrap.wiring import SIGNUP_ALERTS_PER_HOUR, SignupAlerts, country_label
from karria.modules.identity import SignupAccount, UserRegistered
from karria.shared.http import SlidingWindowLimiter
from karria.shared.mail import MailDeliveryError, MailMessage, OutgoingMail, render_for

pytestmark = pytest.mark.unit


class Directory:
    def __init__(self, region: str | None = "BE", timezone: str | None = "Europe/Brussels") -> None:
        self.region = region
        self.timezone = timezone

    async def account(self, user_id: UUID) -> SignupAccount:
        return SignupAccount(
            email="awa@exemple.fr",
            created_at=datetime(2026, 11, 20, 20, 5, tzinfo=UTC),
            region=self.region,
            timezone=self.timezone,
        )

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


def alerts(
    mailer: Mailer,
    recipient: str = "equipe@exemple.fr",
    cap: int = 3,
    users: Directory | None = None,
) -> SignupAlerts:
    return SignupAlerts(
        users=users or Directory(),
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

    async def test_l_alerte_dit_le_pays_en_francais_et_le_fuseau(self) -> None:
        """ADR-0042 : « nouvelle inscription de x@y.fr — Belgique »."""
        mailer = Mailer()

        await alerts(mailer).on_registered(UserRegistered(user_id=uuid4()))

        [mail] = mailer.sent
        assert mail.context["country"] == "Belgique"
        assert mail.context["timezone"] == "Europe/Brussels"
        rendered = render_for(mail, app_url="https://exemple.fr")
        assert rendered.subject == "Karria — nouvelle inscription de awa@exemple.fr — Belgique"
        assert "Belgique" in rendered.text_body
        assert "Europe/Brussels" in rendered.text_body

    async def test_sans_region_l_alerte_part_quand_meme_et_dit_pays_inconnu(self) -> None:
        """Une app ancienne n'annonce rien : l'alerte ne doit ni échouer ni inventer."""
        mailer = Mailer()
        unknown = Directory(region=None, timezone=None)

        await alerts(mailer, users=unknown).on_registered(UserRegistered(user_id=uuid4()))

        [mail] = mailer.sent
        assert mail.context["country"] == "pays inconnu"
        assert mail.context["timezone"] == "fuseau inconnu"

    @pytest.mark.parametrize(
        ("region", "label"),
        [
            ("FR", "France"),
            ("CI", "Côte d'Ivoire"),
            ("CM", "Cameroun"),
            ("QQ", "pays inconnu"),
            (None, "pays inconnu"),
        ],
    )
    def test_le_nom_du_pays_vient_du_code(self, region: str | None, label: str) -> None:
        assert country_label(region) == label

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
