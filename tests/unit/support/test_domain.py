"""Les règles pures de « Parler à l'équipe » : validité, non-lus, quand prévenir."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from karria.modules.support.domain import (
    ALERT_COOLDOWN,
    PRESENCE_WINDOW,
    Attachment,
    Conversation,
    Message,
    MessageAuthor,
    MessageKind,
    kind_for_content_type,
    preview_of,
)
from karria.shared.errors import InvalidInputError

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)


def _message(kind: MessageKind, **kwargs: object) -> Message:
    return Message(
        id=uuid4(),
        conversation_id=uuid4(),
        author=MessageAuthor.USER,
        kind=kind,
        created_at=T0,
        **kwargs,  # type: ignore[arg-type]
    )


def _voice(duration: timedelta | None) -> Attachment:
    return Attachment(
        key="support/x/voice/2026-10-31/a.m4a",
        content_type="audio/m4a",
        size_bytes=10,
        duration=duration,
    )


class TestMessage:
    def test_un_texte_est_nettoye(self) -> None:
        assert _message(MessageKind.TEXT, body="  Bonjour  ").body == "Bonjour"

    @pytest.mark.parametrize("body", ["", "   ", None])
    def test_un_texte_vide_est_refuse(self, body: str | None) -> None:
        with pytest.raises(InvalidInputError) as exc:
            _message(MessageKind.TEXT, body=body)
        assert exc.value.details["reason"] == "support_message_empty"

    def test_un_texte_trop_long_est_refuse(self) -> None:
        with pytest.raises(InvalidInputError) as exc:
            _message(MessageKind.TEXT, body="x" * 4_001)
        assert exc.value.details["reason"] == "support_message_too_long"

    def test_un_vocal_sans_fichier_est_refuse(self) -> None:
        with pytest.raises(InvalidInputError) as exc:
            _message(MessageKind.VOICE)
        assert exc.value.details["reason"] == "support_attachment_required"

    def test_un_vocal_de_plus_de_cinq_minutes_est_refuse(self) -> None:
        with pytest.raises(InvalidInputError) as exc:
            _message(MessageKind.VOICE, attachment=_voice(timedelta(minutes=5, seconds=1)))
        assert exc.value.details["reason"] == "support_voice_too_long"

    def test_un_vocal_valide_ne_porte_pas_de_texte(self) -> None:
        message = _message(MessageKind.VOICE, attachment=_voice(timedelta(seconds=12)), body="x")
        assert message.body is None
        assert message.attachment is not None

    def test_la_sorte_vient_du_type_du_fichier(self) -> None:
        assert kind_for_content_type("image/png") is MessageKind.IMAGE
        assert kind_for_content_type("audio/m4a") is MessageKind.VOICE
        with pytest.raises(InvalidInputError) as exc:
            kind_for_content_type("application/pdf")
        assert exc.value.details["reason"] == "support_media_unsupported"

    def test_l_apercu_dit_la_nature_ou_tronque_le_texte(self) -> None:
        assert preview_of(_message(MessageKind.VOICE, attachment=_voice(None))) == "Message vocal"
        long = _message(MessageKind.TEXT, body="mot " * 60)
        assert preview_of(long).endswith("…")
        assert len(preview_of(long)) <= 140


class TestConversation:
    def test_un_fil_neuf_n_a_rien_de_non_lu(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0)
        assert not fil.has_unread_for_user
        assert not fil.has_unread_for_team
        assert not fil.awaits_team

    def test_la_reponse_de_l_equipe_est_non_lue_jusqu_a_la_lecture(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0).team_replied(
            at=T0 + timedelta(minutes=1)
        )
        assert fil.has_unread_for_user
        assert not fil.read_by_user(at=T0 + timedelta(minutes=2)).has_unread_for_user

    def test_le_mot_de_la_personne_attend_l_equipe(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0).user_posted(at=T0)
        assert fil.awaits_team
        assert fil.has_unread_for_team
        repondu = fil.team_replied(at=T0 + timedelta(minutes=3))
        assert not repondu.awaits_team
        assert not repondu.has_unread_for_team

    def test_on_notifie_la_premiere_reponse(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0).user_posted(at=T0)
        assert fil.push_decision(at=T0 + timedelta(hours=1))

    def test_on_se_tait_quand_la_personne_a_l_ecran_ouvert(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0).read_by_user(at=T0)
        assert not fil.push_decision(at=T0 + PRESENCE_WINDOW - timedelta(seconds=1))
        assert fil.push_decision(at=T0 + PRESENCE_WINDOW)

    def test_une_rafale_de_l_equipe_ne_fait_qu_une_notification(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0).read_by_user(at=T0)
        premiere = T0 + timedelta(minutes=5)
        assert fil.push_decision(at=premiere)
        fil = fil.team_replied(at=premiere)
        assert not fil.push_decision(at=premiere + timedelta(seconds=30))
        # Lu, puis une nouvelle réponse plus tard : on prévient à nouveau.
        relu = fil.read_by_user(at=premiere + timedelta(minutes=1))
        assert relu.push_decision(at=premiere + timedelta(minutes=10))

    def test_on_alerte_l_equipe_au_premier_mot(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0)
        assert fil.team_alert_decision(at=T0)

    def test_une_rafale_de_la_personne_ne_fait_qu_un_courriel(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0).user_posted(at=T0).team_alerted(at=T0)
        assert not fil.team_alert_decision(at=T0 + timedelta(minutes=2))
        assert fil.team_alert_decision(at=T0 + ALERT_COOLDOWN)

    def test_une_fois_le_fil_relu_par_l_equipe_on_realerte(self) -> None:
        fil = Conversation.opened(uuid4(), uuid4(), at=T0).user_posted(at=T0).team_alerted(at=T0)
        relu = fil.read_by_team(at=T0 + timedelta(minutes=1))
        assert relu.team_alert_decision(at=T0 + timedelta(minutes=2))
