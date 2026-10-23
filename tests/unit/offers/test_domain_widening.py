"""Les élargissements : candidats, choix chiffré, phrase, acceptation, départements voisins."""

from __future__ import annotations

import pytest

from karria.modules.offers.domain import (
    WIDEN_MIN_GAIN,
    WIDEN_WHEN_BELOW,
    ContractType,
    ExperienceLevel,
    LocationCriteria,
    RemoteMode,
    Schedule,
    Widening,
    WideningKind,
    accepted_widening,
    choose,
    department_label,
    neighbours_of,
    phrase_widenings,
    relaxations,
    render_pending,
    title_relaxations,
)
from karria.modules.offers.domain.widening import Relaxation, title_key
from offers.fakes import make_criteria, make_offer

pytestmark = pytest.mark.unit


def ids(found: list[Relaxation]) -> list[str]:
    return [r.id for r in found]


class TestDepartements:
    def test_le_libelle_nomme_le_departement(self) -> None:
        assert department_label("69") == "Rhône (69)"
        assert department_label("2a") == "Corse-du-Sud (2A)"
        assert department_label("999") == "999"

    def test_les_voisins_sont_symetriques_et_hors_de_la_demande(self) -> None:
        assert neighbours_of(["69"]) == ["01", "38", "42", "71"]
        assert "69" in neighbours_of(["42"])
        assert neighbours_of(["75", "92"]) == ["78", "91", "93", "94", "95"]
        assert neighbours_of(["971"]) == []


class TestCandidats:
    def test_un_point_propose_deux_rayons_et_toute_la_france(self) -> None:
        found = relaxations(make_criteria())

        assert ids(found) == ["radius-50", "radius-100", "whole-france"]
        assert found[0].criteria.location is not None
        assert found[0].criteria.location.radius_km == 50
        assert found[2].criteria.whole_france
        assert found[0].label == "élargir à 50 km autour de Paris"

    def test_un_grand_rayon_ne_propose_que_le_pas_restant(self) -> None:
        criteria = make_criteria()
        assert criteria.location is not None
        wide = criteria.model_copy(
            update={"location": criteria.location.model_copy(update={"radius_km": 80})}
        )

        assert ids(relaxations(wide)) == ["radius-100", "whole-france"]

    def test_toute_la_france_ou_le_teletravail_ne_s_elargissent_pas_en_lieu(self) -> None:
        assert ids(relaxations(make_criteria(location=None, whole_france=True))) == []
        remote = make_criteria(remote=[RemoteMode.REMOTE])
        assert ids(relaxations(remote)) == []

    def test_des_departements_proposent_leurs_voisins(self) -> None:
        criteria = make_criteria(location=LocationCriteria(label="Rhône", department_codes=["69"]))

        found = relaxations(criteria)

        assert ids(found) == ["departments", "whole-france"]
        assert found[0].criteria.location is not None
        assert found[0].criteria.location.department_codes == ["69", "01", "38", "42", "71"]
        assert (
            found[0].label == "ajouter les départements voisins (Ain (01), Isère (38), Loire (42)…)"
        )

    def test_les_contrats_le_temps_l_experience_et_le_site(self) -> None:
        criteria = make_criteria(
            location=None,
            whole_france=True,
            contract_types=[ContractType.CDI],
            schedule=Schedule.PART_TIME,
            max_experience=ExperienceLevel.JUNIOR,
            remote=[RemoteMode.HYBRID],
        )

        found = relaxations(criteria)

        assert ids(found) == [
            "contract-cdd",
            "contract-interim",
            "schedule-any",
            "experience-any",
            "remote-any",
        ]
        assert found[0].criteria.contract_types == [ContractType.CDI, ContractType.CDD]
        assert found[1].label == "accepter aussi l'intérim"
        assert found[2].criteria.schedule is None
        assert found[3].criteria.max_experience is None
        assert found[4].criteria.remote == []

    def test_le_temps_plein_s_ouvre_au_temps_partiel(self) -> None:
        criteria = make_criteria(location=None, whole_france=True, schedule=Schedule.FULL_TIME)
        assert [r.label for r in relaxations(criteria)] == ["accepter aussi le temps partiel"]

    def test_l_alternance_seule_et_le_sur_site_ne_proposent_rien(self) -> None:
        criteria = make_criteria(
            location=None,
            whole_france=True,
            contract_types=[ContractType.ALTERNANCE],
            remote=[RemoteMode.ON_SITE],
        )
        assert relaxations(criteria) == []


class TestIntitulesVoisins:
    def test_la_cle_d_un_titre_oublie_le_genre_et_les_parentheses(self) -> None:
        assert title_key("Commis de cuisine (H/F)") == "commis de cuisine"
        assert title_key("Cuisinier/ère H/F/X - CDI") == "cuisinier ere - cdi"
        assert title_key("Pâtissier F/H") == "patissier"

    def test_les_titres_frequents_parmi_les_ecartees_deviennent_des_candidats(self) -> None:
        criteria = make_criteria(job_titles=["cuisinier"], excluded_titles=["chef cuisinier"])
        rejected = [
            *(make_offer(title="Commis de cuisine H/F") for _ in range(6)),
            *(make_offer(title="Chef cuisinier") for _ in range(9)),
            *(make_offer(title="Plongeur") for _ in range(2)),
        ]

        found = title_relaxations(criteria, rejected)

        assert ids(found) == ["title-commis-de-cuisine"]
        assert found[0].known_gain == 6
        assert found[0].criteria.job_titles == ["cuisinier", "commis de cuisine"]
        assert found[0].short == "Ajouter « commis de cuisine »"

    def test_un_intitule_exclu_puis_choisi_sort_des_exclusions(self) -> None:
        criteria = make_criteria(job_titles=["cuisinier"], excluded_titles=["second de cuisine"])
        rejected = [make_offer(title="Second de cuisine") for _ in range(5)]

        assert title_relaxations(criteria, rejected) == []  # exclu par le modèle : on respecte

    def test_sans_ecartee_rien(self) -> None:
        assert title_relaxations(make_criteria(), []) == []


