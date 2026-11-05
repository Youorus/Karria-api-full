"""Le dossier : copie figée, propositions réversibles, états déduits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from doubles import sample_adaptation_output, sample_base_cv, sample_letter_json, sample_offer_card
from karria.modules.applications.application import snapshot_of
from karria.modules.applications.domain import (
    AdaptationInProgressError,
    AdaptationNotFailedError,
    AdaptationState,
    Decision,
    Dossier,
    DossierStatus,
    Letter,
    LetterMissingError,
    OfferSnapshot,
    ParagraphNotFoundError,
    Proposition,
    PropositionInvalidError,
    PropositionNotFoundError,
    PropositionPathError,
    PropositionState,
    cv_hash,
    propositions_from,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)


def open_dossier() -> Dossier:
    return Dossier.open(
        user_id=uuid4(),
        offer=snapshot_of(sample_offer_card()),
        base_cv=sample_base_cv(),
        template_id=None,
        now=NOW,
    )


def adapted_dossier() -> Dossier:
    dossier = open_dossier()
    conversion = propositions_from(sample_adaptation_output(), dossier.cv)
    return dossier.adapted(conversion.propositions, now=LATER)


class TestOuverture:
    def test_copie_le_cv_et_fige_l_empreinte(self) -> None:
        base = sample_base_cv()
        dossier = Dossier.open(
            user_id=uuid4(),
            offer=snapshot_of(sample_offer_card()),
            base_cv=base,
            template_id=None,
            now=NOW,
        )

        assert dossier.cv == base
        assert dossier.cv is not base
        assert dossier.cv_base_hash == cv_hash(base)
        assert dossier.adaptation is AdaptationState.EN_COURS
        assert dossier.status is DossierStatus.BROUILLON
        assert not dossier.base_changed(base)
        base["summary"] = "changé"
        assert dossier.base_changed(base)
        assert dossier.base_changed(None)

    def test_la_couverture_est_calculee_des_l_ouverture(self) -> None:
        dossier = open_dossier()

        assert "conseillere de vente" in dossier.coverage.missing
        assert dossier.coverage.present

    def test_l_offre_figee_fait_l_aller_retour_json(self) -> None:
        snapshot = snapshot_of(sample_offer_card())

        assert OfferSnapshot.from_json(snapshot.as_json()) == snapshot
        assert snapshot.excerpt.startswith("Nous recherchons")


class TestAdaptation:
    def test_applique_intitule_et_resume_et_laisse_le_reste_en_attente(self) -> None:
        dossier = adapted_dossier()

        assert dossier.adaptation is AdaptationState.FAITE
        assert dossier.cv["identity"]["headline"] == "Conseillère de vente"
        assert dossier.cv["summary"].startswith("Conseillère de vente depuis")
        states = {p.path: p.state for p in dossier.propositions}
        assert states["identity.headline"] is PropositionState.ACCEPTEE
        assert states["summary"] is PropositionState.ACCEPTEE
        assert states["experiences.0.missions.0"] is PropositionState.PROPOSEE
        assert states["skills"] is PropositionState.PROPOSEE
        assert dossier.cv["experiences"][0]["missions"][0] == "Accueillir les clients"
        assert dossier.pending_count == 3
        assert dossier.status is DossierStatus.BROUILLON
        assert "conseillere de vente" in dossier.coverage.present

    def test_une_proposition_sur_un_element_disparu_est_ecartee(self) -> None:
        dossier = open_dossier()
        orphan = Proposition(uuid4(), "experiences.5.position", "a", "b", "")

        adapted = dossier.adapted([orphan], now=LATER)

        assert adapted.propositions == ()
        assert adapted.status is DossierStatus.PRET

    def test_un_echec_laisse_le_dossier_utilisable_et_relancable(self) -> None:
        failed = open_dossier().adaptation_failed("ai_unavailable", now=LATER)

        assert failed.adaptation is AdaptationState.ECHOUEE
        assert failed.adaptation_error == "ai_unavailable"
        assert failed.status is DossierStatus.PRET
        restarted = failed.adaptation_started(now=LATER)
        assert restarted.adaptation is AdaptationState.EN_COURS
        assert restarted.adaptation_error is None

    def test_on_ne_relance_que_ce_qui_a_echoue(self) -> None:
        with pytest.raises(AdaptationNotFailedError):
            adapted_dossier().adaptation_started(now=LATER)

    def test_un_geste_conflictuel_attend_la_fin(self) -> None:
        with pytest.raises(AdaptationInProgressError):
            open_dossier().ensure_not_adapting()
        adapted_dossier().ensure_not_adapting()


class TestDecisions:
    def test_accepter_applique_et_refuser_revient(self) -> None:
        dossier = adapted_dossier()
        mission = next(p for p in dossier.propositions if p.path == "experiences.0.missions.0")

        accepted = dossier.decide(mission.id, Decision.ACCEPTER, now=LATER)
        assert (
            accepted.cv["experiences"][0]["missions"][0] == "Accueillir et conseiller les clients"
        )
        assert accepted.pending_count == 2

        refused = accepted.decide(mission.id, Decision.REFUSER, now=LATER)
        assert refused.cv["experiences"][0]["missions"][0] == "Accueillir les clients"
        assert (
            next(p for p in refused.propositions if p.id == mission.id).state
            is PropositionState.REFUSEE
        )

    def test_revenir_sur_l_intitule_applique_remet_l_avant_et_rouvre(self) -> None:
        dossier = adapted_dossier()
        headline = next(p for p in dossier.propositions if p.path == "identity.headline")

        reverted = dossier.decide(headline.id, Decision.REVENIR, now=LATER)

        assert reverted.cv["identity"]["headline"] == "Vendeuse"
        assert (
            next(p for p in reverted.propositions if p.id == headline.id).state
            is PropositionState.PROPOSEE
        )

    def test_accepter_un_ordre_de_competences(self) -> None:
        dossier = adapted_dossier()
        skills = next(p for p in dossier.propositions if p.path == "skills")

        accepted = dossier.decide(skills.id, Decision.ACCEPTER, now=LATER)

        assert [s["name"] for s in accepted.cv["skills"]] == [
            "Relation client",
            "Caisse",
            "Merchandising",
        ]

    def test_tout_decide_rend_le_dossier_pret(self) -> None:
        dossier = adapted_dossier()
        for proposition in dossier.propositions:
            if proposition.state is PropositionState.PROPOSEE:
                dossier = dossier.decide(proposition.id, Decision.ACCEPTER, now=LATER)

        assert dossier.status is DossierStatus.PRET

    def test_une_proposition_inconnue_ou_orpheline(self) -> None:
        dossier = adapted_dossier()
        with pytest.raises(PropositionNotFoundError):
            dossier.decide(uuid4(), Decision.ACCEPTER, now=LATER)

        trimmed = dossier.with_cv({**dossier.cv, "experiences": []}, now=LATER)
        mission = next(p for p in trimmed.propositions if p.path == "experiences.0.missions.0")
        with pytest.raises(PropositionPathError):
            trimmed.decide(mission.id, Decision.ACCEPTER, now=LATER)


class TestCvEtLettre:
    def test_retoucher_le_cv_recalcule_la_couverture(self) -> None:
        dossier = adapted_dossier()
        cv = {**dossier.cv, "summary": "Rien à voir", "skills": [], "experiences": []}

        updated = dossier.with_cv(cv, now=LATER)

        assert "caisse" in updated.coverage.missing
        assert "merchandising" in updated.coverage.missing
        assert "caisse" in dossier.coverage.present
        assert updated.updated_at == LATER

    def test_la_lettre_et_ses_paragraphes(self) -> None:
        dossier = adapted_dossier()
        with pytest.raises(LetterMissingError):
            dossier.with_paragraph(0, "x", now=LATER)

        lettered = dossier.with_letter(Letter.validated(sample_letter_json()), now=LATER)
        assert lettered.letter is not None
        with pytest.raises(ParagraphNotFoundError):
            lettered.with_paragraph(4, "x", now=LATER)
        assert (
            lettered.with_paragraph(1, "Nouveau", now=LATER).letter.paragraphs[1].text == "Nouveau"
        )  # type: ignore[union-attr]


class TestCandidature:
    def test_candidater_puis_revenir(self) -> None:
        dossier = adapted_dossier()

        applied = dossier.mark_applied(now=LATER)
        assert applied.status is DossierStatus.CANDIDATE
        assert applied.applied_at == LATER
        assert applied.mark_applied(now=LATER + timedelta(days=1)).applied_at == LATER

        back = applied.unmark_applied(now=LATER)
        assert back.applied_at is None
        assert back.status is DossierStatus.BROUILLON
        assert back.unmark_applied(now=LATER) is back


class TestBornesEtValidation:
    def test_une_auto_application_hors_bornes_reste_proposee(self) -> None:
        dossier = open_dossier()
        bad = Proposition(uuid4(), "identity.headline", "Vendeuse", "x" * 151, "trop long")
        good = Proposition(uuid4(), "summary", dossier.cv["summary"], "Court.", "ok")

        adapted = dossier.adapted([bad, good], now=LATER)

        states = {p.path: p.state for p in adapted.propositions}
        assert states["identity.headline"] is PropositionState.PROPOSEE
        assert states["summary"] is PropositionState.ACCEPTEE
        assert adapted.cv["identity"]["headline"] == "Vendeuse"

    def test_le_controle_externe_refuse_sans_rien_ecrire(self) -> None:
        dossier = open_dossier()
        proposition = Proposition(uuid4(), "summary", dossier.cv["summary"], "Nouveau.", "")

        def refuse(cv: object) -> None:
            raise ValueError("non")

        adapted = dossier.adapted([proposition], now=LATER, check=refuse)
        assert adapted.propositions[0].state is PropositionState.PROPOSEE
        assert adapted.cv["summary"] == dossier.cv["summary"]
        with pytest.raises(PropositionInvalidError) as excinfo:
            adapted.decide(proposition.id, Decision.ACCEPTER, now=LATER, check=refuse)
        assert excinfo.value.details["why"] == "cv_invalid"

    def test_decider_une_valeur_hors_bornes_est_refuse_et_revenir_reste_possible(self) -> None:
        dossier = open_dossier()
        bad = Proposition(uuid4(), "identity.headline", "Vendeuse", "Deux\nlignes", "")
        adapted = dossier.adapted([bad], now=LATER)

        with pytest.raises(PropositionInvalidError):
            adapted.decide(bad.id, Decision.ACCEPTER, now=LATER)
        reverted = adapted.decide(bad.id, Decision.REFUSER, now=LATER)
        assert reverted.cv["identity"]["headline"] == "Vendeuse"
