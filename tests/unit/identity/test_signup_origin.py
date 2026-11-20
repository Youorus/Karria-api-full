"""D'où vient un compte : la région et le fuseau du téléphone, écrits une fois (ADR-0042).

Ce qui est garanti ici : la forme est stricte et une valeur hors forme est
ignorée sans jamais coûter une connexion ; l'origine s'écrit à la création
du compte, sinon à la première occasion ; elle n'est jamais écrasée.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from doubles import SAMPLE_CONSENT as CONSENT
from doubles import IdentityEnv, StubSocialVerifier
from karria.modules.identity.api.schemas import (
    AppleLoginRequest,
    EmailVerifyRequest,
    GoogleLoginRequest,
)
from karria.modules.identity.application import (
    LoginOutcome,
    RecordSignupOrigin,
    SocialLogin,
    StartEmailLogin,
    UserRegistered,
    VerifyEmailLoginCode,
)
from karria.modules.identity.domain import (
    SIGNUP_TIMEZONE_MAX_LENGTH,
    UNKNOWN_ORIGIN,
    SignupOrigin,
    SocialProvider,
    VerifiedSocialProfile,
    parse_signup_region,
    parse_signup_timezone,
)

pytestmark = pytest.mark.unit

EMAIL = "awa@exemple.fr"
BELGIUM = SignupOrigin(region="BE", timezone="Europe/Brussels")
CANADA = SignupOrigin(region="CA", timezone="America/Toronto")
GOOGLE_PROFILE = VerifiedSocialProfile(
    provider=SocialProvider.GOOGLE, subject="1155", email=EMAIL, email_verified=True
)


class TestLaForme:
    @pytest.mark.parametrize("raw", ["FR", "BE", "CI", "XK"])
    def test_deux_lettres_majuscules_sont_une_region(self, raw: str) -> None:
        assert parse_signup_region(raw) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "fr",
            "Fr",
            "FRA",
            "F",
            "",
            " FR",
            "FR ",
            "FR\n",
            "419",
            "F1",
            "ÉT",
            "ZZ",
            None,
            33,
            ["FR"],
        ],
    )
    def test_toute_autre_forme_est_ignoree_sans_reparation(self, raw: object) -> None:
        assert parse_signup_region(raw) is None

    @pytest.mark.parametrize(
        "raw",
        ["Europe/Brussels", "America/Argentina/Buenos_Aires", "UTC", "Etc/GMT+1", "Africa/Abidjan"],
    )
    def test_un_nom_iana_est_un_fuseau(self, raw: str) -> None:
        assert parse_signup_timezone(raw) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "Europe/",
            "/Paris",
            "Europe/Paris/Sud/Est",
            "Europe Paris",
            "Europe/Paris\n",
            "<script>",
            "+02:00",
            "E" * (SIGNUP_TIMEZONE_MAX_LENGTH + 1),
            None,
            120,
        ],
    )
    def test_un_fuseau_hors_forme_ou_trop_long_est_ignore(self, raw: object) -> None:
        assert parse_signup_timezone(raw) is None

    def test_chaque_valeur_se_lit_seule(self) -> None:
        origin = SignupOrigin.parse(region="be", timezone="Europe/Brussels")

        assert origin == SignupOrigin(region=None, timezone="Europe/Brussels")
        assert not origin.is_empty
        assert not origin.is_complete
        assert SignupOrigin.parse(region=None, timezone=None).is_empty

    def test_completer_ne_remplace_jamais_ce_qui_est_connu(self) -> None:
        assert BELGIUM.completed_by(CANADA) == BELGIUM
        assert SignupOrigin(region="BE").completed_by(CANADA) == SignupOrigin(
            region="BE", timezone="America/Toronto"
        )
        assert UNKNOWN_ORIGIN.completed_by(CANADA) == CANADA


class TestLeContratHttp:
    def test_les_trois_requetes_qui_creent_un_compte_portent_region_et_fuseau(self) -> None:
        origin = {"region": "BE", "timezone": "Europe/Brussels"}
        consent = {"terms_version": "2026-10", "privacy_policy_version": "2026-10"}

        verify = EmailVerifyRequest.model_validate(
            {"challenge_id": str(uuid4()), "code": "123456", **origin}
        )
        google = GoogleLoginRequest.model_validate({"id_token": "jeton", **consent, **origin})
        apple = AppleLoginRequest.model_validate({"identity_token": "jeton", **consent, **origin})

        assert verify.origin() == google.origin() == apple.origin() == BELGIUM

    def test_une_app_ancienne_qui_n_envoie_rien_reste_acceptee(self) -> None:
        body = EmailVerifyRequest.model_validate({"challenge_id": str(uuid4()), "code": "123456"})

        assert body.origin() == UNKNOWN_ORIGIN

    @pytest.mark.parametrize(
        ("region", "timezone"),
        [("be", "Europe/Brussels\n"), (419, 3600), ("Belgique", "x" * 500), ({"a": 1}, ["UTC"])],
    )
    def test_une_valeur_hors_forme_est_ignoree_et_ne_refuse_jamais_la_connexion(
        self, region: object, timezone: object
    ) -> None:
        body = EmailVerifyRequest.model_validate(
            {"challenge_id": str(uuid4()), "code": "123456", "region": region, "timezone": timezone}
        )

        assert body.origin() == UNKNOWN_ORIGIN


async def _by_code(
    start: StartEmailLogin,
    verify: VerifyEmailLoginCode,
    env: IdentityEnv,
    origin: SignupOrigin = UNKNOWN_ORIGIN,
) -> LoginOutcome:
    opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
    return await verify.execute(
        challenge_id=opened.challenge_id,
        code=env.sender.last_code_for(EMAIL),
        throttle_key="k",
        origin=origin,
    )


def _social(env: IdentityEnv) -> SocialLogin:
    return SocialLogin(
        verifier=StubSocialVerifier(SocialProvider.GOOGLE, GOOGLE_PROFILE),
        identities=env.identities,
        users=env.users,
        sessions=env.sessions,
        tokens=env.tokens,
        clock=env.clock,
        uow=env.uow,
        events=env.events,
    )


class TestALaCreationDuCompte:
    async def test_par_code_l_origine_est_ecrite_avec_le_compte(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        seen: list[SignupOrigin] = []

        async def read_account(event: UserRegistered) -> None:
            # Ce que l'alerte d'inscription lira : l'origine est déjà là.
            seen.append(env.users.rows[event.user_id].signup_origin)

        env.events.subscribe(UserRegistered, read_account)

        outcome = await _by_code(start, verify, env, BELGIUM)

        assert outcome.created_account is True
        assert outcome.user.signup_origin == BELGIUM
        assert env.users.rows[outcome.user.id].signup_origin == BELGIUM
        assert seen == [BELGIUM]

    async def test_par_apple_ou_google_aussi(self, env: IdentityEnv) -> None:
        outcome = await _social(env).execute(token="t", nonce=None, consent=CONSENT, origin=BELGIUM)

        assert outcome.created_account is True
        assert env.users.rows[outcome.user.id].signup_origin == BELGIUM

    async def test_sans_origine_le_compte_nait_quand_meme(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        outcome = await _by_code(start, verify, env)

        assert outcome.created_account is True
        assert env.users.rows[outcome.user.id].signup_origin == UNKNOWN_ORIGIN


class TestALaPremiereOccasion:
    async def test_un_compte_sans_origine_la_recoit_a_la_connexion_suivante(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        first = await _by_code(start, verify, env)
        env.clock.advance(timedelta(minutes=2))

        again = await _by_code(start, verify, env, BELGIUM)

        assert again.created_account is False
        assert again.user.signup_origin == BELGIUM
        assert env.users.rows[first.user.id].signup_origin == BELGIUM
        # Un seul compte, une seule annonce : compléter n'est pas s'inscrire.
        assert len(env.events.of_type(UserRegistered)) == 1

    async def test_une_origine_enregistree_n_est_jamais_ecrasee(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        first = await _by_code(start, verify, env, BELGIUM)
        env.clock.advance(timedelta(minutes=2))

        # La même personne, en voyage, se reconnecte par code puis par Google.
        by_code = await _by_code(start, verify, env, CANADA)
        by_google = await _social(env).execute(
            token="t", nonce=None, consent=CONSENT, origin=CANADA
        )
        returning = await _social(env).execute(
            token="t", nonce=None, consent=CONSENT, origin=CANADA
        )

        assert by_code.user.signup_origin == BELGIUM
        assert by_google.user.id == returning.user.id == first.user.id
        assert returning.user.signup_origin == BELGIUM
        assert env.users.rows[first.user.id].signup_origin == BELGIUM

    async def test_ce_qui_manque_se_complete_sans_toucher_au_reste(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        first = await _by_code(start, verify, env, SignupOrigin(region="BE"))
        env.clock.advance(timedelta(minutes=2))

        await _by_code(start, verify, env, CANADA)

        assert env.users.rows[first.user.id].signup_origin == SignupOrigin(
            region="BE", timezone="America/Toronto"
        )

    async def test_un_retour_par_google_complete_un_compte_sans_origine(
        self, env: IdentityEnv
    ) -> None:
        login = _social(env)
        first = await login.execute(token="t", nonce=None, consent=CONSENT)

        again = await login.execute(token="t", nonce=None, consent=CONSENT, origin=BELGIUM)

        assert again.created_account is False
        assert env.users.rows[first.user.id].signup_origin == BELGIUM

    async def test_enregistrer_le_compte_ne_touche_pas_a_son_origine(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        """Une connexion qui a lu le compte avant le repli ne remet pas l'origine à vide."""
        outcome = await _by_code(start, verify, env)
        stale = outcome.user
        await env.users.fill_signup_origin(stale.id, BELGIUM)

        await env.users.save(stale.signed_in(at=env.clock.now()))

        assert env.users.rows[stale.id].signup_origin == BELGIUM


