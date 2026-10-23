"""Le planificateur de collecte : cibles canoniques, termes bornés, demande, cadence, priorité."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from karria.modules.offers.domain import (
    ContractType,
    LocationCriteria,
    ScrapeOutcome,
    ScrapeTarget,
    SearchCriteria,
    TargetDemand,
    TargetOrigin,
    TargetState,
    TargetStats,
    after_scrape,
    candidate_terms,
    clean_term,
    demand_scores,
    gate_rate,
    interval_for,
    priority,
    seed_targets,
    targets_from_criteria,
    targets_of,
    validate_terms,
)
from karria.modules.offers.domain.steering import (
    COUNTRY_LOCATION,
    DEMAND_HALF_LIFE,
    DEMAND_INTERVAL,
    GAP_TTL,
    MAX_INTERVAL,
    MIN_INTERVAL,
    SEED_INTERVAL,
    SEED_TERMS,
    location_for_targets,
)
from offers.fakes import NOW

pytestmark = pytest.mark.unit


def criteria(titles: list[str], *, radius: int = 30, **fields: object) -> SearchCriteria:
    base: dict[str, object] = {
        "query": titles[0],
        "job_titles": titles,
        "location": LocationCriteria(
            label="Lyon", latitude=45.76, longitude=4.83, radius_km=radius, department_code="69"
        ),
    }
    base.update(fields)
    return SearchCriteria.model_validate(base)


def lyon(**fields: object) -> LocationCriteria:
    base: dict[str, object] = {"label": "Lyon", "latitude": 45.76, "longitude": 4.83}
    base.update(fields)
    return LocationCriteria.model_validate(base)


ACTIVE = TargetStats(state=TargetState.ACTIVE)


class TestTermes:
    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            ("serveur", "serveur"),
            ("  Chef   de rang ", "Chef de rang"),
            ("développeur python", "développeur python"),
            ("technicien de maintenance industrielle", "technicien de maintenance industrielle"),
        ],
    )
    def test_un_terme_court_est_garde_et_nettoye(self, term: str, expected: str) -> None:
        assert clean_term(term) == expected

    @pytest.mark.parametrize(
        "term",
        [
            "",
            "   ",
            "je cherche un poste de serveur dans un restaurant",
            "un métier qui me laisse du temps pour ma famille",
            "x" * 41,
            "emploi",
            "CDI",
            "temps plein",
            "h/f",
            "de la",
        ],
    )
    def test_une_phrase_un_generique_ou_du_vide_est_refuse(self, term: str) -> None:
        assert clean_term(term) is None

    def test_un_contrat_en_tete_de_terme_est_retire_le_terme_reste_canonique(self) -> None:
        assert clean_term("alternance comptable") == "comptable"
        assert clean_term("Stage en marketing digital") == "marketing digital"
        assert clean_term("apprenti comptable") == "apprenti comptable"
        stage = ScrapeTarget(
            search_term="stage marketing",
            location="Lyon",
            origin=TargetOrigin.DEMAND,
            contract=ContractType.STAGE,
        )
        assert stage.query_term == "stage marketing"

    def test_le_site_recoit_la_ville_du_geocodeur_pas_la_tournure(self) -> None:
        found = criteria(
            ["cariste"], location=lyon(label="Lyon et alentours", city="Lyon", department_code="69")
        )
        assert location_for_targets(found) == ("Lyon", "69", 30)

    def test_les_termes_de_collecte_priment_sur_les_intitules(self) -> None:
        found = criteria(["serveur", "serveuse"], search_terms=["serveur", "barman", "barman"])
        assert candidate_terms(found) == ["serveur", "barman"]

    def test_sans_termes_les_deux_premiers_intitules_servent_et_les_mauvais_tombent(self) -> None:
        found = criteria(["serveur en salle le soir et le week-end à Lyon", "serveuse", "barman"])
        assert candidate_terms(found) == ["serveuse"]

    def test_au_plus_trois_termes(self) -> None:
        found = criteria(["a"], search_terms=["serveur", "barman", "cuisinier", "plongeur"])
        assert len(candidate_terms(found)) == 3

    def test_un_terme_est_valide_par_un_titre_reel_qui_le_dit(self) -> None:
        kept = validate_terms(
            ["serveur", "barman", "hôte de caisse"],
            {
                "serveur": ["Serveur / Serveuse H/F", "Chef de rang"],
                "barman": ["Chef de partie", "Commis de cuisine"],
                "hôte de caisse": ["Hôte / Hôtesse de caisse (H/F)"],
            },
        )
        assert kept == ["serveur", "hôte de caisse"]

    def test_un_terme_sans_echantillon_n_est_pas_valide_ici(self) -> None:
        assert validate_terms(["serveur"], {}) == []

    def test_la_part_de_titres_qui_passent_la_porte(self) -> None:
        assert gate_rate("serveur", ["Serveur H/F", "Serveuse", "Cuisinier", "Plongeur"]) == 0.5
        assert gate_rate("serveur", []) is None


class TestCibles:
    def test_le_socle_couvre_le_pays(self) -> None:
        seeds = seed_targets()
        assert len(seeds) == len(SEED_TERMS)
        assert {t.location for t in seeds} == {COUNTRY_LOCATION}
        assert {t.origin for t in seeds} == {TargetOrigin.SEED}
        assert {t.key.split("|")[1] for t in seeds} == {"fr"}

    def test_lidentite_est_le_terme_plie_le_departement_et_le_contrat(self) -> None:
        a = ScrapeTarget(
            search_term="Développeur",
            location="Lyon",
            origin=TargetOrigin.DEMAND,
            location_key="69",
        )
        b = ScrapeTarget(
            search_term="developpeur",
            location="Villeurbanne",
            origin=TargetOrigin.SEED,
            location_key="69",
        )
        c = ScrapeTarget(
            search_term="developpeur",
            location="Lyon",
            origin=TargetOrigin.DEMAND,
            location_key="69",
            contract=ContractType.ALTERNANCE,
        )
        assert a.key == b.key == "developpeur|69|-"
        assert c.key == "developpeur|69|alternance"
        assert c.query_term == "alternance developpeur"
        assert a.query_term == "Développeur"

    def test_une_personne_donne_un_terme_par_metier_sur_son_departement(self) -> None:
        found = criteria(["serveur", "serveuse"], search_terms=["serveur", "barman"])
        found = found.model_copy(update={"location": lyon(department_code="69")})
        targets = targets_of(found)
        assert [(t.search_term, t.location, t.location_key, t.distance_km) for t in targets] == [
            ("serveur", "Lyon", "69", 30),
            ("barman", "Lyon", "69", 30),
        ]
        assert {t.contract for t in targets} == {None}

    def test_le_departement_vient_du_lieu_geocode_sinon_du_libelle(self) -> None:
        assert location_for_targets(criteria(["a"], location=lyon(department_code="69"))) == (
            "Lyon",
            "69",
            30,
        )
        zone = criteria(
            ["a"], location=LocationCriteria(label="Île-de-France", department_codes=["75", "92"])
        )
        assert location_for_targets(zone) == ("Île-de-France", "75", None)
        assert location_for_targets(criteria(["a"], location=lyon())) == ("Lyon", "fr", 30)
        assert location_for_targets(criteria(["a"], location=None, whole_france=True)) == (
            COUNTRY_LOCATION,
            "fr",
            None,
        )
        assert location_for_targets(criteria(["a"], location=None, remote=["teletravail"])) == (
            COUNTRY_LOCATION,
            "fr",
            None,
        )
        assert location_for_targets(criteria(["a"], location=lyon(label="  "))) == (
            COUNTRY_LOCATION,
            "fr",
            None,
        )

    def test_lalternance_et_le_stage_entrent_dans_la_cible(self) -> None:
        alternance = targets_of(criteria(["développeur"], contract_types=["alternance"]))
        stage = targets_of(criteria(["développeur"], contract_types=["stage"]))
        cdi = targets_of(criteria(["développeur"], contract_types=["cdi"]))
        assert alternance[0].contract is ContractType.ALTERNANCE
        assert alternance[0].query_term == "alternance développeur"
        assert stage[0].query_term == "stage développeur"
        assert cdi[0].contract is None

    def test_des_termes_imposes_remplacent_ceux_des_criteres(self) -> None:
        targets = targets_of(criteria(["serveur"]), ["barman"])
        assert [t.search_term for t in targets] == ["barman"]

    def test_mille_personnes_du_meme_departement_font_une_cible(self) -> None:
        lyonnais = [
            criteria(["serveur"], location=lyon(department_code="69", radius_km=20 + i % 3))
            for i in range(1_000)
        ]
        targets = targets_from_criteria(lyonnais)
        assert len(targets) == 1
        assert (targets[0].weight, targets[0].distance_km) == (1_000, 22)

    def test_les_cibles_sont_triees_par_demande(self) -> None:
        targets = targets_from_criteria(
            [criteria(["serveur"]), criteria(["Serveur"], radius=50), criteria(["cuisinier"])]
        )
        assert [t.search_term for t in targets] == ["serveur", "cuisinier"]
        assert (targets[0].weight, targets[0].distance_km) == (2, 50)

    def test_une_cible_nationale_reste_sans_rayon(self) -> None:
        nationwide = criteria(["serveur"], location=None, whole_france=True)
        targets = targets_from_criteria([nationwide, nationwide])
        assert targets[0].distance_km is None
        assert targets[0].location == COUNTRY_LOCATION


class TestDemande:
    def test_le_poids_decroit_de_moitie_par_demi_vie(self) -> None:
        user = uuid4()
        scores = demand_scores(
            [
                TargetDemand(user_id=user, target_key="a", since=NOW),
                TargetDemand(user_id=uuid4(), target_key="a", since=NOW - DEMAND_HALF_LIFE),
                TargetDemand(user_id=uuid4(), target_key="b", since=NOW + timedelta(hours=1)),
            ],
            NOW,
        )
        assert scores["a"] == pytest.approx(1.5)
        assert scores["b"] == pytest.approx(1.0)


class TestCadence:
    def test_la_base_depend_de_lorigine(self) -> None:
        assert interval_for(TargetOrigin.DEMAND) == DEMAND_INTERVAL
        assert interval_for(TargetOrigin.SEED) == SEED_INTERVAL

    def test_le_rendement_raccourcit_jusqua_lheure(self) -> None:
        fast = interval_for(
            TargetOrigin.DEMAND, TargetStats(state=TargetState.ACTIVE, yield_ema=20)
        )
        some = interval_for(TargetOrigin.DEMAND, TargetStats(state=TargetState.ACTIVE, yield_ema=5))
        assert fast == MIN_INTERVAL
        assert MIN_INTERVAL < some < DEMAND_INTERVAL

    def test_le_silence_double_lattente_jusqua_sept_jours(self) -> None:
        one = interval_for(
            TargetOrigin.DEMAND, TargetStats(state=TargetState.ACTIVE, zero_streak=1)
        )
        many = interval_for(
            TargetOrigin.DEMAND, TargetStats(state=TargetState.ACTIVE, zero_streak=9)
        )
        assert one == DEMAND_INTERVAL * 2
        assert many == MAX_INTERVAL

    def test_une_cible_retrogradee_part_de_quatre_fois_la_base(self) -> None:
        assert interval_for(TargetOrigin.DEMAND, TargetStats(state=TargetState.DEMOTED)) == (
            DEMAND_INTERVAL * 4
        )


class TestApprentissage:
    def test_le_premier_passage_fonde_le_rendement_et_tranche_lessai(self) -> None:
        stats = after_scrape(TargetStats(), ScrapeOutcome(found=40, new=12, gate_rate=0.9))
        assert stats.state is TargetState.ACTIVE
        assert (stats.scrapes, stats.yield_ema, stats.zero_streak, stats.last_found) == (
            1,
            12,
            0,
            40,
        )
        assert stats.gate_rejects == 4

    def test_un_essai_muet_est_retrograde(self) -> None:
        stats = after_scrape(TargetStats(), ScrapeOutcome(found=0, new=0))
        assert stats.state is TargetState.DEMOTED
        assert stats.zero_streak == 1

    def test_un_essai_hors_sujet_et_maigre_est_retrograde(self) -> None:
        stats = after_scrape(TargetStats(), ScrapeOutcome(found=30, new=3, gate_rate=0.1))
        assert stats.state is TargetState.DEMOTED

    def test_un_essai_hors_porte_mais_qui_rapporte_reste_actif(self) -> None:
        # « caissière » → 49 « Hôte de caisse » : la porte dit non, la personne dit oui.
        stats = after_scrape(TargetStats(), ScrapeOutcome(found=49, new=40, gate_rate=0.0))
        assert stats.state is TargetState.ACTIVE
        assert stats.gate_rejects == 49

    def test_le_rendement_se_lisse_et_le_silence_se_compte(self) -> None:
        stats = after_scrape(
            TargetStats(state=TargetState.ACTIVE, scrapes=3, yield_ema=10.0, zero_streak=0),
            ScrapeOutcome(found=20, new=0),
        )
        assert stats.yield_ema == 7.0
        assert stats.zero_streak == 1
        assert stats.state is TargetState.ACTIVE

    def test_une_cible_retrogradee_qui_rapporte_redevient_active(self) -> None:
        stats = after_scrape(
            TargetStats(state=TargetState.DEMOTED, scrapes=1, zero_streak=1),
            ScrapeOutcome(found=5, new=2),
        )
        assert stats.state is TargetState.ACTIVE
        assert stats.zero_streak == 0

    def test_la_redondance_avec_france_travail_se_lisse(self) -> None:
        first = after_scrape(
            TargetStats(), ScrapeOutcome(found=10, new=1, overlap_france_travail=9)
        )
        second = after_scrape(first, ScrapeOutcome(found=10, new=5, overlap_france_travail=0))
        assert first.overlap_ema == 0.9
        assert second.overlap_ema == pytest.approx(0.63)


class TestPriorite:
    def score(self, **changes: object) -> float:
        base: dict[str, object] = {
            "target": ScrapeTarget(
                search_term="serveur",
                location="Lyon",
                origin=TargetOrigin.DEMAND,
                location_key="69",
            ),
            "stats": ACTIVE,
            "demand": 1.0,
            "thin_since": None,
            "last_scraped_at": NOW - DEMAND_INTERVAL,
            "now": NOW,
        }
        base.update(changes)
        return priority(**base)  # type: ignore[arg-type]

    def test_plus_de_demande_plus_de_priorite_mais_pas_lineairement(self) -> None:
        one, ten, thousand = (self.score(demand=d) for d in (1.0, 10.0, 1_000.0))
        assert one < ten < thousand
        assert thousand - ten < 10 * (ten - one)

    def test_une_recherche_maigre_recente_passe_devant(self) -> None:
        assert self.score(thin_since=NOW - timedelta(hours=1)) > self.score(demand=10.0)
        assert self.score(thin_since=NOW - GAP_TTL - timedelta(minutes=1)) == self.score()

    def test_le_rendement_et_lanciennete_comptent(self) -> None:
        assert self.score(stats=TargetStats(state=TargetState.ACTIVE, yield_ema=20)) > self.score()
        assert self.score(last_scraped_at=None) > self.score()
        assert self.score(last_scraped_at=NOW - 10 * DEMAND_INTERVAL) > self.score(
            last_scraped_at=NOW - DEMAND_INTERVAL
        )

    def test_la_redondance_avec_france_travail_penalise(self) -> None:
        redundant = TargetStats(state=TargetState.ACTIVE, overlap_ema=1.0)
        partial = TargetStats(state=TargetState.ACTIVE, overlap_ema=0.5)
        assert self.score(stats=redundant) < self.score(stats=partial) == self.score()
