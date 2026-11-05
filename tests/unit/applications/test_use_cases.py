"""Les cas d'usage des dossiers, avec des doublures : ouvrir, adapter, décider, écrire, candidater."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from doubles import (
    ApplicationsEnv,
    CollectingBus,
    FakeBaseCvReader,
    FakeClock,
    FakeCvAdapter,
    FakeLetterWriter,
    InMemoryDossierRepository,
    PermissiveCvValidator,
    RecordingDossierJobs,
    sample_letter_json,
    sample_offer_card,
)
from karria.modules.applications.application import (
    AdaptCv,
    ChangeDossierColor,
    ChangeDossierTemplate,
    CreateDossierFromOffer,
    DecideProposition,
    DeleteDossier,
    DossierApplied,
    DossierUnapplied,
    DraftLetter,
    ForgetUser,
    GetDossier,
    ListDossiers,
    MarkApplied,
    RegenerateParagraph,
    RetryAdaptation,
    UnmarkApplied,
    UpdateDossierCv,
    UpdateLetter,
    excerpt_of,
)
from karria.modules.applications.domain import (
    AdaptationInProgressError,
    AdaptationOutput,
    AdaptationState,
    BaseCvMissingError,
    CvInvalidError,
    Decision,
    Dossier,
    DossierConflictError,
    DossierNotFoundError,
    DossierStatus,
    Letter,
    LetterInvalidError,
    LetterMissingError,
    ModelOutputInvalidError,
    ModelUnavailableError,
    OfferNotFoundError,
    ParagraphNotFoundError,
    PropositionInvalidError,
    PropositionState,
    TemplateLockedError,
)

pytestmark = pytest.mark.unit


def adapt_of(env: ApplicationsEnv, *, inline_seconds: float = 10.0) -> AdaptCv:
    return AdaptCv(
        dossiers=env.dossiers,
        adapter=env.adapter,
        jobs=env.jobs,
        validator=env.validator,
        clock=env.clock,
        inline_seconds=inline_seconds,
    )


def create_of(env: ApplicationsEnv, **kwargs: float) -> CreateDossierFromOffer:
    return CreateDossierFromOffer(
        dossiers=env.dossiers,
        offers=env.offers,
        base_cvs=env.base_cvs,
        templates=env.templates,
        adapt=adapt_of(env, **kwargs),
        clock=env.clock,
    )


async def opened(env: ApplicationsEnv) -> tuple[Dossier, object]:
    user = uuid4()
    result = await create_of(env).execute(user, env.offer.offer_id, None)
    return result.dossier, user


class TestCreation:
    async def test_ouvre_copie_fige_et_adapte_dans_la_requete(self) -> None:
        env = ApplicationsEnv.build()
        user = uuid4()

        result = await create_of(env).execute(user, env.offer.offer_id, None)

        dossier = result.dossier
        assert result.created
        assert dossier.adaptation is AdaptationState.FAITE
        assert dossier.cv["identity"]["headline"] == "Conseillère de vente"
        assert dossier.offer.title == env.offer.title
        assert "conseillere de vente" in dossier.offer.keywords
        assert env.dossiers.rows[dossier.id] == dossier
        assert env.jobs.deferred == []
        assert env.adapter.calls[0][0] == dossier.offer

    async def test_une_seconde_demande_rend_l_existant_sans_relancer(self) -> None:
        env = ApplicationsEnv.build()
        user = uuid4()
        first = await create_of(env).execute(user, env.offer.offer_id, None)

        second = await create_of(env).execute(user, env.offer.offer_id, uuid4())

        assert not second.created
        assert second.dossier.id == first.dossier.id
        assert len(env.adapter.calls) == 1

    async def test_sans_cv_de_base(self) -> None:
        env = ApplicationsEnv.build()
        env.base_cvs = FakeBaseCvReader(None)

        with pytest.raises(BaseCvMissingError) as excinfo:
            await create_of(env).execute(uuid4(), env.offer.offer_id, None)
        assert excinfo.value.details["reason"] == "cv_missing"

    async def test_offre_inconnue_et_modele_verrouille(self) -> None:
        env = ApplicationsEnv.build()
        with pytest.raises(OfferNotFoundError):
            await create_of(env).execute(uuid4(), uuid4(), None)

        locked = uuid4()
        env.templates.locked.add(locked)
        with pytest.raises(TemplateLockedError):
            await create_of(env).execute(uuid4(), env.offer.offer_id, locked)
        assert env.dossiers.rows == {}

    async def test_un_modele_lent_laisse_le_dossier_en_cours_et_differe(self) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(delay=0.2)

        result = await create_of(env, inline_seconds=0.01).execute(
            uuid4(), env.offer.offer_id, None
        )

        assert result.dossier.adaptation is AdaptationState.EN_COURS
        assert env.jobs.deferred == [result.dossier.id]
        assert env.dossiers.rows[result.dossier.id].adaptation is AdaptationState.EN_COURS

    async def test_une_file_en_panne_marque_l_echec_sans_perdre_le_dossier(self) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(delay=0.2)
        env.jobs = RecordingDossierJobs(error=RuntimeError("file fermée"))

        result = await create_of(env, inline_seconds=0.01).execute(
            uuid4(), env.offer.offer_id, None
        )

        assert result.dossier.adaptation is AdaptationState.ECHOUEE
        assert result.dossier.adaptation_error == "queue_unavailable"

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (ModelUnavailableError("panne"), "ai_unavailable"),
            (ModelOutputInvalidError(), "ai_output_invalid"),
        ],
    )
    async def test_un_modele_en_panne_marque_l_echec(self, error: Exception, reason: str) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(error=error)

        result = await create_of(env).execute(uuid4(), env.offer.offer_id, None)

        assert result.dossier.adaptation is AdaptationState.ECHOUEE
        assert result.dossier.adaptation_error == reason
        assert result.dossier.status is DossierStatus.PRET


class TestAdaptationDifferee:
    async def test_le_worker_adapte_un_dossier_en_attente(self) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(delay=0.2)
        result = await create_of(env, inline_seconds=0.01).execute(
            uuid4(), env.offer.offer_id, None
        )
        env.adapter = FakeCvAdapter()

        adapted = await adapt_of(env).execute(result.dossier.id)

        assert adapted is not None
        assert adapted.adaptation is AdaptationState.FAITE
        assert env.dossiers.rows[result.dossier.id].adaptation is AdaptationState.FAITE

    async def test_le_worker_ignore_un_dossier_supprime_ou_deja_fait(self) -> None:
        env = ApplicationsEnv.build()
        assert await adapt_of(env).execute(uuid4()) is None
        dossier, _ = await opened(env)
        calls = len(env.adapter.calls)

        assert await adapt_of(env).execute(dossier.id) == dossier
        assert len(env.adapter.calls) == calls

    async def test_une_retouche_pendant_l_adaptation_n_est_pas_ecrasee(self) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(delay=0.2)
        result = await create_of(env, inline_seconds=0.01).execute(
            uuid4(), env.offer.offer_id, None
        )
        pending = env.dossiers.rows[result.dossier.id]
        touched = pending.with_cv(
            {**pending.cv, "summary": "Mon résumé à moi"}, now=env.clock.now()
        )
        env.dossiers.rows[pending.id] = touched
        env.adapter = FakeCvAdapter()

        adapted = await adapt_of(env).execute(pending.id)

        assert adapted is not None
        assert adapted.cv["summary"] != "Mon résumé à moi"  # l'à-propos est auto-appliqué…
        summary = next(p for p in adapted.propositions if p.path == "summary")
        assert (
            summary.before == "Mon résumé à moi"
        )  # … mais `before` est la retouche, pour y revenir
        assert adapted.adaptation is AdaptationState.FAITE

    async def test_la_relance_n_est_possible_qu_apres_un_echec(self) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(error=ModelUnavailableError("panne"))
        user = uuid4()
        failed = (await create_of(env).execute(user, env.offer.offer_id, None)).dossier
        env.adapter = FakeCvAdapter()

        retried = await RetryAdaptation(
            dossiers=env.dossiers, adapt=adapt_of(env), clock=env.clock
        ).execute(user, failed.id)

        assert retried.adaptation is AdaptationState.FAITE
        assert retried.propositions
        with pytest.raises(DossierNotFoundError):
            await RetryAdaptation(
                dossiers=env.dossiers, adapt=adapt_of(env), clock=env.clock
            ).execute(uuid4(), failed.id)


class TestLectureEtSuppression:
    async def test_get_dit_si_le_cv_de_base_a_change(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        get = GetDossier(env.dossiers, env.base_cvs)

        assert not (await get.execute(user, dossier.id)).base_changed
        env.base_cvs.data = {**env.base_cvs.data, "summary": "changé"}  # type: ignore[dict-item]
        assert (await get.execute(user, dossier.id)).base_changed
        with pytest.raises(DossierNotFoundError):
            await get.execute(uuid4(), dossier.id)

    async def test_list_trie_et_filtre_les_candidatures(self) -> None:
        env = ApplicationsEnv.build()
        user = uuid4()
        other = sample_offer_card()
        env.offers.cards[other.offer_id] = other
        first = (await create_of(env).execute(user, env.offer.offer_id, None)).dossier
        second = (await create_of(env).execute(user, other.offer_id, None)).dossier
        bus = CollectingBus()
        await MarkApplied(env.dossiers, bus, env.clock).execute(user, first.id)

        everything = await ListDossiers(env.dossiers).execute(user, applied_only=False)
        applied = await ListDossiers(env.dossiers).execute(user, applied_only=True)

        assert {d.id for d in everything} == {first.id, second.id}
        assert [d.id for d in applied] == [first.id]
        assert await ListDossiers(env.dossiers).execute(uuid4(), applied_only=False) == []

    async def test_delete_et_forget(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        with pytest.raises(DossierNotFoundError):
            await DeleteDossier(env.dossiers).execute(uuid4(), dossier.id)

        await DeleteDossier(env.dossiers).execute(user, dossier.id)
        assert env.dossiers.rows == {}

        dossier, user = await opened(env)
        assert await ForgetUser(env.dossiers).execute(user) == 1
        assert await ForgetUser(env.dossiers).execute(user) == 0


class TestPropositionsEtCv:
    async def test_decider_modifie_le_cv(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        skills = next(p for p in dossier.propositions if p.path == "skills")

        decided = await DecideProposition(env.dossiers, env.validator, env.clock).execute(
            user, dossier.id, skills.id, Decision.ACCEPTER
        )

        assert decided.cv["skills"][0]["name"] == "Relation client"
        assert env.dossiers.rows[dossier.id].propositions[-1].state is PropositionState.ACCEPTEE

    async def test_retoucher_le_cv_revalide_et_recalcule(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        update = UpdateDossierCv(env.dossiers, env.validator, env.clock)

        updated = await update.execute(
            user, dossier.id, {**dossier.cv, "summary": "Rien", "skills": []}
        )

        assert updated.cv["summary"] == "Rien"
        assert "merchandising" in updated.coverage.missing
        env.validator.invalid = True
        with pytest.raises(CvInvalidError):
            await update.execute(user, dossier.id, dossier.cv)

    async def test_retoucher_attend_la_fin_de_l_adaptation(self) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(delay=0.2)
        user = uuid4()
        pending = (
            await create_of(env, inline_seconds=0.01).execute(user, env.offer.offer_id, None)
        ).dossier

        with pytest.raises(AdaptationInProgressError):
            await UpdateDossierCv(env.dossiers, env.validator, env.clock).execute(
                user, pending.id, pending.cv
            )
        with pytest.raises(AdaptationInProgressError):
            await DraftLetter(env.dossiers, env.writer, env.clock).execute(user, pending.id)


class TestModele:
    async def test_changer_de_modele_garde_tout_le_reste(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        lettered = dossier.with_letter(Letter.validated(sample_letter_json()), now=env.clock.now())
        await env.dossiers.save(lettered)
        change = ChangeDossierTemplate(env.dossiers, env.templates, env.clock)
        template = uuid4()
        env.clock.advance(timedelta(minutes=1))

        changed = await change.execute(user, dossier.id, template)

        assert changed.template_id == template
        assert changed.cv == lettered.cv
        assert changed.propositions == lettered.propositions
        assert changed.letter == lettered.letter
        assert changed.updated_at > lettered.updated_at
        assert env.dossiers.rows[dossier.id] == changed
        # Un second changement, puis le retour au modèle choisi : rien d'autre ne bouge.
        other = await change.execute(user, dossier.id, uuid4())
        assert other.cv == lettered.cv
        reset = await change.execute(user, dossier.id, None)
        assert reset.template_id is None
        assert reset.letter == lettered.letter

    async def test_le_meme_modele_redemande_ne_reecrit_pas(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        change = ChangeDossierTemplate(env.dossiers, env.templates, env.clock)
        template = uuid4()
        once = await change.execute(user, dossier.id, template)
        env.clock.advance(timedelta(minutes=1))

        again = await change.execute(user, dossier.id, template)

        assert again.updated_at == once.updated_at

    async def test_un_modele_verrouille_ou_un_dossier_inconnu(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        change = ChangeDossierTemplate(env.dossiers, env.templates, env.clock)
        locked = uuid4()
        env.templates.locked.add(locked)

        with pytest.raises(TemplateLockedError):
            await change.execute(user, dossier.id, locked)
        assert env.dossiers.rows[dossier.id].template_id is None
        with pytest.raises(DossierNotFoundError):
            await change.execute(uuid4(), dossier.id, None)


class TestCouleurDuDossier:
    async def test_la_couleur_se_pose_et_se_retire_sans_rien_toucher(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        change = ChangeDossierColor(env.dossiers, env.clock)
        env.clock.advance(timedelta(minutes=1))

        colored = await change.execute(user, dossier.id, "#7B1E2B")

        assert colored.primary_color == "#7B1E2B"
        assert colored.cv == dossier.cv
        assert colored.propositions == dossier.propositions
        assert colored.updated_at > dossier.updated_at
        assert env.dossiers.rows[dossier.id] == colored
        # Le même code redemandé ne réécrit rien.
        env.clock.advance(timedelta(minutes=1))
        assert (await change.execute(user, dossier.id, "#7B1E2B")).updated_at == colored.updated_at
        # Le modèle change, la couleur reste.
        template = uuid4()
        changed = await ChangeDossierTemplate(env.dossiers, env.templates, env.clock).execute(
            user, dossier.id, template
        )
        assert changed.primary_color == "#7B1E2B"
        assert changed.template_id == template
        # `None` rend la main à la couleur du modèle.
        reset = await change.execute(user, dossier.id, None)
        assert reset.primary_color is None
        assert reset.template_id == template
        with pytest.raises(DossierNotFoundError):
            await change.execute(uuid4(), dossier.id, "#7B1E2B")


class TestLettre:
    async def test_ebauche_edition_et_reecriture(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)

        drafted = await DraftLetter(env.dossiers, env.writer, env.clock).execute(user, dossier.id)
        assert drafted.letter is not None
        assert drafted.letter.subject == f"Candidature au poste de {env.offer.title}"

        edited = await UpdateLetter(env.dossiers, env.clock).execute(
            user, dossier.id, {**sample_letter_json(), "closing": "Cordialement,"}
        )
        assert edited.letter is not None
        assert edited.letter.closing == "Cordialement,"
        with pytest.raises(LetterInvalidError):
            await UpdateLetter(env.dossiers, env.clock).execute(user, dossier.id, {"subject": "x"})

        regenerated = await RegenerateParagraph(env.dossiers, env.writer, env.clock).execute(
            user, dossier.id, 2, "plus court"
        )
        assert regenerated.letter is not None
        assert regenerated.letter.paragraphs[2].text == "Paragraphe réécrit."
        assert env.writer.instructions == ["plus court"]
        with pytest.raises(ParagraphNotFoundError):
            await RegenerateParagraph(env.dossiers, env.writer, env.clock).execute(
                user, dossier.id, 9, None
            )

    async def test_sans_lettre_pas_de_paragraphe_et_une_panne_remonte(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        with pytest.raises(LetterMissingError):
            await RegenerateParagraph(env.dossiers, env.writer, env.clock).execute(
                user, dossier.id, 0, None
            )

        env.writer = FakeLetterWriter(error=ModelUnavailableError("panne"))
        with pytest.raises(ModelUnavailableError):
            await DraftLetter(env.dossiers, env.writer, env.clock).execute(user, dossier.id)
        assert env.dossiers.rows[dossier.id].letter is None


class TestCandidature:
    async def test_candidater_publie_et_revenir_aussi(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        bus = CollectingBus()

        applied = await MarkApplied(env.dossiers, bus, env.clock).execute(user, dossier.id)
        again = await MarkApplied(env.dossiers, bus, env.clock).execute(user, dossier.id)
        assert applied.status is DossierStatus.CANDIDATE
        assert again == applied
        assert [e.offer_id for e in bus.of_type(DossierApplied)] == [env.offer.offer_id]

        back = await UnmarkApplied(env.dossiers, bus, env.clock).execute(user, dossier.id)
        await UnmarkApplied(env.dossiers, bus, env.clock).execute(user, dossier.id)
        assert back.applied_at is None
        assert len(bus.of_type(DossierUnapplied)) == 1


def test_l_extrait_coupe_sur_un_mot() -> None:
    assert excerpt_of("court") == "court"
    long = " ".join(["mot"] * 100)
    assert excerpt_of(long).endswith("…")
    assert len(excerpt_of(long)) <= 281


class TestEcrituresSousControle:
    async def test_une_retouche_pendant_l_appel_inline_est_rejouee_pas_ecrasee(self) -> None:
        env = ApplicationsEnv.build()
        later = FakeClock(env.clock.now() + timedelta(seconds=5))

        class TouchingAdapter(FakeCvAdapter):
            """Pendant l'appel au modèle, la personne retouche le dossier."""

            async def adapt(self, offer: Any, cv: Any) -> Any:
                pending = next(iter(env.dossiers.rows.values()))
                touched = pending.with_cv(
                    {**pending.cv, "summary": "Mon résumé à moi"}, now=later.now()
                )
                env.dossiers.rows[pending.id] = touched
                return await super().adapt(offer, cv)

        env.adapter = TouchingAdapter()

        result = await create_of(env).execute(uuid4(), env.offer.offer_id, None)

        saved = env.dossiers.rows[result.dossier.id]
        assert saved.adaptation is AdaptationState.FAITE
        summary = next(p for p in saved.propositions if p.path == "summary")
        assert summary.before == "Mon résumé à moi"  # rejoué sur l'état retouché
        assert saved.updated_at == env.clock.now() or saved.updated_at >= later.now()

    async def test_un_dossier_supprime_pendant_l_appel_n_est_pas_recree(self) -> None:
        env = ApplicationsEnv.build()

        class DeletingAdapter(FakeCvAdapter):
            async def adapt(self, offer: Any, cv: Any) -> Any:
                env.dossiers.rows.clear()
                return await super().adapt(offer, cv)

        env.adapter = DeletingAdapter()

        result = await create_of(env).execute(uuid4(), env.offer.offer_id, None)

        assert result.dossier.adaptation is AdaptationState.FAITE
        assert env.dossiers.rows == {}

    async def test_la_lettre_n_ecrase_pas_une_retouche_faite_pendant_l_appel(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        later = FakeClock(env.clock.now() + timedelta(seconds=5))

        class TouchingWriter(FakeLetterWriter):
            async def draft(self, offer: Any, cv: Any) -> Any:
                current = env.dossiers.rows[dossier.id]
                env.dossiers.rows[dossier.id] = current.with_cv(
                    {**current.cv, "summary": "Retouché pendant"}, now=later.now()
                )
                return await super().draft(offer, cv)

        env.writer = TouchingWriter()

        drafted = await DraftLetter(env.dossiers, env.writer, env.clock).execute(user, dossier.id)

        assert drafted.letter is not None
        assert drafted.cv["summary"] == "Retouché pendant"

    async def test_un_conflit_entre_relecture_et_ecriture_est_dit(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)

        class Flaky(InMemoryDossierRepository):
            async def save_if_unchanged(self, d: Any, *, expected_updated_at: Any) -> bool:
                return False

        flaky = Flaky()
        flaky.rows = env.dossiers.rows
        with pytest.raises(DossierConflictError):
            await DraftLetter(flaky, env.writer, env.clock).execute(user, dossier.id)
        assert env.dossiers.rows[dossier.id].letter is None

        drafted = await DraftLetter(env.dossiers, env.writer, env.clock).execute(user, dossier.id)
        with pytest.raises(DossierConflictError):
            await RegenerateParagraph(flaky, env.writer, env.clock).execute(
                user, dossier.id, 0, None
            )
        assert env.dossiers.rows[dossier.id].letter == drafted.letter

    async def test_une_proposition_auto_appliquee_hors_bornes_reste_proposee(self) -> None:
        env = ApplicationsEnv.build()
        env.adapter = FakeCvAdapter(
            AdaptationOutput.model_validate(
                {
                    "headline": {"text": "Conseillère\nde vente", "reason": "saut de ligne"},
                    "summary": {"text": "Résumé correct.", "reason": "ok"},
                }
            )
        )

        result = await create_of(env).execute(uuid4(), env.offer.offer_id, None)

        states = {p.path: p.state for p in result.dossier.propositions}
        assert states["identity.headline"] is PropositionState.PROPOSEE
        assert states["summary"] is PropositionState.ACCEPTEE
        assert result.dossier.cv["identity"]["headline"] == "Vendeuse"

    async def test_le_validateur_refuse_l_auto_application_sans_bloquer(self) -> None:
        env = ApplicationsEnv.build()
        env.validator = PermissiveCvValidator(invalid=True)

        result = await create_of(env).execute(uuid4(), env.offer.offer_id, None)

        assert result.dossier.adaptation is AdaptationState.FAITE
        assert all(p.state is PropositionState.PROPOSEE for p in result.dossier.propositions)
        assert result.dossier.cv["identity"]["headline"] == "Vendeuse"

    async def test_accepter_une_proposition_qui_rend_le_cv_invalide_est_refuse(self) -> None:
        env = ApplicationsEnv.build()
        dossier, user = await opened(env)
        skills = next(p for p in dossier.propositions if p.path == "skills")
        env.validator.invalid = True

        with pytest.raises(PropositionInvalidError):
            await DecideProposition(env.dossiers, env.validator, env.clock).execute(
                user, dossier.id, skills.id, Decision.ACCEPTER
            )
        assert env.dossiers.rows[dossier.id].cv["skills"][0]["name"] == "Caisse"