class TestLeRepliParLAppareil:
    """`RecordSignupOrigin` : ce que `bootstrap` appelle quand un appareil est déclaré."""

    @pytest.fixture
    def record(self, env: IdentityEnv) -> RecordSignupOrigin:
        return RecordSignupOrigin(users=env.users, uow=env.uow)

    async def test_un_compte_sans_origine_la_reprend_de_l_appareil(
        self,
        start: StartEmailLogin,
        verify: VerifyEmailLoginCode,
        env: IdentityEnv,
        record: RecordSignupOrigin,
    ) -> None:
        user = (await _by_code(start, verify, env)).user

        written = await record.execute(user.id, region="BE", timezone="Europe/Brussels")

        assert written is True
        assert env.users.rows[user.id].signup_origin == BELGIUM
        # Aucune seconde annonce : l'alerte d'inscription ne repart pas.
        assert len(env.events.of_type(UserRegistered)) == 1

    async def test_une_origine_deja_connue_n_est_pas_remplacee(
        self,
        start: StartEmailLogin,
        verify: VerifyEmailLoginCode,
        env: IdentityEnv,
        record: RecordSignupOrigin,
    ) -> None:
        user = (await _by_code(start, verify, env, BELGIUM)).user

        written = await record.execute(user.id, region="CA", timezone="America/Toronto")

        assert written is False
        assert env.users.rows[user.id].signup_origin == BELGIUM

    async def test_ce_que_l_appareil_declare_passe_par_la_meme_validation(
        self,
        start: StartEmailLogin,
        verify: VerifyEmailLoginCode,
        env: IdentityEnv,
        record: RecordSignupOrigin,
    ) -> None:
        user = (await _by_code(start, verify, env)).user

        # `PUT /devices` accepte cent caractères libres : rien de tout cela n'est un pays.
        assert await record.execute(user.id, region="fr", timezone="x" * 100) is False
        assert await record.execute(user.id, region=None, timezone=None) is False
        assert env.users.rows[user.id].signup_origin == UNKNOWN_ORIGIN

    async def test_un_compte_disparu_est_ignore(self, record: RecordSignupOrigin) -> None:
        assert await record.execute(uuid4(), region="BE", timezone="Europe/Brussels") is False
