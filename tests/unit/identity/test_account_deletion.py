"""Supprimer son compte : tout part, une trace anonyme reste (ADR-0015)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from doubles import SAMPLE_CONSENT as CONSENT
from doubles import IdentityEnv
from karria.modules.identity.api.dependencies import (
    get_departure_repository,
    get_main_device_lookup,
)
from karria.modules.identity.api.schemas import DeleteAccountRequest
from karria.modules.identity.application import (
    AccountDeleted,
    DeleteAccount,
    LoginOutcome,
    MainDeviceSnapshot,
    ResolveSession,
    StartEmailLogin,
    VerifyEmailLoginCode,
)
from karria.modules.identity.domain import (
    DEPARTURE_DETAILS_MAX_LENGTH,
    DepartureDetailsInvalidError,
    DepartureDetailsTooLongError,
    DepartureFeedback,
    DepartureReason,
    DepartureReasonInvalidError,
    EmailAddress,
    ProviderIdentity,
    SocialProvider,
    UserNotFoundError,
    account_age_in_days,
    normalize_details,
    open_challenge,
    scrub_personal_data,
)
from karria.modules.identity.infrastructure import (
    TortoiseDepartureFeedbackRepository,
    UnknownMainDeviceLookup,
)

pytestmark = pytest.mark.unit

EMAIL = "partante@exemple.fr"
FEEDBACK = DepartureFeedback(reason=DepartureReason.FOUND_JOB, details="Merci !")


async def _login(start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv) -> UUID:
    opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
    outcome: LoginOutcome = await verify.execute(
        challenge_id=opened.challenge_id, code=env.sender.last_code_for(EMAIL), throttle_key="k"
    )
    return outcome.user.id


class TestDomaine:
    def test_le_mot_libre_est_debarrasse_de_ses_blancs(self) -> None:
        assert normalize_details("  un mot  ") == "un mot"

    @pytest.mark.parametrize("raw", [None, "", "   \n\t"])
    def test_un_mot_vide_devient_none(self, raw: str | None) -> None:
        assert normalize_details(raw) is None

    def test_le_plafond_se_mesure_sans_les_blancs(self) -> None:
        juste = "a" * DEPARTURE_DETAILS_MAX_LENGTH
        assert normalize_details(f"  {juste}  ") == juste

        with pytest.raises(DepartureDetailsTooLongError) as exc:
            normalize_details("a" * (DEPARTURE_DETAILS_MAX_LENGTH + 1))
        assert exc.value.details == {
            "reason": "account_deletion_details_too_long",
            "field": "details",
            "max_length": DEPARTURE_DETAILS_MAX_LENGTH,
        }

    def test_un_mot_qui_nest_pas_un_texte_est_refuse(self) -> None:
        with pytest.raises(DepartureDetailsInvalidError) as exc:
            normalize_details(42)
        assert exc.value.details == {
            "reason": "account_deletion_details_invalid",
            "field": "details",
        }

    @pytest.mark.parametrize(
        ("texte", "attendu"),
        [
            ("Écrivez-moi à marc.t@exemple.fr merci", "Écrivez-moi à [e-mail] merci"),
            ("Mon numéro : 06 12 34 56 78.", "Mon numéro : [téléphone]."),
            (
                "Appelez le +33 6 12 34 56 78 ou le 0033612345678",
                "Appelez le [téléphone] ou le [téléphone]",
            ),
            (
                "Trop de notifs, 3 par jour, depuis 2026.",
                "Trop de notifs, 3 par jour, depuis 2026.",
            ),
            ("Rien pour moi en Bretagne.", "Rien pour moi en Bretagne."),
        ],
    )
    def test_les_adresses_et_numeros_sont_expurges(self, texte: str, attendu: str) -> None:
        assert scrub_personal_data(texte) == attendu

    def test_le_plafond_se_mesure_avant_lexpurgation(self) -> None:
        numero = "0612345678 "
        texte = numero + "a" * (DEPARTURE_DETAILS_MAX_LENGTH - len(numero))
        attendu = "[téléphone] " + "a" * (DEPARTURE_DETAILS_MAX_LENGTH - len(numero))
        assert len(attendu) > DEPARTURE_DETAILS_MAX_LENGTH
        assert normalize_details(texte) == attendu

    def test_parse_normalise_le_mot_et_reconnait_la_raison(self) -> None:
        feedback = DepartureFeedback.parse(reason="other", details="  ")
        assert feedback == DepartureFeedback(reason=DepartureReason.OTHER, details=None)

    def test_une_raison_hors_contrat_est_refusee(self) -> None:
        with pytest.raises(DepartureReasonInvalidError) as exc:
            DepartureFeedback.parse(reason="bored", details=None)
        assert exc.value.details == {"reason": "account_deletion_reason_invalid", "field": "reason"}

    def test_lage_du_compte_est_en_jours_entiers_jamais_negatif(self) -> None:
        created = datetime(2026, 1, 1, tzinfo=UTC)
        assert (
            account_age_in_days(created_at=created, at=created + timedelta(days=3, hours=23)) == 3
        )
        assert account_age_in_days(created_at=created, at=created - timedelta(days=2)) == 0

    def test_les_raisons_sont_celles_du_contrat(self) -> None:
        assert [r.value for r in DepartureReason] == [
            "found_job",
            "no_matching_offers",
            "not_useful",
            "too_many_notifications",
            "privacy",
            "other",
        ]


class TestDeleteAccount:
    async def test_efface_tout_et_garde_une_trace_anonyme(
        self,
        start: StartEmailLogin,
        verify: VerifyEmailLoginCode,
        delete_account: DeleteAccount,
        resolve: ResolveSession,
        env: IdentityEnv,
    ) -> None:
        user_id = await _login(start, verify, env)
        # Une seconde session, un rattachement Google, un parcours en cours.
        await _login(start, verify, env)
        await env.identities.add(
            ProviderIdentity(
                id=uuid4(),
                user_id=user_id,
                provider=SocialProvider.GOOGLE,
                subject="sub-1",
                email=EMAIL,
                created_at=env.clock.now(),
                last_used_at=env.clock.now(),
            )
        )
        pending = open_challenge(
            challenge_id=uuid4(),
            email=EMAIL,
            user_id=user_id,
            code_hash="x",
            terms_version="v",
            privacy_policy_version="v",
            at=env.clock.now(),
        )
        await env.challenges.add(pending)
        # Le parcours d'un autre compte, lui, ne doit pas bouger.
        autre = open_challenge(
            challenge_id=uuid4(),
            email="autre@exemple.fr",
            user_id=None,
            code_hash="y",
            terms_version="v",
            privacy_policy_version="v",
            at=env.clock.now(),
        )
        await env.challenges.add(autre)
        env.main_device.snapshot = MainDeviceSnapshot(platform="ios", app_version="2.1.0")
        env.clock.advance(timedelta(days=12, hours=5))

        await delete_account.execute(user_id=user_id, feedback=FEEDBACK)

        assert user_id not in env.users.rows
        assert not [s for s in env.sessions.rows.values() if s.user_id == user_id]
        assert not await env.identities.list_for(user_id)
        assert set(env.challenges.rows) == {autre.id}
        assert await env.users.find_by_email(EmailAddress(EMAIL)) is None

        [record] = env.departures.rows
        assert record.reason is DepartureReason.FOUND_JOB
        assert record.details == "Merci !"
        assert record.account_age_days == 12
        assert record.platform == "ios"
        assert record.app_version == "2.1.0"
        assert record.recorded_at == env.clock.now()
        assert not hasattr(record, "user_id")
        assert not hasattr(record, "email")
        assert env.main_device.calls == [user_id]

        [event] = env.events.of_type(AccountDeleted)
        assert event.user_id == user_id

    async def test_sans_appareil_connu_la_trace_reste_muette_sur_la_plateforme(
        self,
        start: StartEmailLogin,
        verify: VerifyEmailLoginCode,
        delete_account: DeleteAccount,
        env: IdentityEnv,
    ) -> None:
        user_id = await _login(start, verify, env)

        await delete_account.execute(
            user_id=user_id, feedback=DepartureFeedback(reason=DepartureReason.PRIVACY)
        )

        [record] = env.departures.rows
        assert (record.platform, record.app_version, record.details) == (None, None, None)
        assert record.account_age_days == 0

    async def test_un_compte_disparu_est_refuse_sans_rien_ecrire(
        self, delete_account: DeleteAccount, env: IdentityEnv
    ) -> None:
        with pytest.raises(UserNotFoundError):
            await delete_account.execute(user_id=uuid4(), feedback=FEEDBACK)

        assert env.departures.rows == []
        assert env.events.of_type(AccountDeleted) == []

    async def test_la_meme_adresse_se_reinscrit_comme_une_premiere_venue(
        self,
        start: StartEmailLogin,
        verify: VerifyEmailLoginCode,
        delete_account: DeleteAccount,
        env: IdentityEnv,
    ) -> None:
        ancien = await _login(start, verify, env)
        await delete_account.execute(user_id=ancien, feedback=FEEDBACK)

        opened = await start.execute(email=EMAIL, consent=CONSENT, throttle_key="k")
        outcome = await verify.execute(
            challenge_id=opened.challenge_id,
            code=env.sender.last_code_for(EMAIL),
            throttle_key="k",
        )

        assert outcome.created_account is True
        assert outcome.user.id != ancien

    async def test_sans_bus_la_suppression_aboutit_quand_meme(
        self, start: StartEmailLogin, verify: VerifyEmailLoginCode, env: IdentityEnv
    ) -> None:
        user_id = await _login(start, verify, env)
        muet = DeleteAccount(
            users=env.users,
            identities=env.identities,
            sessions=env.sessions,
            challenges=env.challenges,
            departures=env.departures,
            main_device=env.main_device,
            clock=env.clock,
            uow=env.uow,
        )

        await muet.execute(user_id=user_id, feedback=FEEDBACK)

        assert user_id not in env.users.rows
        assert len(env.departures.rows) == 1


class TestSchema:
    def test_projette_un_avis_normalise(self) -> None:
        body = DeleteAccountRequest.model_validate({"reason": "other", "details": "  bof  "})
        assert body.feedback() == DepartureFeedback(reason=DepartureReason.OTHER, details="bof")

    def test_details_est_facultatif(self) -> None:
        body = DeleteAccountRequest.model_validate({"reason": "privacy"})
        assert body.feedback().details is None

    def test_une_raison_hors_contrat_est_une_erreur_du_domaine(self) -> None:
        body = DeleteAccountRequest.model_validate({"reason": "bored", "details": None})
        with pytest.raises(DepartureReasonInvalidError):
            body.feedback()

    def test_une_raison_absurde_est_refusee_par_la_forme(self) -> None:
        with pytest.raises(ValidationError):
            DeleteAccountRequest.model_validate({"reason": "x" * 65})

    def test_un_mot_qui_nest_pas_un_texte_est_une_erreur_du_domaine(self) -> None:
        body = DeleteAccountRequest.model_validate({"reason": "other", "details": ["liste"]})
        with pytest.raises(DepartureDetailsInvalidError):
            body.feedback()

    def test_un_mot_trop_long_est_une_erreur_du_domaine(self) -> None:
        body = DeleteAccountRequest.model_validate(
            {"reason": "other", "details": "a" * (DEPARTURE_DETAILS_MAX_LENGTH + 1)}
        )
        with pytest.raises(DepartureDetailsTooLongError):
            body.feedback()


class TestCablage:
    async def test_sans_branchement_lappareil_principal_est_inconnu(self) -> None:
        lookup = get_main_device_lookup()
        assert isinstance(lookup, UnknownMainDeviceLookup)
        assert await lookup.main_device_of(uuid4()) is None

    def test_la_trace_de_depart_va_dans_tortoise(self) -> None:
        assert isinstance(get_departure_repository(), TortoiseDepartureFeedbackRepository)
