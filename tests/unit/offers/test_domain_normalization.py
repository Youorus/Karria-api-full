"""De l'annonce brute à l'offre comprise : extraits réels d'annonces françaises."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from karria.modules.offers.domain import (
    ContractType,
    ExperienceLevel,
    GeocodedPlace,
    LocationHint,
    RawPosting,
    RemoteMode,
    Schedule,
    Source,
    clean_text,
    department_from_postal,
    fold,
    normalize_posting,
    parse_location_label,
    parse_salary_text,
    published_at_of,
    source_place,
)
from karria.modules.offers.domain.normalization import (
    DESCRIPTION_MAX_LENGTH,
    HOURS_PER_MONTH,
    _to_number,
    build_location,
    clean_title,
    detect_contract,
    detect_experience,
    detect_remote,
    detect_schedule,
    level_for_years,
    normalize_salary,
    source_default_contract,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, 9, tzinfo=UTC)


def raw(**fields: object) -> RawPosting:
    base: dict[str, object] = {
        "source": Source.INDEED,
        "source_id": "in-1",
        "title": "Serveur H/F",
        "description": "Service en salle.",
    }
    base.update(fields)
    return RawPosting(**base)  # type: ignore[arg-type]


class TestTexte:
    def test_le_pliage_retire_accents_casse_et_blancs(self) -> None:
        assert fold("  Éducateur   SPÉCIALISÉ ") == "educateur specialise"

    def test_un_texte_absent_devient_vide(self) -> None:
        assert clean_text(None) == ""

    def test_les_blancs_et_lignes_vides_sont_resserres(self) -> None:
        assert (
            clean_text("Missions :\r\n\n\n\n -  service  \t en salle")
            == "Missions :\n\n- service en salle"
        )

    def test_une_description_trop_longue_est_coupee_sur_un_mot(self) -> None:
        text = clean_text("mot " * DESCRIPTION_MAX_LENGTH)
        assert len(text) <= DESCRIPTION_MAX_LENGTH
        assert text.endswith("mot")

    def test_un_titre_tient_sur_une_ligne(self) -> None:
        assert clean_title("Serveur\n  H/F ") == "Serveur H/F"


class TestContrat:
    @pytest.mark.parametrize(
        ("title", "description", "expected"),
        [
            ("Alternance - Développeur web H/F", "", ContractType.ALTERNANCE),
            (
                "Assistant RH",
                "Contrat d'apprentissage de 24 mois, rythme 3 semaines / 1 semaine.",
                ContractType.ALTERNANCE,
            ),
            ("Stage - Data Analyst (6 mois)", "", ContractType.STAGE),
            (
                "Préparateur de commandes",
                "Mission d'intérim de 3 mois renouvelable.",
                ContractType.INTERIM,
            ),
            ("Saisonnier vendanges", "", ContractType.SAISONNIER),
            ("Développeur Freelance React", "", ContractType.FREELANCE),
            (
                "Infirmier",
                "Poste en CDD de 6 mois, remplacement congé maternité.",
                ContractType.CDD,
            ),
            ("Comptable", "Nous recrutons en CDI à temps plein.", ContractType.CDI),
        ],
    )
    def test_le_contrat_se_lit_dans_le_texte(
        self, title: str, description: str, expected: ContractType
    ) -> None:
        assert detect_contract(title, description, ()) is expected

    def test_le_premier_contrat_cite_lemporte(self) -> None:
        text = "Alternance en CDD de 12 mois au sein de notre agence."
        assert detect_contract("Chargé de recrutement", text, ()) is ContractType.ALTERNANCE

    def test_le_type_de_la_source_sert_ensuite(self) -> None:
        assert (
            detect_contract("Vendeur", "Rejoignez-nous.", ("fulltime", "temporary"))
            is ContractType.CDD
        )
        assert detect_contract("Vendeur", "Rejoignez-nous.", ("internship",)) is ContractType.STAGE

    def test_le_contrat_dit_en_bas_dannonce_est_lu_en_dernier(self) -> None:
        description = "Présentation de la maison. " * 80 + "Type d'emploi : CDI"
        assert detect_contract("Boulanger", description, ()) is ContractType.CDI

    def test_le_defaut_de_la_source_passe_avant_le_bas_dannonce(self) -> None:
        description = "x " * 800 + "CDD"
        assert (
            detect_contract("Vendeur", description, (), default=ContractType.CDI)
            is ContractType.CDI
        )

    def test_sans_indice_on_ne_sait_pas(self) -> None:
        assert detect_contract("Serveur", "Service en salle.", ("fulltime",)) is None

    def test_une_carte_hellowork_sans_type_de_contrat_est_un_cdi(self) -> None:
        assert source_default_contract(Source.HELLOWORK, ("fulltime",)) is ContractType.CDI

    def test_une_carte_hellowork_typee_nest_pas_deduite(self) -> None:
        assert source_default_contract(Source.HELLOWORK, ("Temporary", "fulltime")) is None

    def test_indeed_ne_permet_aucune_deduction(self) -> None:
        assert source_default_contract(Source.INDEED, ("fulltime",)) is None


class TestTeletravail:
    def test_le_teletravail_complet(self) -> None:
        assert (
            detect_remote(None, None, "Téléconseiller", "Poste en 100 % télétravail.")
            is RemoteMode.REMOTE
        )

    def test_le_type_de_la_source_dit_le_teletravail(self) -> None:
        assert detect_remote(None, "Remote", "Dev", "") is RemoteMode.REMOTE
        assert detect_remote(None, "Hybrid", "Dev", "") is RemoteMode.HYBRID
        assert detect_remote(None, "On site", "Dev", "") is RemoteMode.ON_SITE

    def test_lhybride_se_lit_dans_le_texte(self) -> None:
        text = "Organisation : 2 jours de télétravail par semaine."
        assert detect_remote(None, None, "Chef de projet", text) is RemoteMode.HYBRID

    def test_le_drapeau_de_la_source_vaut_teletravail(self) -> None:
        assert detect_remote(True, None, "Rédacteur", "Rédaction d'articles.") is RemoteMode.REMOTE

    def test_une_simple_mention_vaut_une_part_de_teletravail(self) -> None:
        assert detect_remote(None, None, "Juriste", "Possibilité de remote.") is RemoteMode.HYBRID

    def test_sans_indice_on_ne_sait_pas(self) -> None:
        assert detect_remote(False, None, "Serveur", "Service en salle.") is None


class TestTempsDeTravail:
    def test_le_temps_partiel_du_titre(self) -> None:
        assert detect_schedule((), "Hôte de caisse - temps partiel", "") is Schedule.PART_TIME

    def test_un_volume_horaire_dit_le_temps(self) -> None:
        assert detect_schedule((), "Caissier", "Contrat de 24h par semaine.") is Schedule.PART_TIME
        assert detect_schedule((), "Caissier", "Contrat de 35h par semaine.") is Schedule.FULL_TIME

    def test_le_type_de_la_source_sert_ensuite(self) -> None:
        assert detect_schedule(("parttime",), "Vendeur", "") is Schedule.PART_TIME
        assert detect_schedule(("fulltime",), "Vendeur", "") is Schedule.FULL_TIME

    def test_sans_indice_on_ne_sait_pas(self) -> None:
        assert detect_schedule((), "Vendeur", "Conseil client.") is None


class TestExperience:
    def test_les_debutants_acceptes(self) -> None:
        assert (
            detect_experience(None, "Vendeur", "Débutants acceptés, formation assurée.")
            is ExperienceLevel.BEGINNER
        )

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "Vous avez au moins 2 ans d'expérience sur un poste similaire.",
                ExperienceLevel.JUNIOR,
            ),
            ("Expérience de 5 ans minimum exigée.", ExperienceLevel.CONFIRMED),
            ("10 ans d'expérience en management.", ExperienceLevel.SENIOR),
        ],
    )
    def test_les_annees_explicites(self, text: str, expected: ExperienceLevel) -> None:
        assert detect_experience(None, "Responsable", text) is expected

    def test_les_mots_du_titre(self) -> None:
        assert detect_experience(None, "Développeur Junior", "") is ExperienceLevel.JUNIOR
        assert detect_experience(None, "Stagiaire comptable", "") is ExperienceLevel.BEGINNER
        assert detect_experience(None, "Lead Developer", "") is ExperienceLevel.SENIOR
        assert detect_experience(None, "Comptable confirmé", "") is ExperienceLevel.CONFIRMED

    def test_le_niveau_de_la_source_en_dernier(self) -> None:
        assert detect_experience("Entry level", "Analyste", "") is ExperienceLevel.BEGINNER
        assert detect_experience("Inconnu", "Analyste", "") is None

    def test_les_paliers_dannees(self) -> None:
        assert [level_for_years(y) for y in (0, 1, 3, 7)] == [
            ExperienceLevel.BEGINNER,
            ExperienceLevel.JUNIOR,
            ExperienceLevel.CONFIRMED,
            ExperienceLevel.SENIOR,
        ]


class TestSalaire:
    def test_une_fourchette_annuelle(self) -> None:
        assert parse_salary_text("Salaire : 45 000 - 60 000 € / an") == (3750, 5000)

    def test_un_salaire_en_k_euros(self) -> None:
        assert parse_salary_text("Rémunération 40k€ brut annuel") == (3333, None)

    def test_un_taux_horaire(self) -> None:
        assert parse_salary_text("12,31 € de l'heure") == (round(12.31 * HOURS_PER_MONTH), None)

    def test_un_tjm(self) -> None:
        assert parse_salary_text("TJM : 450 € par jour") == (9752, None)

    def test_un_mensuel(self) -> None:
        assert parse_salary_text("Salaire de 1 900 € brut mensuel") == (1900, None)

    def test_un_petit_montant_sans_unite_est_horaire(self) -> None:
        assert parse_salary_text("Rémunération : 13 €") == (round(13 * HOURS_PER_MONTH), None)

    def test_le_point_des_milliers(self) -> None:
        assert parse_salary_text("2.100 € par mois") == (2100, None)

    def test_les_bornes_inversees_sont_remises_dans_lordre(self) -> None:
        assert parse_salary_text("de 2 400 à 2 000 € par mois") == (2000, 2400)

    def test_un_montant_invraisemblable_est_ignore(self) -> None:
        assert parse_salary_text("Prime de 50 000 000 €") == (None, None)

    def test_sans_somme_en_euros_rien(self) -> None:
        assert parse_salary_text("Selon profil") == (None, None)

    def test_un_nombre_illisible_rien(self) -> None:
        assert parse_salary_text("salaire : .. €") == (None, None)

    def test_un_montant_reconnu_se_lit_avec_ses_milliers(self) -> None:
        assert _to_number("2,5", "k") == 2500
        assert _to_number("1.234", None) == 1234

    def test_les_bornes_structurees_de_la_source_priment(self) -> None:
        salary = normalize_salary(
            raw(salary_min=14.54, salary_max=14.54, salary_interval="hourly", salary_currency="EUR")
        )
        assert salary.min_monthly == salary.max_monthly == round(14.54 * HOURS_PER_MONTH)
        assert salary.raw == "14 - 14 EUR (hourly)"

    def test_le_texte_de_la_source_est_garde(self) -> None:
        salary = normalize_salary(
            raw(salary_max=30_000, salary_interval="yearly", raw={"salary_text": "30 k€ par an"})
        )
        assert (salary.min_monthly, salary.max_monthly, salary.raw) == (None, 2500, "30 k€ par an")

    def test_une_devise_etrangere_se_rabat_sur_le_texte(self) -> None:
        salary = normalize_salary(
            raw(
                salary_min=66_000,
                salary_interval="yearly",
                salary_currency="USD",
                description="Selon profil",
            )
        )
        assert not salary.is_known

    def test_des_bornes_invraisemblables_se_rabattent_sur_le_texte(self) -> None:
        salary = normalize_salary(
            raw(salary_min=1, salary_interval="monthly", description="Salaire : 2 000 € par mois")
        )
        assert salary.min_monthly == 2000
        assert salary.raw is None


class TestLieu:
    def test_ville_et_departement(self) -> None:
        assert parse_location_label("Lyon (69)") == LocationHint(
            query="Lyon", city="Lyon", department_code="69"
        )

    def test_code_postal_et_ville(self) -> None:
        hint = parse_location_label("75011 Paris")
        assert hint == LocationHint(
            query="75011 Paris", city="Paris", postal_code="75011", department_code="75"
        )

    def test_une_adresse_indeed(self) -> None:
        assert parse_location_label("Nanterre, Île-de-France, France").city == "Nanterre"

    def test_un_lieu_generique_ne_dit_rien(self) -> None:
        assert parse_location_label("Télétravail") == LocationHint(query=None)
        assert parse_location_label(None) == LocationHint(query=None)

    def test_une_tete_generique_garde_le_departement(self) -> None:
        hint = parse_location_label("France (75)")
        assert hint.city is None
        assert hint.department_code == "75"
        assert hint.query is None

    @pytest.mark.parametrize(
        ("postal", "department"),
        [("69003", "69"), ("97411", "974"), ("20090", "2A"), ("20200", "2B")],
    )
    def test_le_departement_dun_code_postal(self, postal: str, department: str) -> None:
        assert department_from_postal(postal) == department

    def test_le_geocodeur_donne_les_coordonnees(self) -> None:
        place = GeocodedPlace(
            city="Lyon", postal_code="69003", department_code="69", latitude=45.76, longitude=4.85
        )
        location = build_location(parse_location_label("Lyon 3e"), place, " Lyon 3e ")
        assert location is not None
        assert (location.label, location.city, location.is_geocoded) == ("Lyon 3e", "Lyon", True)

    def test_sans_libelle_le_geocodeur_nomme_le_lieu(self) -> None:
        place = GeocodedPlace(
            city="Lyon", postal_code=None, department_code=None, latitude=45.7, longitude=4.8
        )
        location = build_location(
            LocationHint(query="Lyon", postal_code="69001", department_code="69"), place, None
        )
        assert location is not None
        assert (location.label, location.postal_code, location.department_code) == (
            "Lyon",
            "69001",
            "69",
        )

    def test_sans_geocodage_le_libelle_reste(self) -> None:
        location = build_location(parse_location_label("Lyon (69)"), None, "Lyon (69)")
        assert location is not None
        assert not location.is_geocoded
        assert location.department_code == "69"

    def test_sans_geocodage_ni_libelle_la_ville_sert(self) -> None:
        location = build_location(LocationHint(query="Lyon", city="Lyon"), None, None)
        assert location is not None
        assert location.label == "Lyon"
        assert (
            build_location(LocationHint(query=None, department_code="69"), None, None) is not None
        )

    def test_rien_a_comprendre_pas_de_lieu(self) -> None:
        assert build_location(LocationHint(query=None), None, "France") is None


class TestAnnonce:
    def test_la_date_de_parution_vient_de_la_source(self) -> None:
        assert published_at_of(raw(date_posted=date(2026, 10, 20)), now=NOW) == datetime(
            2026, 10, 20, tzinfo=UTC
        )

    def test_sans_date_cest_maintenant(self) -> None:
        assert published_at_of(raw(), now=NOW) == NOW

    def test_une_date_future_est_ramenee_a_maintenant(self) -> None:
        assert published_at_of(raw(date_posted=(NOW + timedelta(days=2)).date()), now=NOW) == NOW

    def test_une_annonce_hellowork_complete(self) -> None:
        posting = raw(
            source=Source.HELLOWORK,
            source_id="hw-84683722",
            title="Serveur - Serveuse Mariage H/F",
            description="Temporis Vienne recherche des serveurs.\nService à l'assiette. Débutants acceptés.",
            company_name="Temporis Interim",
            location_label="Vienne, 38, France",
            date_posted=date(2026, 10, 22),
            job_types=("fulltime",),
            job_url="https://www.hellowork.com/fr-fr/emplois/84683722.html",
        )
        place = GeocodedPlace(
            city="Vienne", postal_code="38200", department_code="38", latitude=45.52, longitude=4.87
        )
        offer = normalize_posting(posting, now=NOW, place=place)
        assert offer.company is not None
        assert offer.company.name == "Temporis Interim"
        assert offer.contract_type is ContractType.CDI
        assert offer.schedule is Schedule.FULL_TIME
        assert offer.experience is ExperienceLevel.BEGINNER
        assert offer.location is not None
        assert offer.location.department_code == "38"
        assert offer.apply_url == offer.origin_url == posting.job_url
        assert offer.first_seen_at == offer.last_seen_at == NOW

    def test_une_annonce_sans_employeur_et_avec_lien_direct(self) -> None:
        offer = normalize_posting(
            raw(
                company_name="  ", job_url="https://indeed/1", job_url_direct="https://employeur/1"
            ),
            now=NOW,
            place=None,
            offer_id=None,
        )
        assert offer.company is None
        assert offer.apply_url == "https://employeur/1"
        assert offer.origin_url == "https://indeed/1"


class TestIndicesStructures:
    """Ce qu'une source structurée (France Travail) dit explicitement prime sur le texte."""

    def test_le_contrat_de_la_source_prime_sur_le_texte(self) -> None:
        offer = normalize_posting(
            raw(
                source=Source.FRANCE_TRAVAIL,
                title="Cuisinier",
                description="Un CDD a précédé ce poste.",
                contract_type="cdi",
            ),
            now=NOW,
            place=None,
        )
        assert offer.contract_type is ContractType.CDI

    def test_un_indice_hors_domaine_vaut_absent(self) -> None:
        offer = normalize_posting(
            raw(contract_type="CCE", schedule="35h", experience="expert"), now=NOW, place=None
        )
        assert (offer.contract_type, offer.schedule, offer.experience) == (None, None, None)

    def test_le_temps_de_travail_et_l_experience_de_la_source(self) -> None:
        offer = normalize_posting(
            raw(schedule="temps_partiel", experience="debutant", description="Rejoignez-nous."),
            now=NOW,
            place=None,
        )
        assert (offer.schedule, offer.experience) == (Schedule.PART_TIME, ExperienceLevel.BEGINNER)

    @pytest.mark.parametrize(
        ("years", "expected"),
        [
            (0.5, ExperienceLevel.BEGINNER),
            (1, ExperienceLevel.JUNIOR),
            (5, ExperienceLevel.CONFIRMED),
        ],
    )
    def test_les_annees_chiffrees_par_la_source(
        self, years: float, expected: ExperienceLevel
    ) -> None:
        assert detect_experience(None, "Cuisinier", "", years=years) is expected

    def test_le_libelle_de_salaire_de_la_source_est_lu_en_premier(self) -> None:
        salary = normalize_salary(
            raw(salary_text="Horaire de 12.31 Euros à 18.47 Euros", description="2 000 € par mois")
        )
        assert salary.min_monthly == round(12.31 * HOURS_PER_MONTH)
        assert salary.max_monthly == round(18.47 * HOURS_PER_MONTH)
        assert salary.raw == "Horaire de 12.31 Euros à 18.47 Euros"

    def test_un_libelle_de_salaire_muet_rend_la_main_au_texte(self) -> None:
        salary = normalize_salary(raw(salary_text="Selon profil", description="2 000 € par mois"))
        assert (salary.min_monthly, salary.raw) == (2000, "Selon profil")

    def test_un_libelle_mensuel_france_travail(self) -> None:
        salary = normalize_salary(raw(salary_text="Mensuel de 2300.0 Euros - Selon expérience"))
        assert (salary.min_monthly, salary.max_monthly) == (2300, None)

    def test_un_libelle_annuel_france_travail(self) -> None:
        salary = normalize_salary(raw(salary_text="Annuel de 27000.0 Euros à 28200.0 Euros"))
        assert (salary.min_monthly, salary.max_monthly) == (2250, 2350)

    def test_la_source_qui_situe_ses_annonces_evite_le_geocodeur(self) -> None:
        posting = raw(
            location_label="Fleure (86)", latitude=46.47, longitude=0.52, postal_code="86340"
        )
        place = source_place(posting)
        assert place == GeocodedPlace(
            city="Fleure", postal_code="86340", department_code="86", latitude=46.47, longitude=0.52
        )
        offer = normalize_posting(posting, now=NOW, place=place)
        assert offer.location is not None
        assert (
            offer.location.city,
            offer.location.department_code,
            offer.location.is_geocoded,
        ) == (
            "Fleure",
            "86",
            True,
        )

    def test_sans_coordonnees_la_source_ne_situe_rien(self) -> None:
        assert source_place(raw(location_label="Paris", latitude=48.8)) is None

    def test_le_departement_vient_du_code_postal_quand_le_libelle_ne_le_dit_pas(self) -> None:
        place = source_place(
            raw(location_label="Paris 08", latitude=48.8, longitude=2.3, postal_code="75008")
        )
        assert place is not None
        assert (place.city, place.department_code) == ("Paris 08", "75")

    def test_sans_libelle_ni_code_postal_le_lieu_est_anonyme(self) -> None:
        place = source_place(raw(location_label=None, latitude=48.8, longitude=2.3))
        assert place is not None
        assert (place.city, place.postal_code, place.department_code) == ("", None, None)

    def test_la_parution_a_la_seconde_prime_sur_le_jour(self) -> None:
        moment = datetime(2026, 10, 21, 14, 30, tzinfo=UTC)
        posting = raw(posted_at=moment, date_posted=date(2026, 10, 20))
        assert published_at_of(posting, now=NOW) == moment
        assert published_at_of(raw(posted_at=NOW + timedelta(hours=1)), now=NOW) == NOW
