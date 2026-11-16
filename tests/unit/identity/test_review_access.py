"""L'accès de démonstration des revues d'Apple et de Google (ADR-0039).

Une seule adresse, un code fixe et réutilisable, aucun courriel envoyé ; tout
le reste du parcours — expiration, essais comptés, autres adresses — inchangé.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from doubles import SAMPLE_CONSENT as CONSENT
from doubles import IdentityEnv
from karria.modules.identity.application import (
    ResendEmailLoginCode,
    StartEmailLogin,
    VerifyEmailLoginCode,
)
from karria.modules.identity.domain import (
    LoginChallengeExpiredError,
    LoginCodeInvalidError,
    ReviewAccess,
    TooManyAttemptsError,
)
from karria.modules.identity.infrastructure import InMemoryLoginThrottle
from karria.shared.config import ReviewSettings

REVIEW = ReviewAccess(email="appreview@karria.fr", code="482913")


def _start(env: IdentityEnv, review: ReviewAccess | None = REVIEW) -> StartEmailLogin:
    return StartEmailLogin(
        users=env.users,
        challenges=env.challenges,
        sender=env.sender,
        tokens=env.tokens,
        throttle=env.throttle,
        clock=env.clock,
        uow=env.uow,
        review=review,
    )


def _resend(env: IdentityEnv) -> ResendEmailLoginCode:
    return ResendEmailLoginCode(
        challenges=env.challenges,
        sender=env.sender,
        tokens=env.tokens,
        throttle=env.throttle,
        clock=env.clock,
        uow=env.uow,
        review=REVIEW,
    )


class TestReviewAccess:
    def test_rien_n_est_ouvert_sans_adresse(self) -> None:
        assert ReviewAccess.of("", "") is None
        assert ReviewAccess.of("   ", "123456") is None

    def test_l_adresse_est_normalisee_et_le_code_a_la_forme_d_un_code(self) -> None:
        access = ReviewAccess.of("  AppReview@Karria.FR ", "482913")

        assert access == REVIEW
        assert access is not None
        assert access.matches("appreview@karria.fr")
        assert not access.matches("marc@exemple.fr")

    @pytest.mark.parametrize(
        ("email", "code"), [("pas-une-adresse", "482913"), ("a@b.fr", "12ab56"), ("a@b.fr", "1234")]
    )
    def test_une_configuration_fautive_est_refusee(self, email: str, code: str) -> None:
        with pytest.raises(ValueError, match="démonstration"):
            ReviewAccess.of(email, code)

    def test_le_reglage_refuse_un_code_mal_forme(self) -> None:
        with pytest.raises(ValueError, match="six chiffres"):
            ReviewSettings(email="appreview@karria.fr", code="12")
        assert ReviewSettings().email == ""


class TestParcours:
    async def test_le_code_fixe_ouvre_le_compte_sans_aucun_courriel_et_se_reutilise(
        self, env: IdentityEnv, verify: VerifyEmailLoginCode
    ) -> None:
        start = _start(env)

        first = await start.execute(email="AppReview@karria.fr", consent=CONSENT, throttle_key="k")
        opened = await verify.execute(
            challenge_id=first.challenge_id, code="482913", throttle_key="k"
        )
        again = await start.execute(email="appreview@karria.fr", consent=CONSENT, throttle_key="k")
        reopened = await verify.execute(
            challenge_id=again.challenge_id, code="482913", throttle_key="k"
        )

        assert env.sender.sent == []
        assert opened.created_account
        assert not reopened.created_account
        assert reopened.user.id == opened.user.id

    async def test_le_code_faux_compte_toujours_comme_un_essai(
        self, env: IdentityEnv, verify: VerifyEmailLoginCode
    ) -> None:
        started = await _start(env).execute(
            email="appreview@karria.fr", consent=CONSENT, throttle_key="k"
        )

        with pytest.raises(LoginCodeInvalidError):
            await verify.execute(challenge_id=started.challenge_id, code="000000", throttle_key="k")

    async def test_le_parcours_expire_comme_les_autres(
        self, env: IdentityEnv, verify: VerifyEmailLoginCode
    ) -> None:
        started = await _start(env).execute(
            email="appreview@karria.fr", consent=CONSENT, throttle_key="k"
        )
        env.clock.advance(timedelta(hours=2))

        with pytest.raises(LoginChallengeExpiredError):
            await verify.execute(challenge_id=started.challenge_id, code="482913", throttle_key="k")

    async def test_toute_autre_adresse_recoit_son_vrai_code(self, env: IdentityEnv) -> None:
        await _start(env).execute(email="marc@exemple.fr", consent=CONSENT, throttle_key="k")

        [(to, code, _)] = env.sender.sent
        assert to == "marc@exemple.fr"
        assert len(code) == 6
        assert code.isdigit()

    async def test_le_renvoi_garde_le_code_fixe_et_n_envoie_rien(
        self, env: IdentityEnv, verify: VerifyEmailLoginCode
    ) -> None:
        started = await _start(env).execute(
            email="appreview@karria.fr", consent=CONSENT, throttle_key="k"
        )
        env.clock.advance(timedelta(minutes=2))

        await _resend(env).execute(challenge_id=started.challenge_id, throttle_key="k")
        outcome = await verify.execute(
            challenge_id=started.challenge_id, code="482913", throttle_key="k"
        )

        assert env.sender.sent == []
        assert outcome.created_account

    async def test_sans_acces_configure_l_adresse_de_demo_est_une_adresse_comme_une_autre(
        self, env: IdentityEnv
    ) -> None:
        await _start(env, review=None).execute(
            email="appreview@karria.fr", consent=CONSENT, throttle_key="k"
        )

        assert [to for to, _, _ in env.sender.sent] == ["appreview@karria.fr"]

    async def test_un_frein_global_arrete_la_devinette_du_code_toutes_origines_confondues(
        self, env: IdentityEnv
    ) -> None:
        frein = InMemoryLoginThrottle(max_attempts=2, window_seconds=3600)
        verify = VerifyEmailLoginCode(
            users=env.users,
            challenges=env.challenges,
            sessions=env.sessions,
            tokens=env.tokens,
            throttle=env.throttle,
            clock=env.clock,
            uow=env.uow,
            review=REVIEW,
            review_throttle=frein,
        )
        start = _start(env)
        for origine in ("a", "b"):
            started = await start.execute(
                email="appreview@karria.fr", consent=CONSENT, throttle_key=origine
            )
            with pytest.raises(LoginCodeInvalidError):
                await verify.execute(
                    challenge_id=started.challenge_id, code="000000", throttle_key=origine
                )
        started = await start.execute(
            email="appreview@karria.fr", consent=CONSENT, throttle_key="c"
        )

        # Saturé : même le bon code attend la fin de la fenêtre.
        with pytest.raises(TooManyAttemptsError):
            await verify.execute(challenge_id=started.challenge_id, code="482913", throttle_key="c")