def relaxation(key: str, kind: WideningKind, known_gain: int | None = None) -> Relaxation:
    return Relaxation(
        id=key,
        kind=kind,
        criteria=make_criteria(),
        label=f"l-{key}",
        short=f"S-{key}",
        known_gain=known_gain,
    )


class TestChoix:
    def test_classe_par_gain_une_par_famille_trois_au_plus(self) -> None:
        counted = [
            (relaxation("radius-50", WideningKind.RADIUS), 60),
            (relaxation("radius-100", WideningKind.RADIUS), 300),
            (relaxation("whole-france", WideningKind.WHOLE_FRANCE), 2_000),
            (relaxation("contract-cdd", WideningKind.CONTRACT_TYPES), 33),
            (relaxation("schedule-any", WideningKind.SCHEDULE), 20 + WIDEN_MIN_GAIN - 1),
            (relaxation("title-commis", WideningKind.JOB_TITLES, known_gain=15), 20),
        ]

        chosen = choose(20, counted)

        # Le lieu est une seule famille : le rayon 50 (+40) double déjà un fil
        # de 20, on ne propose pas plus loin ; le contrat (+13) passe derrière
        # l'intitulé voisin (+15).
        assert [(w.id, w.gain) for w in chosen] == [
            ("radius-50", 40),
            ("title-commis", 15),
            ("contract-cdd", 13),
        ]
        assert chosen[2].message == "S-contract-cdd : +13"
        assert chosen[2].label == "l-contract-cdd"

    def test_a_gain_egal_le_rayon_le_plus_petit_l_emporte(self) -> None:
        counted = [
            (relaxation("radius-50", WideningKind.RADIUS), 32),
            (relaxation("radius-100", WideningKind.RADIUS), 32),
            (relaxation("whole-france", WideningKind.WHOLE_FRANCE), 32),
        ]

        assert [w.id for w in choose(20, counted)] == ["radius-50"]

    def test_un_pas_insuffisant_cede_au_pas_suivant(self) -> None:
        counted = [
            (relaxation("radius-50", WideningKind.RADIUS), 32),  # +12 : notable, mais ne double pas
            (relaxation("radius-100", WideningKind.RADIUS), 45),  # +25 : double le fil
            (relaxation("whole-france", WideningKind.WHOLE_FRANCE), 600),
        ]

        assert [(w.id, w.gain) for w in choose(20, counted)] == [("radius-100", 25)]

    def test_un_fil_fourni_n_est_elargi_que_s_il_double(self) -> None:
        current = WIDEN_WHEN_BELOW + 10
        counted = [
            (relaxation("radius-50", WideningKind.RADIUS), current + 25),
            (relaxation("whole-france", WideningKind.WHOLE_FRANCE), current * 3),
        ]

        assert [w.id for w in choose(current, counted)] == ["whole-france"]

    def test_un_gain_nul_ou_negatif_ne_passe_pas(self) -> None:
        assert choose(50, [(relaxation("radius-50", WideningKind.RADIUS), 45)]) == []


def widening(key: str = "radius-50", gain: int = 40) -> Widening:
    return Widening(
        id=key,
        kind=WideningKind.RADIUS,
        label="élargir à 50 km autour de Paris",
        gain=gain,
        message=f"Élargir à 50 km : +{gain}",
        criteria=make_criteria(),
    )


class TestPhraseEtAcceptation:
    def test_la_phrase_chiffre_et_demande_la_permission(self) -> None:
        assert phrase_widenings(20, []) == ""
        assert (
            phrase_widenings(20, [widening()])
            == "Si tu veux, je peux élargir : élargir à 50 km autour de Paris (+40). Je le fais ?"
        )
        assert phrase_widenings(0, [widening()]).startswith("Pour en trouver, je peux élargir")

    def test_une_reponse_rapide_reprise_telle_quelle_est_reconnue(self) -> None:
        pending = [widening()]

        assert accepted_widening("élargir à 50 km : +40", pending) == pending[0]
        assert accepted_widening("  Élargir à 50 km autour de Paris ", pending) == pending[0]
        assert accepted_widening("oui", pending) is None
        assert accepted_widening("Élargir à 50 km : +40", []) is None

    def test_les_propositions_se_rendent_au_modele_en_donnees(self) -> None:
        assert render_pending([]) is None
        rendered = render_pending([widening()])
        assert rendered is not None
        assert "- radius-50 : élargir à 50 km autour de Paris (+40 offres)" in rendered

    def test_une_proposition_fait_l_aller_retour_en_dictionnaire(self) -> None:
        original = widening()
        assert Widening.from_dict(original.to_dict()) == original
