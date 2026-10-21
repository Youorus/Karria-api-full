"""Le parcours par code e-mail : démarrer, renvoyer, valider.

Ce qui est garanti ici : la réponse ne dit pas si un compte existe, le
code ne circule jamais en clair hors de l'e-mail, cinq essais brûlent le
code, et la validation crée le compte avec son consentement.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from doubles import SAMPLE_CONSENT as CONSENT
from doubles import IdentityEnv
from karria.modules.identity.application import (
    ResendEmailLoginCode,
    StartEmailLogin,
    UserRegistered,
    UserSignedIn,
    VerifyEmailLoginCode,
)
from karria.modules.identity.domain import (
    LOGIN_CODE_RESEND_DELAY,
    MAX_CODE_ATTEMPTS,
    InvalidEmailError,
    LoginChallengeExpiredError,
    LoginCodeExhaustedError,
    LoginCodeInvalidError,
    LoginCodeResendTooSoonError,
    TooManyAttemptsError,
)
from karria.shared.i18n import Language

pytestmark = pytest.mark.unit

EMAIL = "Marc@Exemple.fr"


class TestStart:
    async def test_envoie_un_code_et_rend_le_parcours(
        self, start: StartEmailLogin, env: IdentityEnv
    ) -> None:
        result = await start.execute(
            email=EMAIL, consent=CONSENT, throttle_key="k", language=Language.EN
        )

        assert result.sent_to == "m•••@exemple.fr"
        assert result.expires_in == 600
        assert result.seconds_before_resend == 30
        to, code, language = env.sender.sent[0]
        assert to == "marc@exemple.fr"
        assert len(code) == 6
        assert code.isdigit()
        assert language is Language.EN
        # Le code n'est stocké que haché.
        challenge = env.challenges.rows[result.challenge_id]
        assert challenge.code_hash != code
        assert env.tokens.matches(code, challenge.code_hash)

    async def test_repond_pareil_pour_une_adresse_connue(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        first = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        await verify.execute(
            challenge_id=first.challenge_id,
            code=env.sender.last_code_for("marc@exemple.fr"),
            throttle_key="k",
        )

        again = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")

        assert again.sent_to == first.sent_to
        assert again.expires_in == first.expires_in
        assert env.challenges.rows[again.challenge_id].user_id is not None

    async def test_refuse_une_adresse_invalide_avant_tout_envoi(
        self, start: StartEmailLogin, env: IdentityEnv
    ) -> None:
        with pytest.raises(InvalidEmailError):
            await start.execute(email="pas-une-adresse", consent=CONSENT, throttle_key="k")
        assert env.sender.sent == []

    async def test_est_borne_par_le_limiteur(
        self, start: StartEmailLogin, env: IdentityEnv
    ) -> None:
        env.throttle.blocked.add("k")
        with pytest.raises(TooManyAttemptsError) as exc:
            await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        assert exc.value.details["retry_after"] == 42
        assert env.sender.sent == []


class TestResend:
    async def test_renvoie_un_nouveau_code_sur_le_meme_parcours(
        self, start: StartEmailLogin, resend: ResendEmailLoginCode, env: IdentityEnv
    ) -> None:
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        first_code = env.sender.last_code_for("marc@exemple.fr")
        env.clock.advance(LOGIN_CODE_RESEND_DELAY)

        result = await resend.execute(challenge_id=opened.challenge_id, throttle_key="k")

        assert result.challenge_id == opened.challenge_id
        assert result.expires_in == 570
        assert len(env.sender.sent) == 2
        challenge = env.challenges.rows[opened.challenge_id]
        assert not env.tokens.matches(first_code, challenge.code_hash)

    async def test_refuse_trop_tot(
        self, start: StartEmailLogin, resend: ResendEmailLoginCode, env: IdentityEnv
    ) -> None:
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        env.clock.advance(timedelta(seconds=5))

        with pytest.raises(LoginCodeResendTooSoonError) as exc:
            await resend.execute(challenge_id=opened.challenge_id, throttle_key="k")
        assert exc.value.details["retry_after"] == 25
        assert len(env.sender.sent) == 1

    async def test_un_parcours_inconnu_est_perime(self, resend: ResendEmailLoginCode) -> None:
        with pytest.raises(LoginChallengeExpiredError):
            await resend.execute(challenge_id=uuid4(), throttle_key="k")

    async def test_est_borne_par_le_limiteur(
        self, resend: ResendEmailLoginCode, env: IdentityEnv
    ) -> None:
        env.throttle.blocked.add("k")
        with pytest.raises(TooManyAttemptsError):
            await resend.execute(challenge_id=uuid4(), throttle_key="k")


class TestVerify:
    async def test_cree_le_compte_avec_son_consentement_et_ouvre_la_session(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")

        outcome = await verify.execute(
            challenge_id=opened.challenge_id,
            code=env.sender.last_code_for("marc@exemple.fr"),
            throttle_key="k",
            user_agent="Karria/2.0",
        )

        assert outcome.created_account
        assert outcome.user.email.value == "marc@exemple.fr"
        assert outcome.user.consent.terms_version == "2026-10"
        assert outcome.user.consent.accepted_at == env.clock.now()
        assert outcome.user.email_verified_at == env.clock.now()
        assert outcome.session.access_token != outcome.session.refresh_token
        session = env.sessions.rows[outcome.session.session_id]
        assert session.user_agent == "Karria/2.0"
        assert session.access_token_hash == env.tokens.fingerprint(outcome.session.access_token)
        assert env.challenges.rows[opened.challenge_id].created_account
        assert env.throttle.resets == ["k"]
        assert [e.user_id for e in env.events.of_type(UserRegistered)] == [outcome.user.id]
        signed = env.events.of_type(UserSignedIn)
        assert signed[0].created_account

    async def test_reconnait_un_compte_existant_et_renouvelle_le_consentement(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        first = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        created = await verify.execute(
            challenge_id=first.challenge_id,
            code=env.sender.last_code_for("marc@exemple.fr"),
            throttle_key="k",
        )
        env.clock.advance(timedelta(days=40))
        newer = CONSENT.__class__(terms_version="2026-12", privacy_policy_version="2026-10")
        second = await start.execute(email=EMAIL, consent=newer, throttle_key="k")

        outcome = await verify.execute(
            challenge_id=second.challenge_id,
            code=env.sender.last_code_for("marc@exemple.fr"),
            throttle_key="k",
        )

        assert not outcome.created_account
        assert outcome.user.id == created.user.id
        assert outcome.user.consent.terms_version == "2026-12"
        assert outcome.user.last_login_at == env.clock.now()
        assert len(env.users.rows) == 1
        assert len(env.events.of_type(UserRegistered)) == 1

    async def test_un_code_faux_compte_un_essai_et_dit_ce_quil_reste(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")

        with pytest.raises(LoginCodeInvalidError) as exc:
            await verify.execute(challenge_id=opened.challenge_id, code="000000", throttle_key="k")

        assert exc.value.details == {
            "reason": "auth_code_invalid",
            "field": "code",
            "remaining_attempts": MAX_CODE_ATTEMPTS - 1,
        }
        assert env.throttle.failures[-1] == "k"
        assert env.sessions.rows == {}

    async def test_cinq_codes_faux_brulent_le_parcours(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        good = env.sender.last_code_for("marc@exemple.fr")
        for _ in range(MAX_CODE_ATTEMPTS - 1):
            with pytest.raises(LoginCodeInvalidError):
                await verify.execute(challenge_id=opened.challenge_id, code="1", throttle_key="k")

        with pytest.raises(LoginCodeExhaustedError):
            await verify.execute(challenge_id=opened.challenge_id, code="1", throttle_key="k")
        # Même le bon code ne passe plus : le parcours est clos.
        with pytest.raises(LoginChallengeExpiredError):
            await verify.execute(challenge_id=opened.challenge_id, code=good, throttle_key="k")

    async def test_un_parcours_expire_refuse_le_bon_code(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        env.clock.advance(timedelta(minutes=11))

        with pytest.raises(LoginChallengeExpiredError):
            await verify.execute(
                challenge_id=opened.challenge_id,
                code=env.sender.last_code_for("marc@exemple.fr"),
                throttle_key="k",
            )

    async def test_un_parcours_inconnu_est_perime(self, verify: VerifyEmailLoginCode) -> None:
        with pytest.raises(LoginChallengeExpiredError):
            await verify.execute(challenge_id=uuid4(), code="123456", throttle_key="k")

    async def test_est_borne_par_le_limiteur(
        self, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        env.throttle.blocked.add("k")
        with pytest.raises(TooManyAttemptsError):
            await verify.execute(challenge_id=uuid4(), code="123456", throttle_key="k")

    async def test_le_code_est_compare_apres_nettoyage_des_blancs(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        code = env.sender.last_code_for("marc@exemple.fr")

        outcome = await verify.execute(
            challenge_id=opened.challenge_id, code=f" {code} ", throttle_key="k"
        )

        assert outcome.created_account

    async def test_sans_bus_rien_nest_publie_et_tout_fonctionne(self, env: IdentityEnv) -> None:
        verify = VerifyEmailLoginCode(
            users=env.users,
            challenges=env.challenges,
            sessions=env.sessions,
            tokens=env.tokens,
            throttle=env.throttle,
            clock=env.clock,
            uow=env.uow,
        )
        start = StartEmailLogin(
            users=env.users,
            challenges=env.challenges,
            sender=env.sender,
            tokens=env.tokens,
            throttle=env.throttle,
            clock=env.clock,
            uow=env.uow,
        )
        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")

        outcome = await verify.execute(
            challenge_id=opened.challenge_id,
            code=env.sender.last_code_for("marc@exemple.fr"),
            throttle_key="k",
        )

        assert outcome.created_account
        assert env.events.published == []
