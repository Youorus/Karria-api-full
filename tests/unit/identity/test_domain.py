"""Les règles pures du module `identity` : adresse, consentement, parcours, session."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from karria.modules.identity.domain import (
    ACCESS_TOKEN_LIFETIME,
    LOGIN_CHALLENGE_LIFETIME,
    LOGIN_CODE_RESEND_DELAY,
    MAX_CODE_ATTEMPTS,
    MAX_CODE_SENDS,
    REFRESH_REUSE_GRACE,
    REFRESH_TOKEN_LIFETIME,
    Consent,
    EmailAddress,
    InvalidEmailError,
    LoginChallengeExpiredError,
    LoginChallengeStatus,
    LoginCodeExhaustedError,
    LoginCodeResendExhaustedError,
    LoginCodeResendTooSoonError,
    SessionEndReason,
    SessionExpiredError,
    SessionRevokedError,
    User,
    open_challenge,
    open_session,
)
from karria.shared.errors import ErrorCode

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


class TestEmailAddress:
    @pytest.mark.parametrize(
        ("saisie", "attendue"),
        [
            ("Marc@Exemple.FR", "marc@exemple.fr"),
            ("  marc@exemple.fr ", "marc@exemple.fr"),
            ("a.b+tag@sub.domaine.org", "a.b+tag@sub.domaine.org"),
        ],
    )
    def test_normalise_en_minuscules_sans_blancs(self, saisie: str, attendue: str) -> None:
        assert EmailAddress.parse(saisie).value == attendue

    @pytest.mark.parametrize(
        "saisie", ["", "   ", "marc", "marc@", "@exemple.fr", "a@b", "a b@c.fr"]
    )
    def test_refuse_ce_qui_nest_pas_une_adresse(self, saisie: str) -> None:
        with pytest.raises(InvalidEmailError) as exc:
            EmailAddress.parse(saisie)
        assert exc.value.code is ErrorCode.INVALID_INPUT
        assert exc.value.details == {"reason": "user_email_invalid", "field": "email"}

    def test_refuse_une_adresse_trop_longue(self) -> None:
        with pytest.raises(InvalidEmailError):
            EmailAddress.parse("a" * 250 + "@exemple.fr")

    def test_masque_la_partie_locale_sauf_la_premiere_lettre(self) -> None:
        assert EmailAddress("marc@exemple.fr").masked == "m•••@exemple.fr"
        assert EmailAddress("m@exemple.fr").masked == "m•@exemple.fr"


class TestConsent:
    def test_une_acceptation_identique_garde_sa_date(self) -> None:
        consent = Consent("2026-10", "2026-10", NOW)

        same = consent.renewed(
            terms_version="2026-10", privacy_policy_version="2026-10", at=NOW + timedelta(days=9)
        )

        assert same is consent

    def test_une_nouvelle_version_est_horodatee(self) -> None:
        consent = Consent("2026-10", "2026-10", NOW)
        later = NOW + timedelta(days=30)

        renewed = consent.renewed(
            terms_version="2026-11", privacy_policy_version="2026-10", at=later
        )

        assert renewed.terms_version == "2026-11"
        assert renewed.accepted_at == later


class TestUser:
    def _user(self) -> User:
        return User(
            id=uuid4(),
            email=EmailAddress("marc@exemple.fr"),
            email_verified_at=NOW,
            consent=Consent("1", "1", NOW),
            created_at=NOW,
            updated_at=NOW,
        )

    def test_with_consent_ne_change_rien_si_les_versions_sont_les_memes(self) -> None:
        user = self._user()

        assert user.with_consent(terms_version="1", privacy_policy_version="1", at=NOW) is user

    def test_with_consent_met_a_jour_le_compte(self) -> None:
        later = NOW + timedelta(days=1)
        updated = self._user().with_consent(terms_version="2", privacy_policy_version="1", at=later)

        assert updated.consent.terms_version == "2"
        assert updated.updated_at == later

    def test_signed_in_horodate_la_connexion(self) -> None:
        later = NOW + timedelta(hours=2)
        assert self._user().signed_in(at=later).last_login_at == later


class TestLoginChallenge:
    def test_souvre_pour_dix_minutes(self) -> None:
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="marc@exemple.fr",
            user_id=None,
            code_hash="h",
            terms_version="1",
            privacy_policy_version="1",
            at=NOW,
        )
        assert challenge.expires_at == NOW + LOGIN_CHALLENGE_LIFETIME
        assert challenge.seconds_until_expiry(NOW) == 600
        assert challenge.seconds_before_resend(NOW) == 30
        assert challenge.is_open

    def test_expire_puis_refuse(self) -> None:
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="e@x.fr",
            user_id=None,
            code_hash="h",
            terms_version="1",
            privacy_policy_version="1",
            at=NOW,
        )
        challenge.ensure_usable_at(NOW + timedelta(minutes=9))
        with pytest.raises(LoginChallengeExpiredError) as exc:
            challenge.ensure_usable_at(NOW + LOGIN_CHALLENGE_LIFETIME)
        assert exc.value.code is ErrorCode.GONE
        assert exc.value.details["reason"] == "auth_login_challenge_expired"
        assert challenge.seconds_until_expiry(NOW + timedelta(hours=1)) == 0

    def test_un_parcours_clos_est_refuse_comme_perime(self) -> None:
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="e@x.fr",
            user_id=None,
            code_hash="h",
            terms_version="1",
            privacy_policy_version="1",
            at=NOW,
        )
        done = challenge.completed(user_id=uuid4(), created_account=True, at=NOW)

        assert done.status is LoginChallengeStatus.COMPLETED
        assert done.created_account
        with pytest.raises(LoginChallengeExpiredError):
            done.ensure_usable_at(NOW)

    def test_le_renvoi_attend_le_delai(self) -> None:
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="e@x.fr",
            user_id=None,
            code_hash="h",
            terms_version="1",
            privacy_policy_version="1",
            at=NOW,
        )
        with pytest.raises(LoginCodeResendTooSoonError) as exc:
            challenge.resent(code_hash="h2", at=NOW + timedelta(seconds=10))
        assert exc.value.code is ErrorCode.RATE_LIMITED
        assert exc.value.details["retry_after"] == 20

    def test_le_renvoi_remplace_le_code_et_remet_les_essais_a_zero(self) -> None:
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="e@x.fr",
            user_id=None,
            code_hash="h",
            terms_version="1",
            privacy_policy_version="1",
            at=NOW,
        ).failed_attempt(at=NOW)
        later = NOW + LOGIN_CODE_RESEND_DELAY

        resent = challenge.resent(code_hash="h2", at=later)

        assert resent.code_hash == "h2"
        assert resent.attempts == 0
        assert resent.code_sends == 2
        assert resent.seconds_before_resend(later) == 30

    def test_le_renvoi_sepuise(self) -> None:
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="e@x.fr",
            user_id=None,
            code_hash="h",
            terms_version="1",
            privacy_policy_version="1",
            at=NOW,
        )
        moment = NOW
        for _ in range(MAX_CODE_SENDS - 1):
            moment += LOGIN_CODE_RESEND_DELAY
            challenge = challenge.resent(code_hash="h", at=moment)
        with pytest.raises(LoginCodeResendExhaustedError) as exc:
            challenge.resent(code_hash="h", at=moment + LOGIN_CODE_RESEND_DELAY)
        assert exc.value.details["reason"] == "auth_login_code_resend_exhausted"

    def test_cinq_essais_brulent_le_code(self) -> None:
        challenge = open_challenge(
            challenge_id=uuid4(),
            email="e@x.fr",
            user_id=None,
            code_hash="h",
            terms_version="1",
            privacy_policy_version="1",
            at=NOW,
        )
        for _ in range(MAX_CODE_ATTEMPTS):
            challenge.ensure_attempt_allowed()
            challenge = challenge.failed_attempt(at=NOW)

        assert challenge.remaining_attempts == 0
        assert challenge.status is LoginChallengeStatus.FAILED
        with pytest.raises(LoginCodeExhaustedError) as exc:
            challenge.ensure_attempt_allowed()
        assert exc.value.details == {
            "reason": "auth_code_exhausted",
            "field": "code",
            "retry_after": 0,
        }


class TestSession:
    def _session(self):  # noqa: ANN202
        return open_session(
            session_id=uuid4(),
            user_id=uuid4(),
            access_token_hash="a",
            refresh_token_hash="r",
            at=NOW,
            user_agent="Karria/2.0",
        )

    def test_le_jeton_dacces_vit_quinze_minutes(self) -> None:
        session = self._session()
        assert session.access_is_valid_at(NOW + timedelta(minutes=14))
        assert not session.access_is_valid_at(NOW + ACCESS_TOKEN_LIFETIME)
        assert session.issued_at == NOW

    def test_la_rotation_perime_lancien_jeton_et_repousse_la_fenetre(self) -> None:
        later = NOW + timedelta(days=100)
        rotated = self._session().rotated(access_token_hash="a2", refresh_token_hash="r2", at=later)

        assert rotated.previous_refresh_token_hash == "r"  # noqa: S105
        assert rotated.refresh_token_hash == "r2"  # noqa: S105
        assert rotated.rotation_count == 1
        assert rotated.refresh_expires_at == later + REFRESH_TOKEN_LIFETIME
        assert rotated.issued_at == later

    def test_le_rejeu_est_benin_dans_la_minute_puis_ne_lest_plus(self) -> None:
        rotated = self._session().rotated(access_token_hash="a2", refresh_token_hash="r2", at=NOW)
        assert rotated.reuse_is_benign_at(NOW + REFRESH_REUSE_GRACE)
        assert not rotated.reuse_is_benign_at(NOW + REFRESH_REUSE_GRACE + timedelta(seconds=1))
        assert not rotated.ended(reason=SessionEndReason.LOGGED_OUT, at=NOW).reuse_is_benign_at(NOW)

    def test_une_session_close_ne_se_rafraichit_plus(self) -> None:
        ended = self._session().ended(reason=SessionEndReason.TOKEN_REUSE_DETECTED, at=NOW)
        with pytest.raises(SessionRevokedError) as exc:
            ended.ensure_refreshable_at(NOW)
        assert exc.value.code is ErrorCode.UNAUTHENTICATED
        assert exc.value.details["end_reason"] == "token_reuse_detected"

    def test_une_session_perimee_ne_se_rafraichit_plus(self) -> None:
        with pytest.raises(SessionExpiredError):
            self._session().ensure_refreshable_at(NOW + REFRESH_TOKEN_LIFETIME)

    def test_lusage_ne_secrit_que_de_temps_en_temps(self) -> None:
        session = self._session()
        assert not session.should_record_use(
            NOW + timedelta(minutes=1), granularity=timedelta(minutes=5)
        )
        assert session.should_record_use(
            NOW + timedelta(minutes=6), granularity=timedelta(minutes=5)
        )
        fresh = replace(session, last_used_at=None)
        assert fresh.should_record_use(NOW, granularity=timedelta(minutes=5))
        assert session.touched(at=NOW + timedelta(minutes=6)).last_used_at == NOW + timedelta(
            minutes=6
        )
