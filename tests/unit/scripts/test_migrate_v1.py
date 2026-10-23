"""La migration V1 → V2 : la correspondance pure, sans base ni modèle de langage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from scripts.migrate_v1 import (
    IMPORT_CONSENT_VERSION,
    TITLES_MAX,
    TOP_TERMS_MAX,
    ImportPlan,
    TokenKind,
    anonymous_samples,
    build_export_record,
    classify_token,
    convert_record,
    deterministic_criteria,
    fold_term,
    installation_id_for,
    is_title_term,
    location_from,
    map_contract,
    map_contracts,
    map_experience,
    normalise_email,
    parse_moment,
    pick_titles,
    plan_import,
    rank_terms,
    refine_with_llm,
    searchable_once_located,
    summarise_convert,
    summarise_export,
)

from karria.modules.offers.domain import (
    DEFAULT_RADIUS_KM,
    ContractType,
    ExperienceLevel,
    LocationCriteria,
    SearchCriteria,
)

pytestmark = pytest.mark.unit

EXPO_PUSH = "ExponentPushToken[abcDEF123_-]"
LEGACY_EXPO_PUSH = "ExpoPushToken[xyz]"
FCM_PUSH = "d" * 20 + ":" + "APA91b" + "x" * 120
CREATED = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)

LILLE = {
    "name": "Lille",
    "postal_code": "59000",
    "lat": 50.63,
    "lng": 3.06,
    "department_code": "59",
}
LYON = {"name": "Lyon", "postal_code": "69001", "lat": 45.76, "lng": 4.83, "department_code": "69"}


def make_source(**changes: Any) -> dict[str, Any]:
    source: dict[str, Any] = {
        "search_intent_raw": "Je cherche un poste de serveur à Lille, CDI ou CDD.",
        "keywords": {
            "keywords": ["serveur", "chef de rang", "serveuse"],
            "job_types": ["CDI", "temps plein"],
            "sectors": ["restauration", "hôtellerie"],
            "contract_codes": ["CDI", "CDD"],
            "experience": "junior",
            "summary": "Serveur en restauration à Lille",
        },
        "whole_france": False,
        "cities": [LILLE, LYON],
        "current_city": None,
        "contract_codes": ["CDI", "CDD", "MIS"],
        "experience_code": "JUNIOR",
        "notifications_enabled": True,
        "top_search_terms": [
            {"term": "temps plein", "count": 9},
            {"term": "serveur", "count": 7},
            {"term": "cdi", "count": 5},
            {"term": "commis de salle", "count": 2},
        ],
    }
    source.update(changes)
    return source


def make_record(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "v1_user_id": 7,
        "email": "a@exemple.fr",
        "email_verified": True,
        "is_active": True,
        "oauth_provider": "google",
        "created_at": CREATED.isoformat(),
        "last_login": None,
        "push": {
            "token": EXPO_PUSH,
            "kind": "expo",
            "provider": "expo",
            "platform": "ios",
            "updated_at": None,
        },
        "criteria": make_source(),
    }
    record.update(changes)
    return record


# --- Jetons ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "kind"),
    [
        (None, TokenKind.NONE),
        ("   ", TokenKind.NONE),
        (EXPO_PUSH, TokenKind.EXPO),
        (LEGACY_EXPO_PUSH, TokenKind.EXPO),
        (FCM_PUSH, TokenKind.FCM),
        ("abc", TokenKind.OTHER),
    ],
)
def test_un_jeton_est_reconnu_par_sa_forme(token: str | None, kind: TokenKind):
    assert classify_token(token) is kind


def test_seul_un_jeton_expo_est_utilisable():
    assert TokenKind.EXPO.usable
    assert not TokenKind.FCM.usable
    assert not TokenKind.NONE.usable


# --- Normalisations ----------------------------------------------------------


def test_une_adresse_est_mise_en_minuscules_sans_blancs():
    assert normalise_email("  Marc@Exemple.FR ") == "marc@exemple.fr"


def test_le_repliement_retire_accents_casse_et_blancs():
    assert fold_term("  Hôte   de  Caisse ") == "hote de caisse"


# --- Contrats et expérience ----------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, None),
        ("CDI", ContractType.CDI),
        (" din ", ContractType.CDI),
        ("DDI", ContractType.CDD),
        ("MIS", ContractType.INTERIM),
        ("TTI", ContractType.INTERIM),
        ("SAI", ContractType.SAISONNIER),
        ("LIB", ContractType.FREELANCE),
        ("ALT", ContractType.ALTERNANCE),
        ("STA", ContractType.STAGE),
        ("FRA", None),
        ("NS", None),
    ],
)
def test_un_code_de_contrat_v1_devient_un_contrat_v2_ou_tombe(
    code: str | None, expected: ContractType | None
):
    assert map_contract(code) is expected


def test_les_contrats_sont_dedoublonnes_dans_l_ordre():
    assert map_contracts(["CDD", "DDI", "CDI", None, "FRA", "CDD"]) == [
        ContractType.CDD,
        ContractType.CDI,
    ]


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (("BEGINNER",), ExperienceLevel.BEGINNER),
        (("débutant",), ExperienceLevel.BEGINNER),
        (("INTERMEDIATE",), ExperienceLevel.CONFIRMED),
        (("intermédiaire",), ExperienceLevel.CONFIRMED),
        (("EXPERT",), ExperienceLevel.SENIOR),
        (("EXECUTIVE",), ExperienceLevel.SENIOR),
        ((None, "junior"), ExperienceLevel.JUNIOR),
        ((None, "inconnu"), None),
        ((), None),
    ],
)
def test_le_premier_niveau_reconnu_l_emporte(
    labels: tuple[str | None, ...], expected: ExperienceLevel | None
):
    assert map_experience(*labels) is expected


# --- Termes et intitulés -------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "is_title"),
    [
        ("serveur", True),
        ("Temps Plein", False),
        ("CDI", False),
        ("Télétravail", False),
        ("", False),
    ],
)
def test_un_contrat_ou_un_rythme_n_est_pas_un_intitule(term: str, is_title: bool):
    assert is_title_term(term) is is_title


def test_les_termes_sont_comptes_par_profil_et_la_premiere_graphie_gardee():
    ranked = rank_terms(
        [(1, "Serveur"), (1, "serveur"), (1, "  "), (1, "chef de rang"), (2, "caissier")]
    )
    assert ranked == {
        1: [{"term": "Serveur", "count": 2}, {"term": "chef de rang", "count": 1}],
        2: [{"term": "caissier", "count": 1}],
    }


def test_au_plus_dix_termes_par_profil():
    ranked = rank_terms((1, f"metier {i}") for i in range(TOP_TERMS_MAX + 5))
    assert len(ranked[1]) == TOP_TERMS_MAX


def test_les_intitules_viennent_des_termes_puis_des_mots_cles_sans_contrats():
    titles = pick_titles(
        [{"term": "temps plein", "count": 9}, {"term": "Serveur", "count": 7}],
        ["serveur", "CDI", "chef de rang"],
    )
    assert titles == ["Serveur", "chef de rang"]


def test_les_intitules_sont_plafonnes():
    titles = pick_titles([], [f"metier {i}" for i in range(TITLES_MAX + 3)])
    assert len(titles) == TITLES_MAX


# --- Lieu --------------------------------------------------------------------


def test_toute_la_france_prime_sur_les_villes():
    assert location_from(make_source(whole_france=True)) == (None, True)


def test_la_premiere_ville_choisie_donne_le_lieu():
    location, whole_france = location_from(make_source())
    assert not whole_france
    assert location == LocationCriteria(
        label="Lille (59)",
        latitude=50.63,
        longitude=3.06,
        radius_km=DEFAULT_RADIUS_KM,
        department_code="59",
        city="Lille",
    )


def test_sans_ville_choisie_la_position_courante_sert():
    source = make_source(
        cities=[],
        current_city={"name": "Nantes", "postal_code": "44000", "lat": 47.2, "lng": -1.55},
    )
    location, _ = location_from(source)
    assert location is not None
    assert location.label == "Nantes"
    assert location.department_code is None
    assert location.is_geocoded


def test_une_position_sans_coordonnees_ne_fait_pas_un_lieu():
    source = make_source(cities=[], current_city={"name": "Nantes", "lat": None, "lng": None})
    assert location_from(source) == (None, False)


def test_une_ville_sans_nom_garde_un_libelle():
    location, _ = location_from(make_source(cities=[{"name": "", "lat": 1.0, "lng": 2.0}]))
    assert location is not None
    assert location.label == "lieu V1"
    assert location.city is None


# --- Critères déterministes ----------------------------------------------------


def test_sans_profil_pas_de_criteres():
    assert deterministic_criteria(None) is None


def test_un_profil_complet_donne_des_criteres_cherchables():
    criteria = deterministic_criteria(make_source())
    assert criteria is not None
    assert criteria.is_searchable
    assert criteria.job_titles == ["serveur", "commis de salle", "chef de rang", "serveuse"]
    assert criteria.search_terms == ["serveur", "commis de salle", "chef de rang"]
    assert criteria.query == "Serveur en restauration à Lille"
    assert criteria.semantic_query.startswith("Je cherche un poste de serveur")
    assert criteria.preferences == ["restauration", "hôtellerie"]
    assert criteria.contract_types == [ContractType.CDI, ContractType.CDD, ContractType.INTERIM]
    assert criteria.max_experience is ExperienceLevel.JUNIOR
    assert criteria.location is not None
    assert criteria.location.city == "Lille"


def test_sans_contrats_choisis_ceux_de_l_intention_servent():
    criteria = deterministic_criteria(make_source(contract_codes=[]))
    assert criteria is not None
    assert criteria.contract_types == [ContractType.CDI, ContractType.CDD]


def test_la_requete_se_replie_sur_l_intention_puis_l_intitule_puis_un_defaut():
    keywords = {"keywords": ["serveur"]}
    from_intent = deterministic_criteria(
        make_source(keywords=keywords, search_intent_raw="x" * 300)
    )
    assert from_intent is not None
    assert from_intent.query == "x" * 200
    from_title = deterministic_criteria(make_source(keywords=keywords, search_intent_raw=""))
    assert from_title is not None
    assert from_title.query == "serveur"
    default = deterministic_criteria(
        make_source(keywords={}, search_intent_raw="", top_search_terms=[])
    )
    assert default is not None
    assert default.query == "Recherche V1"
    assert not default.is_searchable


def test_l_experience_se_replie_sur_le_libelle_de_l_intention():
    criteria = deterministic_criteria(make_source(experience_code=None))
    assert criteria is not None
    assert criteria.max_experience is ExperienceLevel.JUNIOR


# --- Affinage par le modèle ----------------------------------------------------


def _proposal(**changes: Any) -> SearchCriteria:
    payload: dict[str, Any] = {
        "query": "Serveur à Lille",
        "job_titles": ["serveur", "serveuse"],
        "location": {"label": "Lille"},
    }
    payload.update(changes)
    return SearchCriteria.model_validate(payload)


def test_sans_proposition_cherchable_le_deterministe_reste():
    base = deterministic_criteria(make_source())
    assert refine_with_llm(base, None) == (base, "deterministic")
    assert refine_with_llm(base, _proposal(job_titles=[], location=None)) == (
        base,
        "deterministic",
    )
    assert refine_with_llm(None, None) == (None, "none")


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"job_titles": []}, False),
        ({"location": None}, False),
        ({"location": {"label": "   "}}, False),
        ({"location": None, "whole_france": True}, True),
        ({"location": None, "remote": ["teletravail"]}, True),
    ],
)
def test_un_lieu_seulement_nomme_suffit_une_fois_geocode(changes: dict[str, Any], expected: bool):
    assert searchable_once_located(_proposal(**changes)) is expected


def test_une_proposition_au_lieu_seulement_nomme_est_retenue_sans_base():
    proposed = _proposal()
    assert not proposed.is_searchable
    assert refine_with_llm(None, proposed) == (proposed, "llm")


def test_sans_base_la_proposition_cherchable_est_prise_telle_quelle():
    proposed = _proposal(whole_france=True)
    assert refine_with_llm(None, proposed) == (proposed, "llm")


def test_la_v1_complete_la_proposition_lieu_contrats_experience():
    base = deterministic_criteria(make_source())
    merged, origin = refine_with_llm(base, _proposal())
    assert origin == "llm+v1"
    assert merged is not None
    assert merged.job_titles == ["serveur", "serveuse"]
    assert merged.location is not None
    assert merged.location.is_geocoded
    assert merged.location.city == "Lille"
    assert not merged.whole_france
    assert merged.contract_types == [ContractType.CDI, ContractType.CDD, ContractType.INTERIM]
    assert merged.max_experience is ExperienceLevel.JUNIOR


def test_une_proposition_deja_complete_n_est_pas_modifiee():
    base = deterministic_criteria(make_source())
    proposed = _proposal(
        location={"label": "Lyon", "latitude": 45.76, "longitude": 4.83},
        contract_types=["stage"],
        max_experience="senior",
    )
    merged, origin = refine_with_llm(base, proposed)
    assert origin == "llm+v1"
    assert merged == proposed


# --- Identifiants et dates -------------------------------------------------------


def test_l_identifiant_d_installation_est_stable_et_un_uuid():
    first, second = installation_id_for(EXPO_PUSH), installation_id_for(EXPO_PUSH)
    assert first == second
    assert UUID(first).version == 5
    assert installation_id_for(LEGACY_EXPO_PUSH) != first


def test_une_date_sans_fuseau_est_lue_en_utc():
    assert parse_moment(None) is None
    assert parse_moment("") is None
    assert parse_moment("2025-03-01T10:00:00") == CREATED
    assert parse_moment("2025-03-01T12:00:00+02:00") == CREATED


# --- Export --------------------------------------------------------------------


def _user_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 7,
        "email": " Marc@Exemple.FR ",
        "oauth_provider": "google",
        "is_active": True,
        "is_email_verified": True,
        "created_at": CREATED,
        "last_login": None,
        "fcm_token": FCM_PUSH,
        "push_provider": "fcm",
        "push_platform": "ios",
        "push_token_updated_at": CREATED,
    }
    row.update(changes)
    return row


def _profile_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 3,
        "user_id": 7,
        "search_intent_raw": "Serveur à Lille",
        "search_intent_keywords": {
            "keywords": ["serveur"],
            "job_types": ["CDI"],
            "competences": ["service"],
            "sectors": [],
            "contract_codes": ["CDI"],
            "experience": "junior",
            "summary": "Serveur",
            "_intent_model": "x",
            "expected_salary_max": 2000,
            "desired_salary_min": None,
        },
        "whole_france": False,
        "notifications_enabled": True,
        "current_latitude": 50.63,
        "current_longitude": 3.06,
        "current_city_name": "Lille",
        "current_postal_code": "59000",
        "experience_code": "JUNIOR",
    }
    row.update(changes)
    return row


def test_l_export_ne_garde_que_l_adresse_normalisee_et_la_matiere_des_criteres():
    record = build_export_record(
        user=_user_row(),
        profile=_profile_row(),
        cities=[
            {
                "name": "Lille",
                "postal_code": "59000",
                "latitude": 50.63,
                "longitude": 3.06,
                "department_code": "59",
            }
        ],
        contract_codes=["CDI", "MIS"],
        experience_code="JUNIOR",
        top_terms=[{"term": "serveur", "count": 3}],
    )
    assert record["email"] == "marc@exemple.fr"
    assert set(record) == {
        "v1_user_id",
        "email",
        "email_verified",
        "is_active",
        "oauth_provider",
        "created_at",
        "last_login",
        "push",
        "criteria",
    }
    assert record["push"] == {
        "token": FCM_PUSH,
        "kind": "fcm",
        "provider": "fcm",
        "platform": "ios",
        "updated_at": CREATED.isoformat(),
    }
    criteria = record["criteria"]
    assert criteria["cities"] == [LILLE]
    assert criteria["current_city"] == {
        "name": "Lille",
        "postal_code": "59000",
        "lat": 50.63,
        "lng": 3.06,
    }
    assert criteria["contract_codes"] == ["CDI", "MIS"]
    assert criteria["top_search_terms"] == [{"term": "serveur", "count": 3}]
    assert set(criteria["keywords"]) == {
        "keywords",
        "job_types",
        "competences",
        "contract_codes",
        "experience",
        "summary",
    }


def test_un_compte_sans_profil_ni_jeton_s_exporte_quand_meme():
    record = build_export_record(
        user=_user_row(fcm_token=None, created_at=None),
        profile=None,
        cities=[],
        contract_codes=[],
        experience_code=None,
        top_terms=[],
    )
    assert record["push"] is None
    assert record["criteria"] is None
    assert record["created_at"] is None


def test_un_profil_sans_position_courante_n_en_invente_pas():
    record = build_export_record(
        user=_user_row(),
        profile=_profile_row(current_latitude=None, search_intent_keywords=None),
        cities=[],
        contract_codes=[],
        experience_code=None,
        top_terms=[],
    )
    assert record["criteria"]["current_city"] is None
    assert record["criteria"]["keywords"] == {}


# --- Conversion ------------------------------------------------------------------


def test_la_conversion_garde_la_provenance_et_les_notes():
    converted = convert_record(make_record())
    assert converted["source"] == "deterministic"
    assert converted["notes"] == []
    assert converted["notifications_enabled"] is True
    assert SearchCriteria.model_validate(converted["criteria"]).is_searchable


def test_la_conversion_note_les_criteres_incomplets_et_les_jetons_inutilisables():
    record = make_record(
        criteria=make_source(whole_france=False, cities=[], current_city=None),
        push={"token": FCM_PUSH, "kind": "fcm", "provider": "fcm", "platform": "ios"},
    )
    converted = convert_record(record)
    assert converted["notes"] == ["incomplete:location", "push_unusable:fcm"]


def test_la_conversion_sans_profil_rend_aucun_critere():
    converted = convert_record(make_record(criteria=None, push=None))
    assert converted["criteria"] is None
    assert converted["source"] == "none"
    assert converted["notifications_enabled"] is False


def test_la_conversion_prend_la_proposition_du_modele_quand_elle_est_donnee():
    converted = convert_record(make_record(), proposed=_proposal())
    assert converted["source"] == "llm+v1"
    assert converted["criteria"]["job_titles"] == ["serveur", "serveuse"]


# --- Plan d'import -----------------------------------------------------------------


def test_un_compte_non_verifie_est_ecarte():
    plan = plan_import(make_record(email_verified=False))
    assert plan == ImportPlan(email="a@exemple.fr", skip_reason="unverified")


def test_un_compte_verifie_avec_jeton_expo_et_criteres_s_importe_entierement():
    converted = convert_record(make_record(email=" A@Exemple.fr ", last_login=CREATED.isoformat()))
    plan = plan_import(converted)
    assert plan.skip_reason is None
    assert plan.email == "a@exemple.fr"
    assert plan.created_at == CREATED
    assert plan.last_login == CREATED
    assert plan.push_token == EXPO_PUSH
    assert plan.push_platform == "ios"
    assert plan.installation_id == installation_id_for(EXPO_PUSH)
    assert plan.criteria is not None
    assert plan.criteria.is_searchable
    assert plan.notes == ()


def test_un_jeton_fcm_ou_une_plateforme_inconnue_ne_donne_pas_d_appareil():
    fcm = plan_import(
        convert_record(make_record(push={"token": FCM_PUSH, "kind": "fcm", "platform": "ios"}))
    )
    assert fcm.push_token is None
    assert fcm.notes == ("push_unusable:fcm",)
    unknown = plan_import(
        convert_record(
            make_record(push={"token": EXPO_PUSH, "kind": "expo", "platform": "unknown"})
        )
    )
    assert unknown.push_token is None
    assert unknown.installation_id is None


def test_des_criteres_incomplets_sont_comptes_et_non_appliques():
    record = make_record(criteria=make_source(cities=[], top_search_terms=[], keywords={}))
    plan = plan_import(convert_record(record))
    assert plan.criteria is None
    assert "criteria_incomplete" in plan.notes


def test_sans_profil_le_plan_n_a_ni_criteres_ni_note():
    plan = plan_import(convert_record(make_record(criteria=None, push=None)))
    assert plan.criteria is None
    assert plan.notes == ()


def test_la_version_de_consentement_identifie_l_import():
    assert IMPORT_CONSENT_VERSION == "v1-import"


# --- Résumés ----------------------------------------------------------------------


def test_le_resume_d_export_ne_contient_que_des_comptes():
    records = [
        make_record(),
        make_record(
            email_verified=False,
            push={"token": FCM_PUSH, "kind": "fcm", "provider": "fcm", "platform": None},
        ),
        make_record(push=None, criteria=None),
    ]
    assert summarise_export(records) == {
        "users": 3,
        "verified": 2,
        "with_profile": 2,
        "with_push_token": 2,
        "push_tokens": {"expo/ios/expo": 1, "fcm/-/fcm": 1},
        "notifications_enabled": 2,
    }


def test_le_resume_de_conversion_distingue_cherchable_incomplet_et_provenance():
    converted = [
        convert_record(make_record()),
        convert_record(make_record(criteria=make_source(cities=[], current_city=None))),
        convert_record(
            make_record(criteria=None, push={"token": FCM_PUSH, "kind": "fcm", "platform": "ios"})
        ),
    ]
    assert summarise_convert(converted) == {
        "users": 3,
        "with_criteria": 2,
        "searchable": 1,
        "incomplete": 1,
        "by_source": {"deterministic": 2, "none": 1},
        "push_unusable": 1,
    }


def test_les_echantillons_sont_des_resumes_sans_adresse():
    converted = [convert_record(make_record(criteria=None)), *[convert_record(make_record())] * 4]
    samples = anonymous_samples(converted, limit=2)
    assert samples == ["[deterministic] serveur · Lille (59) (30 km) · CDI, CDD, Intérim"] * 2
    assert all("@" not in line for line in samples)


def test_sans_criteres_il_n_y_a_pas_d_echantillon():
    assert anonymous_samples([convert_record(make_record(criteria=None))]) == []
