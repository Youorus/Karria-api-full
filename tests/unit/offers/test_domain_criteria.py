"""Les critères de recherche : complétude, filtre dur, dérivés pour l'index et la collecte."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from karria.modules.offers.domain import (
    DEFAULT_RADIUS_KM,
    Company,
    ContractType,
    ExperienceLevel,
    Location,
    LocationCriteria,
    Offer,
    RemoteMode,
    Salary,
    Schedule,
    SearchCriteria,
    Source,
    haversine_km,
    uuid7,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, tzinfo=UTC)
PARIS = LocationCriteria(label="Paris", latitude=48.8566, longitude=2.3522)
#: Versailles, à une vingtaine de kilomètres de Paris.
VERSAILLES = Location(
    label="Versailles (78)",
    city="Versailles",
    department_code="78",
    latitude=48.8049,
    longitude=2.1204,
)
#: Lyon, à quatre cents kilomètres.
LYON = Location(label="Lyon", city="Lyon", department_code="69", latitude=45.764, longitude=4.8357)


def make_offer(**changes: object) -> Offer:
    offer = Offer(
        id=uuid7(),
        source=Source.INDEED,
        source_id="in-1",
        title="Serveur H/F",
        company=Company(name="Le Bistrot"),
        description="Service en salle.",
        location=VERSAILLES,
        contract_type=ContractType.CDI,
        remote=RemoteMode.ON_SITE,
        schedule=Schedule.FULL_TIME,
        experience=ExperienceLevel.JUNIOR,
        salary=Salary(min_monthly=1_900, max_monthly=2_100, raw=None),
        published_at=NOW,
        first_seen_at=NOW,
        last_seen_at=NOW,
        apply_url=None,
        origin_url=None,
    )
    return dataclasses.replace(offer, **changes)  # type: ignore[arg-type]


def criteria(**fields: object) -> SearchCriteria:
    base: dict[str, object] = {
        "query": "serveur à Paris",
        "job_titles": ["serveur"],
        "location": PARIS,
    }
    base.update(fields)
    return SearchCriteria.model_validate(base)


class TestValidation:
    def test_les_intitules_sont_dedoublonnes_sans_tenir_compte_des_accents(self) -> None:
        found = criteria(job_titles=["Développeur", "developpeur", "  ", "Dev  web"])
        assert found.job_titles == ["Développeur", "Dev web"]

    def test_les_contrats_et_modes_sont_dedoublonnes(self) -> None:
        found = criteria(contract_types=["cdi", "cdi"], remote=["hybride", "hybride"])
        assert found.contract_types == [ContractType.CDI]
        assert found.remote == [RemoteMode.HYBRID]

    def test_les_departements_sont_normalises(self) -> None:
        place = LocationCriteria(label="Corse", department_codes=["2a", " 75 ", "", "2A"])
        assert place.department_codes == ["2A", "75"]

    def test_un_lieu_sans_point_ni_departement_nest_pas_utilisable(self) -> None:
        assert not LocationCriteria(label="Ailleurs").is_usable
        assert LocationCriteria(label="IDF", department_codes=["75"]).is_usable

    def test_le_rayon_par_defaut_est_celui_du_domaine(self) -> None:
        assert LocationCriteria(label="Lyon").radius_km == DEFAULT_RADIUS_KM


class TestCompletude:
    def test_un_metier_et_un_lieu_suffisent_pour_chercher(self) -> None:
        assert criteria().is_searchable

    def test_sans_metier_on_demande_le_metier_dabord(self) -> None:
        assert criteria(job_titles=[], location=None).missing() == ["job", "location"]

    def test_toute_la_france_tient_lieu_de_lieu(self) -> None:
        assert criteria(location=None, whole_france=True).has_place

    def test_le_teletravail_seul_tient_lieu_de_lieu(self) -> None:
        found = criteria(location=None, remote=["teletravail"])
        assert found.wants_remote_only
        assert found.is_searchable

    def test_un_lieu_inutilisable_ne_suffit_pas(self) -> None:
        assert criteria(location=LocationCriteria(label="quelque part")).missing() == ["location"]

    def test_lalternance_seule_se_reconnait(self) -> None:
        assert criteria(contract_types=["alternance"]).alternance_only
        assert not criteria(contract_types=["alternance", "cdi"]).alternance_only


class TestLieu:
    def test_une_offre_dans_le_rayon_convient(self) -> None:
        assert criteria().accepts_location(make_offer())

    def test_une_offre_hors_du_rayon_est_ecartee(self) -> None:
        assert not criteria().accepts_location(make_offer(location=LYON))

    def test_une_offre_en_teletravail_complet_ne_convient_partout_que_si_on_l_accepte(
        self,
    ) -> None:
        remote = make_offer(location=LYON, remote=RemoteMode.REMOTE)

        assert not criteria().accepts_location(remote)
        assert criteria(remote=[RemoteMode.HYBRID, RemoteMode.REMOTE]).accepts_location(remote)
        assert criteria(remote=[RemoteMode.HYBRID, RemoteMode.REMOTE]).remote_anywhere

    def test_l_alternance_ou_le_stage_exigent_un_contrat_lisible(self) -> None:
        mute = make_offer(contract_type=None)
        alternance = criteria(contract_types=[ContractType.ALTERNANCE, ContractType.STAGE])

        assert alternance.strict_contracts
        assert not alternance.accepts_terms(mute)
        assert alternance.accepts_terms(make_offer(contract_type=ContractType.STAGE))

    def test_un_cdi_laisse_passer_l_offre_muette(self) -> None:
        found = criteria(contract_types=[ContractType.CDI, ContractType.ALTERNANCE])

        assert not found.strict_contracts
        assert found.accepts_terms(make_offer(contract_type=None))
        assert not criteria().strict_contracts

    def test_toute_la_france_accepte_tout_lieu(self) -> None:
        assert criteria(location=None, whole_france=True).accepts_location(
            make_offer(location=LYON)
        )

    def test_sans_lieu_utilisable_rien_nest_ecarte(self) -> None:
        found = criteria(location=LocationCriteria(label="quelque part"))
        assert found.accepts_location(make_offer(location=LYON))

    def test_une_offre_sans_lieu_est_ecartee_dune_recherche_localisee(self) -> None:
        assert not criteria().accepts_location(make_offer(location=None))

    def test_le_departement_vise_suffit(self) -> None:
        found = criteria(location=LocationCriteria(label="Rhône", department_codes=["69"]))
        assert found.accepts_location(make_offer(location=LYON))

    def test_une_offre_sans_coordonnees_hors_departement_est_ecartee(self) -> None:
        place = Location(label="Lyon", city="Lyon", department_code="69")
        assert not criteria().accepts_location(make_offer(location=place))

    def test_la_distance_est_inconnue_sans_point(self) -> None:
        assert criteria(location=None, whole_france=True).distance_km(45.0, 4.0) is None

    def test_haversine_paris_lyon(self) -> None:
        assert 385 < haversine_km(48.8566, 2.3522, 45.764, 4.8357) < 400


class TestFiltreDur:
    def test_une_offre_conforme_est_acceptee(self) -> None:
        assert criteria(contract_types=["cdi"]).accepts(make_offer())

    def test_une_offre_non_indexable_est_refusee(self) -> None:
        assert not criteria().accepts(make_offer(is_active=False))

    def test_une_offre_hors_zone_est_refusee(self) -> None:
        assert not criteria().accepts(make_offer(location=LYON))

    def test_un_contrat_contraire_elimine(self) -> None:
        assert not criteria(contract_types=["cdd"]).accepts(make_offer())

    def test_un_contrat_absent_nelimine_pas(self) -> None:
        assert criteria(contract_types=["cdd"]).accepts(make_offer(contract_type=None))

    def test_le_teletravail_seul_ecarte_une_offre_muette(self) -> None:
        found = criteria(location=None, remote=["teletravail"])
        assert not found.accepts(make_offer(remote=None))
        assert found.accepts(make_offer(remote=RemoteMode.REMOTE))

    def test_un_mode_de_travail_contraire_elimine(self) -> None:
        assert not criteria(remote=["hybride"]).accepts(make_offer())
        assert criteria(remote=["hybride"]).accepts(make_offer(remote=None))

    def test_un_temps_de_travail_contraire_elimine(self) -> None:
        assert not criteria(schedule="temps_partiel").accepts(make_offer())
        assert not criteria(schedule="temps_plein").accepts(make_offer(schedule=Schedule.PART_TIME))

    def test_le_temps_partiel_ecarte_une_offre_muette_le_temps_plein_la_garde(self) -> None:
        # Une annonce à temps partiel le dit toujours ; une annonce muette est
        # presque toujours à temps plein (85 % des offres France Travail).
        assert not criteria(schedule="temps_partiel").accepts(make_offer(schedule=None))
        assert criteria(schedule="temps_plein").accepts(make_offer(schedule=None))
        assert criteria(schedule="temps_partiel").strict_schedule
        assert not criteria(schedule="temps_plein").strict_schedule

    def test_une_experience_exigee_superieure_elimine(self) -> None:
        found = criteria(max_experience="debutant")
        assert not found.accepts(make_offer())
        assert found.accepts(make_offer(experience=None))
        assert criteria(max_experience="senior").accepts(make_offer())

    def test_un_salaire_plafond_trop_bas_elimine(self) -> None:
        assert not criteria(salary_min_monthly=2_500).accepts(make_offer())
        assert criteria(salary_min_monthly=2_000).accepts(make_offer())

    def test_un_salaire_inconnu_nelimine_pas(self) -> None:
        unknown = Salary(min_monthly=None, max_monthly=None, raw=None)
        assert criteria(salary_min_monthly=2_500).accepts(make_offer(salary=unknown))

    def test_le_salaire_minimal_sert_de_plafond_sans_maximum(self) -> None:
        low_only = Salary(min_monthly=1_800, max_monthly=None, raw=None)
        assert not criteria(salary_min_monthly=2_000).accepts(make_offer(salary=low_only))


class TestDerives:
    def test_le_resume_dit_metier_lieu_contrat_salaire(self) -> None:
        found = criteria(contract_types=["cdi", "cdd"], salary_min_monthly=2_000)
        assert (
            found.summary()
            == f"serveur · Paris ({DEFAULT_RADIUS_KM} km) · CDI, CDD · ≥ 2000 €/mois"
        )

    def test_le_resume_dit_teletravail_ou_toute_la_france(self) -> None:
        assert "en télétravail" in criteria(location=None, remote=["teletravail"]).summary()
        assert "toute la France" in criteria(location=None, whole_france=True).summary()

    def test_le_resume_dun_lieu_sans_point_na_pas_de_rayon(self) -> None:
        place = LocationCriteria(label="Île-de-France", department_codes=["75"])
        assert criteria(location=place).summary() == "serveur · Île-de-France"

    def test_le_resume_vide_retombe_sur_la_phrase(self) -> None:
        assert criteria(job_titles=[], location=None).summary() == "serveur à Paris"

    def test_les_termes_lexicaux_mettent_les_intitules_dabord(self) -> None:
        found = criteria(keywords=["service en salle"])
        assert found.lexical_terms() == ["serveur", "service en salle"]

    def test_le_texte_dense_prefere_la_description_semantique(self) -> None:
        assert (
            criteria(semantic_query="Serveur en brasserie").dense_text() == "Serveur en brasserie"
        )
        assert criteria().dense_text() == "serveur ; serveur à Paris"

    def test_lempreinte_ignore_la_phrase_mais_pas_les_criteres(self) -> None:
        assert criteria().fingerprint() == criteria(query="autre phrase").fingerprint()
        assert criteria().fingerprint() != criteria(contract_types=["cdi"]).fingerprint()

    def test_les_termes_de_collecte_sont_courts_dedoublonnes_et_au_plus_trois(self) -> None:
        found = criteria(search_terms=["serveur", "Serveur", "barman", "x" * 60, "plongeur"])
        assert found.search_terms == ["serveur", "barman", "x" * 40]

    def test_des_termes_sans_intitules_disent_quand_meme_le_metier(self) -> None:
        found = criteria(job_titles=[], search_terms=["comptable"])
        assert found.job_titles == ["comptable"]
        assert found.is_searchable

    def test_les_preferences_ne_changent_pas_lempreinte(self) -> None:
        assert (
            criteria(preferences=["pas trop de stress"], search_terms=["serveur"]).fingerprint()
            == criteria().fingerprint()
        )
        assert criteria(preferences=["a", "a", "b"]).preferences == ["a", "b"]
