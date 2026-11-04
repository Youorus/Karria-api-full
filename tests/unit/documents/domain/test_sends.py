"""L'envoi par e-mail : adresse, journée civile, quota, états, noms de fichiers (ADR-0031)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from documents.fakes import make_generated
from karria.modules.documents.domain.errors import RecipientInvalidError, SendQuotaExceededError
from karria.modules.documents.domain.sends import (
    DAILY_SEND_LIMIT,
    DocumentSend,
    SendStatus,
    day_bounds,
    ensure_quota,
    normalize_address,
)
from karria.modules.documents.domain.templates import DocumentKind

pytestmark = pytest.mark.unit

NOON = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)


class TestAdresse:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("  Awa@Example.COM ", "Awa@example.com"),
            ("a.b+c@sub.domaine.fr", "a.b+c@sub.domaine.fr"),
        ],
    )
    def test_une_adresse_valide_est_normalisee_sans_toucher_la_partie_locale(
        self, given: str, expected: str
    ) -> None:
        assert normalize_address(given) == expected

    @pytest.mark.parametrize(
        "given", ["", "   ", "sans-arobase", "a@b", "a@b.", "deux@@x.fr", "a b@x.fr", "a@x.f"]
    )
    def test_une_adresse_mal_formee_est_refusee(self, given: str) -> None:
        with pytest.raises(RecipientInvalidError):
            normalize_address(given)

    def test_une_adresse_trop_longue_est_refusee(self) -> None:
        with pytest.raises(RecipientInvalidError):
            normalize_address("a" * 250 + "@x.fr")


class TestQuota:
    def test_la_journee_civile_est_en_utc(self) -> None:
        start, end = day_bounds(datetime(2026, 11, 5, 23, 30, tzinfo=UTC))
        assert start == datetime(2026, 11, 5, tzinfo=UTC)
        assert end == datetime(2026, 11, 6, tzinfo=UTC)

    def test_sous_le_plafond_rien_ne_se_passe(self) -> None:
        ensure_quota(DAILY_SEND_LIMIT - 1, NOON)

    def test_au_plafond_le_refus_dit_quand_reessayer(self) -> None:
        with pytest.raises(SendQuotaExceededError) as exc:
            ensure_quota(DAILY_SEND_LIMIT, NOON)
        details = exc.value.details
        assert details["reason"] == "send_quota_exceeded"
        assert details["retry_after"] == int(timedelta(hours=11, minutes=30).total_seconds())


class TestEtats:
    def test_un_envoi_remis_puis_en_echec_garde_sa_trace(self) -> None:
        send = DocumentSend(
            id=uuid4(),
            user_id=uuid4(),
            generated_ids=(uuid4(),),
            to="x@y.fr",
            status=SendStatus.QUEUED,
            created_at=NOON,
        )
        done = send.succeeded(NOON + timedelta(seconds=3))
        assert done.status is SendStatus.SENT
        assert done.sent_at == NOON + timedelta(seconds=3)
        failed = send.failed("MailServerUnavailableError" * 20)
        assert failed.status is SendStatus.FAILED
        assert failed.error is not None
        assert len(failed.error) == 200


class TestNomsDeFichiers:
    def test_le_cv_et_la_lettre_se_nomment_selon_leur_genre(self) -> None:
        cv = make_generated(uuid4())
        letter = make_generated(
            uuid4(), kind=DocumentKind.LETTER, company_name="Acme", full_name="Awa Diallo"
        )
        assert cv.file_name("pdf") == "CV - Awa Diallo.pdf"
        assert letter.file_name("docx") == "Lettre de motivation - Acme.docx"
        assert make_generated(uuid4(), full_name="").file_name("pdf") == "CV.pdf"
