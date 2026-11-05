"""L'envoi par e-mail : la demande (journal, quota, file) et la remise (pièces jointes) — ADR-0031."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from documents.application.conftest import Env
from documents.fakes import (
    DOCX_BYTES,
    NOW,
    PDF_BYTES,
    FakeAccountDirectory,
    FakeDocumentMailer,
    FakeOriginThrottle,
    InMemorySendRepository,
    make_generated,
)
from karria.modules.documents.application import DeliverDocuments, GetSend, SendDocuments
from karria.modules.documents.domain.errors import (
    AccountEmailUnknownError,
    DocumentFileMissingError,
    GeneratedCvNotFoundError,
    RecipientInvalidError,
    SendNotFoundError,
    SendQuotaExceededError,
)
from karria.modules.documents.domain.sends import (
    DAILY_SEND_LIMIT,
    DAILY_SENDS_PER_RECIPIENT,
    DAILY_SENDS_PER_RECIPIENT_GLOBAL,
    SendStatus,
)
from karria.modules.documents.domain.templates import DocumentKind
from karria.shared.errors.exceptions import InvalidInputError
from karria.shared.mail import MailDeliveryError
from karria.shared.storage import Bucket

pytestmark = pytest.mark.unit


class SendEnv:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.sends = InMemorySendRepository()
        self.accounts = FakeAccountDirectory()
        self.mailer = FakeDocumentMailer()
        self.origins = FakeOriginThrottle()

    def request(self) -> SendDocuments:
        return SendDocuments(
            generated=self.env.generated,
            sends=self.sends,
            accounts=self.accounts,
            jobs=self.env.jobs,
            clock=self.env.clock,
            origins=self.origins,
        )

    def deliver(self) -> DeliverDocuments:
        return DeliverDocuments(
            sends=self.sends,
            generated=self.env.generated,
            storage=self.env.storage,
            mailer=self.mailer,
            clock=self.env.clock,
        )

    async def cv(self, *, pdf: bool = True, docx: bool = False) -> object:
        generated = make_generated(
            self.env.user,
            pdf_key=f"generated-cvs/{uuid4()}/cv.pdf" if pdf else None,
            docx_key=f"generated-cvs/{uuid4()}/cv.docx" if docx else None,
        )
        if generated.pdf_key:
            await self.env.storage.put(
                Bucket.DOCUMENTS, generated.pdf_key, PDF_BYTES, content_type="application/pdf"
            )
        if generated.docx_key:
            await self.env.storage.put(
                Bucket.DOCUMENTS,
                generated.docx_key,
                DOCX_BYTES,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        await self.env.generated.add(generated)
        return generated


@pytest.fixture
def sends(env: Env) -> SendEnv:
    return SendEnv(env)


class TestDemande:
    async def test_un_envoi_est_journalise_puis_differe(self, sends: SendEnv) -> None:
        cv = await sends.cv()

        send = await sends.request().execute(
            sends.env.user, generated_ids=[cv.id], to=" Ami@Exemple.FR "
        )

        assert send.status is SendStatus.QUEUED
        assert send.to == "Ami@exemple.fr"
        assert send.generated_ids == (cv.id,)
        assert send.created_at == NOW
        assert sends.sends.items[send.id] == send
        assert sends.env.jobs.sends == [send.id]

    async def test_sans_adresse_c_est_celle_du_compte(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        sends.accounts.emails[sends.env.user] = "moi@exemple.fr"

        send = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to=None)

        assert send.to == "moi@exemple.fr"

    async def test_sans_adresse_de_compte_connue_le_refus_est_explicite(
        self, sends: SendEnv
    ) -> None:
        cv = await sends.cv()
        with pytest.raises(AccountEmailUnknownError):
            await sends.request().execute(sends.env.user, generated_ids=[cv.id], to=None)

    async def test_les_documents_sont_verifies_avant_tout(self, sends: SendEnv) -> None:
        other = make_generated(uuid4())
        await sends.env.generated.add(other)
        empty = make_generated(sends.env.user, pdf_key=None)
        await sends.env.generated.add(empty)

        with pytest.raises(GeneratedCvNotFoundError):
            await sends.request().execute(sends.env.user, generated_ids=[other.id], to="a@b.fr")
        with pytest.raises(GeneratedCvNotFoundError):
            await sends.request().execute(sends.env.user, generated_ids=[uuid4()], to="a@b.fr")
        with pytest.raises(DocumentFileMissingError):
            await sends.request().execute(sends.env.user, generated_ids=[empty.id], to="a@b.fr")
        assert sends.sends.items == {}

    async def test_une_adresse_mal_formee_est_refusee(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        with pytest.raises(RecipientInvalidError):
            await sends.request().execute(
                sends.env.user, generated_ids=[cv.id], to="pas-une-adresse"
            )

    async def test_entre_un_et_quatre_documents_sans_doublon(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        with pytest.raises(InvalidInputError):
            await sends.request().execute(sends.env.user, generated_ids=[], to="a@b.fr")
        five = [(await sends.cv()).id for _ in range(5)]
        with pytest.raises(InvalidInputError):
            await sends.request().execute(sends.env.user, generated_ids=five, to="a@b.fr")
        send = await sends.request().execute(
            sends.env.user, generated_ids=[cv.id, cv.id], to="a@b.fr"
        )
        assert send.generated_ids == (cv.id,)

    async def test_le_sixieme_envoi_du_jour_est_refuse_mais_pas_celui_du_lendemain(
        self, sends: SendEnv
    ) -> None:
        cv = await sends.cv()
        sends.accounts.emails[sends.env.user] = "moi@exemple.fr"
        for _ in range(DAILY_SEND_LIMIT):
            await sends.request().execute(sends.env.user, generated_ids=[cv.id], to=None)

        with pytest.raises(SendQuotaExceededError) as exc:
            await sends.request().execute(sends.env.user, generated_ids=[cv.id], to=None)
        assert exc.value.details["retry_after"] > 0
        assert exc.value.scope == "account"
        assert "scope" not in exc.value.details

        sends.env.clock.advance(timedelta(days=1))
        again = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to=None)
        assert again.status is SendStatus.QUEUED
        assert len(sends.sends.items) == DAILY_SEND_LIMIT + 1

    async def test_deux_envois_par_jour_d_un_compte_vers_une_meme_adresse_tierce(
        self, sends: SendEnv
    ) -> None:
        cv = await sends.cv()
        other_cv = make_generated(uuid4(), pdf_key="generated-cvs/o/cv.pdf")
        await sends.env.generated.add(other_cv)
        # Casse et étiquette `+` ne font pas une autre adresse.
        await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="RH@acme.fr")
        await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="rh+karria@acme.fr")

        with pytest.raises(SendQuotaExceededError) as mine:
            await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="rh@acme.fr")
        assert mine.value.scope == "recipient"
        # Un autre compte garde son propre plafond vers la même adresse.
        theirs = await sends.request().execute(
            other_cv.user_id, generated_ids=[other_cv.id], to="rh@acme.fr"
        )
        assert theirs.status is SendStatus.QUEUED
        # Une autre adresse tierce reste possible, dans le quota du compte.
        ok = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="rh@beta.fr")
        assert ok.status is SendStatus.QUEUED
        assert sends.sends.locked.count(sends.env.user) >= 3

    async def test_le_plafond_global_par_destinataire_couvre_tous_les_comptes(
        self, sends: SendEnv
    ) -> None:
        for _ in range(DAILY_SENDS_PER_RECIPIENT_GLOBAL):
            other = make_generated(uuid4(), pdf_key="generated-cvs/o/cv.pdf")
            await sends.env.generated.add(other)
            await sends.request().execute(other.user_id, generated_ids=[other.id], to="rh@acme.fr")
        cv = await sends.cv()

        with pytest.raises(SendQuotaExceededError) as exc:
            await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="rh@acme.fr")
        assert exc.value.scope == "recipient_global"

    async def test_un_envoi_en_echec_ne_compte_pas(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        for _ in range(DAILY_SENDS_PER_RECIPIENT):
            send = await sends.request().execute(
                sends.env.user, generated_ids=[cv.id], to="rh@acme.fr"
            )
            await sends.sends.save(send.failed("MailServerUnavailableError"))

        again = await sends.request().execute(
            sends.env.user, generated_ids=[cv.id], to="rh@acme.fr"
        )
        assert again.status is SendStatus.QUEUED

    async def test_sa_propre_adresse_echappe_aux_plafonds_anti_relais(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        sends.accounts.emails[sends.env.user] = "Moi@Exemple.fr"
        sends.origins.limit = 0
        for _ in range(3):
            send = await sends.request().execute(
                sends.env.user, generated_ids=[cv.id], to="moi@exemple.fr", origin="1.2.3.4"
            )
            assert send.status is SendStatus.QUEUED
        assert sends.origins.hits == {}

    async def test_une_origine_qui_insiste_est_refusee_et_journalisee(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        sends.origins.limit = 1
        await sends.request().execute(
            sends.env.user, generated_ids=[cv.id], to="a@b.fr", origin="1.2.3.4"
        )
        with pytest.raises(SendQuotaExceededError) as exc:
            await sends.request().execute(
                sends.env.user, generated_ids=[cv.id], to="c@d.fr", origin="1.2.3.4"
            )
        assert exc.value.scope == "origin"
        assert exc.value.details["retry_after"] == 60
        assert sends.origins.hits == {"send|1.2.3.4": 2}

    async def test_une_origine_illisible_partage_un_seau_commun(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        sends.origins.limit = 1
        await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="a@b.fr", origin="")
        with pytest.raises(SendQuotaExceededError):
            await sends.request().execute(
                sends.env.user, generated_ids=[cv.id], to="c@d.fr", origin=None
            )
        assert sends.origins.hits == {"send|unknown": 2}

    async def test_une_file_en_panne_marque_l_envoi_en_echec_tout_de_suite(
        self, sends: SendEnv
    ) -> None:
        cv = await sends.cv()
        sends.env.jobs.send_error = RuntimeError("file")

        send = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="a@b.fr")

        assert send.status is SendStatus.FAILED
        assert send.error == "file indisponible : RuntimeError"
        assert sends.sends.items[send.id].status is SendStatus.FAILED

    async def test_un_envoi_se_relit_par_son_auteur_seulement(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        send = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="a@b.fr")
        assert await GetSend(sends.sends).execute(sends.env.user, send.id) == send
        with pytest.raises(SendNotFoundError):
            await GetSend(sends.sends).execute(uuid4(), send.id)


class TestRemise:
    async def test_les_pieces_sont_jointes_avec_leur_nom_et_la_signature(
        self, sends: SendEnv
    ) -> None:
        cv = await sends.cv()
        letter = make_generated(
            sends.env.user,
            pdf_key=None,
            docx_key="generated-letters/x/lettre.docx",
            kind=DocumentKind.LETTER,
            company_name="Acme",
        )
        await sends.env.storage.put(
            Bucket.DOCUMENTS,
            "generated-letters/x/lettre.docx",
            DOCX_BYTES,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        await sends.env.generated.add(letter)
        send = await sends.request().execute(
            sends.env.user, generated_ids=[cv.id, letter.id], to="rh@acme.fr"
        )

        status = await sends.deliver().execute(send.id)

        assert status is SendStatus.SENT
        [(to, sender_name, documents)] = sends.mailer.sent
        assert to == "rh@acme.fr"
        assert sender_name == "Awa Diallo"
        assert [(d.file_name, d.content_type[:16], d.data) for d in documents] == [
            ("CV - Awa Diallo.pdf", "application/pdf", PDF_BYTES),
            ("Lettre de motivation - Acme.docx", "application/vnd.", DOCX_BYTES),
        ]
        done = sends.sends.items[send.id]
        assert done.sent_at == NOW
        assert done.error is None

    async def test_un_transport_en_panne_marque_l_echec_sans_lever(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        send = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="a@b.fr")
        sends.mailer.error = MailDeliveryError("smtp")

        assert await sends.deliver().execute(send.id) is SendStatus.FAILED
        assert sends.sends.items[send.id].error == "MailDeliveryError"

    async def test_un_fichier_disparu_du_bucket_marque_l_echec(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        send = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="a@b.fr")
        await sends.env.storage.delete(Bucket.DOCUMENTS, cv.pdf_key)

        assert await sends.deliver().execute(send.id) is SendStatus.FAILED
        assert sends.sends.items[send.id].error == "ObjectNotFoundError"
        assert sends.mailer.sent == []

    async def test_un_envoi_deja_remis_ou_inconnu_ne_repart_pas(self, sends: SendEnv) -> None:
        cv = await sends.cv()
        send = await sends.request().execute(sends.env.user, generated_ids=[cv.id], to="a@b.fr")
        await sends.deliver().execute(send.id)

        assert await sends.deliver().execute(send.id) is SendStatus.SENT
        assert await sends.deliver().execute(uuid4()) is None
        assert len(sends.mailer.sent) == 1

    async def test_un_nom_qui_ressemble_a_un_lien_ne_signe_pas_et_ne_nomme_pas_la_piece(
        self, sends: SendEnv
    ) -> None:
        generated = make_generated(sends.env.user, full_name="www.arnaque.fr")
        await sends.env.storage.put(
            Bucket.DOCUMENTS, generated.pdf_key, PDF_BYTES, content_type="application/pdf"
        )
        await sends.env.generated.add(generated)
        send = await sends.request().execute(
            sends.env.user, generated_ids=[generated.id], to="a@b.fr"
        )

        await sends.deliver().execute(send.id)

        _, sender_name, documents = sends.mailer.sent[0]
        assert sender_name == "Une personne"
        assert documents[0].file_name == "CV.pdf"

    async def test_sans_nom_sur_les_documents_la_signature_reste_sobre(
        self, sends: SendEnv
    ) -> None:
        generated = make_generated(sends.env.user, full_name="   ")
        await sends.env.storage.put(
            Bucket.DOCUMENTS, generated.pdf_key, PDF_BYTES, content_type="application/pdf"
        )
        await sends.env.generated.add(generated)
        send = await sends.request().execute(
            sends.env.user, generated_ids=[generated.id], to="a@b.fr"
        )

        await sends.deliver().execute(send.id)

        assert sends.mailer.sent[0][1] == "Une personne"
        assert sends.mailer.sent[0][2][0].file_name == "CV.pdf"
