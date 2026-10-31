"""Les cas d'usage de « Parler à l'équipe », sur des doublures."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from doubles import SupportEnv, sample_image
from karria.modules.support.application import (
    CountUnreadForUser,
    ForgetUserSupport,
    GetConversationForTeam,
    GetMyConversation,
    ListConversationsForTeam,
    MarkReadByUser,
    ReplyAsTeam,
    SendUserMessage,
)
from karria.modules.support.domain import MessageAuthor, MessageKind
from karria.shared.config import Environment, StorageSettings
from karria.shared.database import NullUnitOfWork
from karria.shared.errors import DependencyUnavailableError, EntityNotFoundError, InvalidInputError
from karria.shared.push import PushKind
from karria.shared.storage import Bucket, InMemoryObjectStorage

pytestmark = pytest.mark.unit


class Tools:
    """Les cas d'usage assemblés sur l'environnement de doublures."""

    def __init__(self) -> None:
        self.env = SupportEnv.build()
        self.storage = InMemoryObjectStorage(StorageSettings(), Environment.TEST.value)
        env, uow = self.env, NullUnitOfWork()
        self.get = GetMyConversation(
            conversations=env.conversations, messages=env.messages, clock=env.clock, uow=uow
        )
        self.send = SendUserMessage(
            conversations=env.conversations,
            messages=env.messages,
            storage=self.storage,
            feed=env.feed,
            alerter=env.alerter,
            directory=env.directory,
            get_conversation=self.get,
            clock=env.clock,
            uow=uow,
        )
        self.reply = ReplyAsTeam(
            conversations=env.conversations,
            messages=env.messages,
            storage=self.storage,
            feed=env.feed,
            notifier=env.notifier,
            clock=env.clock,
            uow=uow,
        )
        self.mark = MarkReadByUser(conversations=env.conversations, clock=env.clock, uow=uow)
        self.unread = CountUnreadForUser(conversations=env.conversations)
        self.list = ListConversationsForTeam(
            conversations=env.conversations, directory=env.directory
        )
        self.open_for_team = GetConversationForTeam(
            conversations=env.conversations,
            messages=env.messages,
            directory=env.directory,
            clock=env.clock,
            uow=uow,
        )
        self.forget = ForgetUserSupport(
            conversations=env.conversations, messages=env.messages, storage=self.storage, uow=uow
        )


class TestGetMyConversation:
    async def test_ouvre_le_fil_une_fois_puis_le_retrouve(self) -> None:
        t, user = Tools(), uuid4()
        first = await t.get.execute(user)
        second = await t.get.execute(user)
        assert first.conversation.id == second.conversation.id
        assert len(t.env.conversations.rows) == 1
        assert second.messages == []


class TestSendUserMessage:
    async def test_un_texte_reveille_le_fil_et_alerte_l_equipe(self) -> None:
        t, user = Tools(), uuid4()
        message = await t.send.text(user, "  Le fil ne charge plus. ")
        assert message.author is MessageAuthor.USER
        assert message.body == "Le fil ne charge plus."
        assert t.env.feed.woken == [message.conversation_id]
        assert t.env.alerter.alerts[0]["preview"] == "Le fil ne charge plus."
        assert t.env.alerter.alerts[0]["user_email"] == "awa@exemple.fr"
        fil = await t.env.conversations.find(message.conversation_id)
        assert fil is not None
        assert fil.awaits_team
        assert fil.team_alerted_at is not None

    async def test_une_rafale_ne_fait_qu_un_courriel(self) -> None:
        t, user = Tools(), uuid4()
        await t.send.text(user, "un")
        t.env.clock.advance(timedelta(minutes=2))
        await t.send.text(user, "deux")
        assert len(t.env.alerter.alerts) == 1
        t.env.clock.advance(timedelta(minutes=10))
        await t.send.text(user, "trois")
        assert len(t.env.alerter.alerts) == 2

    async def test_une_image_est_rangee_dans_le_bucket_et_le_message_porte_sa_cle(self) -> None:
        t, user = Tools(), uuid4()
        message = await t.send.media(user, sample_image(), "image/jpeg", duration=None)
        assert message.kind is MessageKind.IMAGE
        assert message.attachment is not None
        assert message.attachment.key.startswith(f"support/{message.conversation_id}/image/")
        assert await t.storage.exists(Bucket.SUPPORT, message.attachment.key)
        assert message.attachment.duration is None

    async def test_un_vocal_garde_sa_duree(self) -> None:
        t, user = Tools(), uuid4()
        message = await t.send.media(
            user, b"\x00" * 2048, "audio/m4a", duration=timedelta(seconds=12)
        )
        assert message.kind is MessageKind.VOICE
        assert message.attachment is not None
        assert message.attachment.duration == timedelta(seconds=12)
        assert message.attachment.key.endswith(".m4a")

    async def test_un_fichier_d_un_autre_type_est_refuse_sans_rien_ecrire(self) -> None:
        t, user = Tools(), uuid4()
        with pytest.raises(InvalidInputError):
            await t.send.media(user, b"%PDF-1.4", "application/pdf", duration=None)
        assert t.env.messages.rows == []


class TestReplyAsTeam:
    async def test_une_reponse_notifie_la_personne_et_la_reveille(self) -> None:
        t, user = Tools(), uuid4()
        posted = await t.send.text(user, "Bonjour")
        t.env.clock.advance(timedelta(minutes=5))
        reply = await t.reply.text(posted.conversation_id, "Bonjour Awa, on regarde.")
        assert reply.author is MessageAuthor.TEAM
        assert [m.kind for _, m in t.env.notifier.sent] == [PushKind.SUPPORT]
        assert t.env.notifier.sent[0][0] == user
        assert t.env.notifier.sent[0][1].url == "karria://equipe"
        assert t.env.feed.woken[-1] == posted.conversation_id
        assert await t.unread.execute(user)

    async def test_pas_de_notification_quand_la_personne_lit_en_direct(self) -> None:
        t, user = Tools(), uuid4()
        posted = await t.send.text(user, "Bonjour")
        t.env.clock.advance(timedelta(seconds=10))
        await t.reply.text(posted.conversation_id, "Je suis là.")
        assert t.env.notifier.sent == []

    async def test_une_rafale_de_reponses_ne_fait_qu_une_notification(self) -> None:
        t, user = Tools(), uuid4()
        posted = await t.send.text(user, "Bonjour")
        t.env.clock.advance(timedelta(minutes=5))
        await t.reply.text(posted.conversation_id, "Un")
        await t.reply.text(posted.conversation_id, "Deux")
        assert len(t.env.notifier.sent) == 1

    async def test_expo_en_panne_n_annule_pas_la_reponse(self) -> None:
        t, user = Tools(), uuid4()
        posted = await t.send.text(user, "Bonjour")
        t.env.clock.advance(timedelta(minutes=5))
        t.env.notifier.fail_with = DependencyUnavailableError("expo")
        reply = await t.reply.text(posted.conversation_id, "Réponse")
        assert reply in t.env.messages.rows

    async def test_un_fil_inconnu_est_une_erreur(self) -> None:
        t = Tools()
        with pytest.raises(EntityNotFoundError):
            await t.reply.text(uuid4(), "x")

    async def test_l_equipe_peut_envoyer_une_image(self) -> None:
        t, user = Tools(), uuid4()
        posted = await t.send.text(user, "Bonjour")
        reply = await t.reply.media(posted.conversation_id, sample_image(fmt="PNG"), "image/png")
        assert reply.kind is MessageKind.IMAGE
        assert reply.author is MessageAuthor.TEAM


class TestReadAndList:
    async def test_lire_efface_le_non_lu(self) -> None:
        t, user = Tools(), uuid4()
        posted = await t.send.text(user, "Bonjour")
        t.env.clock.advance(timedelta(minutes=5))
        await t.reply.text(posted.conversation_id, "Réponse")
        assert await t.unread.execute(user)
        await t.mark.execute(user)
        assert not await t.unread.execute(user)

    async def test_marquer_lu_sans_fil_ne_fait_rien(self) -> None:
        t = Tools()
        await t.mark.execute(uuid4())
        assert t.env.conversations.rows == {}

    async def test_la_liste_de_l_equipe_trie_et_filtre(self) -> None:
        t = Tools()
        a, b = uuid4(), uuid4()
        first = await t.send.text(a, "Premier")
        t.env.clock.advance(timedelta(minutes=1))
        await t.send.text(b, "Second")
        t.env.clock.advance(timedelta(minutes=1))
        await t.reply.text(first.conversation_id, "Répondu")

        tout = await t.list.execute(limit=10, offset=0, awaiting_only=False)
        assert tout.total == 2
        assert tout.items[0].listing.conversation.id == first.conversation_id
        assert tout.items[0].listing.message_count == 2
        assert tout.items[0].user_email == "awa@exemple.fr"

        attente = await t.list.execute(limit=10, offset=0, awaiting_only=True)
        assert [i.listing.conversation.user_id for i in attente.items] == [b]

    async def test_ouvrir_un_fil_le_marque_lu_par_l_equipe(self) -> None:
        t, user = Tools(), uuid4()
        posted = await t.send.text(user, "Bonjour")
        view = await t.open_for_team.execute(posted.conversation_id)
        assert view.conversation.has_unread_for_team is False
        assert [m.body for m in view.messages] == ["Bonjour"]
        with pytest.raises(EntityNotFoundError):
            await t.open_for_team.execute(uuid4())


class TestForget:
    async def test_efface_le_fil_et_ses_fichiers(self) -> None:
        t, user = Tools(), uuid4()
        message = await t.send.media(user, sample_image(), "image/jpeg", duration=None)
        assert message.attachment is not None
        assert await t.forget.execute(user) == 1
        assert not await t.storage.exists(Bucket.SUPPORT, message.attachment.key)
        assert await t.env.conversations.find_by_user(user) is None
        assert await t.forget.execute(user) == 0
