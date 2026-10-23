"""L'adaptateur Qdrant : le JSON exact envoyé, la lecture des réponses, la traduction des pannes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from karria.modules.offers.application import IndexedOffer, IndexQuery
from karria.modules.offers.domain import (
    MAX_RADIUS_KM,
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
    SearchUnavailableError,
    Source,
)
from karria.modules.offers.infrastructure.qdrant import (
    BODY,
    BODY_AVG_WORDS,
    DENSE,
    FUSION_WEIGHTS,
    OFFER_PAYLOAD_INDEXES,
    TITLE,
    TITLE_AVG_WORDS,
    QdrantCriteriaIndex,
    QdrantOfferIndex,
    bm25_document,
    build_transport,
    collection_name,
    criteria_payload,
    criteria_place_filter,
    filter_for,
    offer_payload,
)
from karria.shared.config import QdrantSettings
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)
PARIS = {"lat": 48.8566, "lon": 2.3522}


class FakeTransport:
    """Ce que les collections attendent du client HTTP, sans réseau."""

    def __init__(self, *responses: JsonResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, Any]] = []

    async def request_json(self, method: str, path: str, payload: Any = None) -> JsonResponse:
        self.calls.append((method, path, payload))
        response = self._responses.pop(0) if self._responses else JsonResponse(200, {"result": {}})
        if isinstance(response, Exception):
            raise response
        return response


def make_offer(**changes: Any) -> Offer:
    values: dict[str, Any] = {
        "id": uuid4(),
        "source": Source.INDEED,
        "source_id": "in-1",
        "title": "Serveur H/F",
        "company": Company(name="Le Bistrot"),
        "description": "Service en salle.",
        "location": Location(
            label="Paris (75)",
            city="Paris",
            postal_code="75011",
            department_code="75",
            latitude=PARIS["lat"],
            longitude=PARIS["lon"],
        ),
        "contract_type": ContractType.CDI,
        "remote": RemoteMode.ON_SITE,
        "schedule": Schedule.FULL_TIME,
        "experience": ExperienceLevel.JUNIOR,
        "salary": Salary(min_monthly=1_900, max_monthly=2_100, raw="1900-2100"),
        "published_at": NOW,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "apply_url": None,
        "origin_url": None,
    }
    values.update(changes)
    return Offer(**values)


def criteria(**changes: Any) -> SearchCriteria:
    base: dict[str, Any] = {"query": "serveur à Paris", "job_titles": ["serveur"]}
    base.update(changes)
    return SearchCriteria(**base)


def offer_index(*responses: JsonResponse | Exception) -> tuple[QdrantOfferIndex, FakeTransport]:
    transport = FakeTransport(*responses)
    return QdrantOfferIndex(transport, "karria-test-offers", 4), transport


def criteria_index(
    *responses: JsonResponse | Exception,
) -> tuple[QdrantCriteriaIndex, FakeTransport]:
    transport = FakeTransport(*responses)
    return QdrantCriteriaIndex(transport, "karria-test-criteria", 4), transport


class TestConfiguration:
    def test_le_nom_de_collection_porte_l_environnement(self) -> None:
        name = collection_name(QdrantSettings(collection_prefix="karria"), "production", "offers")
        assert name == "karria-production-offers"

    def test_la_cle_voyage_en_en_tete(self) -> None:
        transport = build_transport(QdrantSettings(api_key="secret", url="http://q:6333"))
        assert isinstance(transport, JsonClient)
        assert transport.headers == {"api-key": "secret"}
        assert transport.base_url == "http://q:6333"

    def test_sans_cle_aucun_en_tete(self) -> None:
        transport = build_transport(QdrantSettings())
        assert isinstance(transport, JsonClient)
        assert transport.headers == {}

    def test_un_document_bm25_demande_le_francais_et_le_pliage(self) -> None:
        assert bm25_document("Serveur", avg_words=7) == {
            "text": "Serveur",
            "model": "qdrant/bm25",
            "options": {"language": "french", "ascii_folding": True, "avg_len": 7},
        }


class TestCreation:
    async def test_une_collection_absente_est_creee_avec_ses_trois_vecteurs(self) -> None:
        index, transport = offer_index(OutboundError("absente", status=404))

        await index.ensure_ready()

        method, path, body = transport.calls[1]
        assert (method, path) == ("PUT", "/collections/karria-test-offers")
        assert body["vectors"][DENSE]["size"] == 4
        assert body["quantization_config"]["scalar"]["type"] == "int8"
        assert body["sparse_vectors"] == {TITLE: {"modifier": "idf"}, BODY: {"modifier": "idf"}}
        fields = [call[2]["field_name"] for call in transport.calls[2:]]
        assert fields == list(OFFER_PAYLOAD_INDEXES)

    async def test_une_collection_existante_ne_recoit_que_ses_index(self) -> None:
        index, transport = offer_index(JsonResponse(200, {"result": {}}))

        await index.ensure_ready()

        assert all(call[1].endswith("/index?wait=true") for call in transport.calls[1:])
        assert len(transport.calls) == 1 + len(OFFER_PAYLOAD_INDEXES)

    async def test_une_panne_a_la_verification_devient_une_indisponibilite(self) -> None:
        index, _ = offer_index(OutboundError("boom", status=500))

        with pytest.raises(SearchUnavailableError):
            await index.ensure_ready()

    async def test_la_collection_des_criteres_est_creee_sans_vecteurs_creux(self) -> None:
        index, transport = criteria_index(OutboundError("absente", status=404))

        await index.ensure_ready()

        body = transport.calls[1][2]
        assert "sparse_vectors" not in body
        assert DENSE in body["vectors"]


class TestEcriture:
    async def test_un_point_porte_le_dense_les_deux_textes_et_le_payload(self) -> None:
        index, transport = offer_index()
        offer = make_offer()

        await index.upsert(
            [IndexedOffer(offer=offer, dense=(0.1, 0.2), title_text="Serveur", body_text="corps")]
        )

        method, path, body = transport.calls[0]
        assert (method, path) == ("PUT", "/collections/karria-test-offers/points?wait=true")
        point = body["points"][0]
        assert point["id"] == str(offer.id)
        assert point["vector"][DENSE] == [0.1, 0.2]
        assert point["vector"][TITLE] == bm25_document("Serveur", avg_words=TITLE_AVG_WORDS)
        assert point["vector"][BODY] == bm25_document("corps", avg_words=BODY_AVG_WORDS)
        assert point["payload"] == offer_payload(offer)

    async def test_rien_a_ecrire_aucun_appel(self) -> None:
        index, transport = offer_index()

        await index.upsert([])
        await index.update_payloads([])
        await index.delete([])

        assert transport.calls == []

    async def test_les_attributs_se_reecrivent_en_un_lot(self) -> None:
        index, transport = offer_index()
        offers = [make_offer(), make_offer()]

        await index.update_payloads(offers)

        method, path, body = transport.calls[0]
        assert (method, path) == ("POST", "/collections/karria-test-offers/points/batch?wait=true")
        assert [op["overwrite_payload"]["points"] for op in body["operations"]] == [
            [str(o.id)] for o in offers
        ]

    async def test_le_retrait_vise_les_identifiants(self) -> None:
        index, transport = offer_index()
        ids = [uuid4(), uuid4()]

        await index.delete(ids)

        assert transport.calls[0][2] == {"points": [str(i) for i in ids]}

    async def test_le_compte_est_exact(self) -> None:
        index, transport = offer_index(JsonResponse(200, {"result": {"count": 42}}))

        assert await index.count() == 42
        assert transport.calls[0][2] == {"exact": True}

    async def test_un_compte_sans_resultat_vaut_zero(self) -> None:
        index, _ = offer_index(JsonResponse(200, {}))

        assert await index.count() == 0

    async def test_une_panne_d_ecriture_devient_une_indisponibilite(self) -> None:
        index, _ = offer_index(OutboundError("injoignable"))

        with pytest.raises(SearchUnavailableError) as raised:
            await index.delete([uuid4()])

        assert raised.value.details["reason"] == "search_unavailable"

    async def test_une_panne_http_nomme_qdrant(self) -> None:
        index, _ = offer_index(OutboundError("refus", status=400))

        with pytest.raises(SearchUnavailableError) as raised:
            await index.count()

        assert raised.value.details["dependency"] == "Qdrant"


class TestRecherche:
    async def test_trois_branches_filtrees_et_une_fusion_ponderee(self) -> None:
        found = uuid4()
        index, transport = offer_index(
            JsonResponse(200, {"result": {"points": [{"id": str(found), "score": 0.5}]}})
        )
        wanted = criteria()

        candidates = await index.search(
            IndexQuery(
                dense=(1.0, 0.0),
                title_terms="serveur",
                body_terms="serveur salle",
                criteria=wanted,
                limit=30,
                offset=10,
            )
        )

        assert [c.offer_id for c in candidates] == [found]
        assert candidates[0].retrieval_score == 0.5
        body = transport.calls[0][2]
        assert [branch["using"] for branch in body["prefetch"]] == [DENSE, TITLE, BODY]
        assert all(branch["filter"] == filter_for(wanted) for branch in body["prefetch"])
        assert body["prefetch"][1]["query"] == bm25_document("serveur", avg_words=TITLE_AVG_WORDS)
        assert body["query"]["rrf"]["weights"] == list(FUSION_WEIGHTS)
        assert (body["limit"], body["offset"]) == (30, 10)
        assert body["prefetch"][0]["params"]["quantization"]["rescore"] is True

    async def test_sans_termes_seule_la_branche_dense_reste(self) -> None:
        index, transport = offer_index(JsonResponse(200, {"result": {}}))

        candidates = await index.search(
            IndexQuery(dense=(1.0,), title_terms=" ", body_terms="", criteria=criteria(), limit=5)
        )

        assert candidates == []
        body = transport.calls[0][2]
        assert [branch["using"] for branch in body["prefetch"]] == [DENSE]
        assert body["query"]["rrf"]["weights"] == [FUSION_WEIGHTS[0]]

    async def test_le_vocabulaire_des_titres_passe_par_la_branche_d_intitule_seule(self) -> None:
        wanted = uuid4()
        transport = FakeTransport(JsonResponse(200, {"result": {"points": [{"id": str(wanted)}]}}))
        index = QdrantOfferIndex(transport, "offers", 2)

        found = await index.title_candidates("chef de rang", limit=40)

        assert list(found) == [wanted]
        _, path, body = transport.calls[0]
        assert path.endswith("/points/query")
        assert body["using"] == "title"
        assert body["limit"] == 40
        assert body["filter"] == {"must": [{"key": "active", "match": {"value": True}}]}
        assert body["query"]["text"] == "chef de rang"
        assert "prefetch" not in body

    async def test_un_point_sans_score_vaut_zero(self) -> None:
        found = uuid4()
        index, _ = offer_index(JsonResponse(200, {"result": {"points": [{"id": str(found)}]}}))

        candidates = await index.search(
            IndexQuery(dense=(1.0,), title_terms="", body_terms="", criteria=criteria(), limit=5)
        )

        assert candidates[0].retrieval_score == 0.0


class TestPayload:
    def test_une_offre_complete_expose_ses_attributs_filtrables(self) -> None:
        offer = make_offer()

        assert offer_payload(offer) == {
            "active": True,
            "published_at": int(NOW.timestamp()),
            "contract": "cdi",
            "remote": "sur_site",
            "schedule": "temps_plein",
            "experience_rank": ExperienceLevel.JUNIOR.rank,
            "salary_ceiling": 2_100,
            "department": "75",
            "city": "Paris",
            "location": PARIS,
        }

    def test_une_offre_muette_n_ecrit_pas_ses_absences(self) -> None:
        offer = make_offer(
            location=None,
            contract_type=None,
            remote=None,
            schedule=None,
            experience=None,
            salary=Salary(min_monthly=None, max_monthly=None, raw=None),
        )

        assert offer_payload(offer) == {"active": True, "published_at": int(NOW.timestamp())}

    def test_le_plafond_retombe_sur_le_minimum(self) -> None:
        offer = make_offer(salary=Salary(min_monthly=1_800, max_monthly=None, raw=None))

        assert offer_payload(offer)["salary_ceiling"] == 1_800

    def test_un_lieu_sans_coordonnees_n_a_pas_de_point(self) -> None:
        offer = make_offer(location=Location(label="Lyon", city="Lyon", department_code="69"))

        payload = offer_payload(offer)
        assert "location" not in payload
        assert payload["department"] == "69"

    def test_un_doublon_n_est_pas_actif(self) -> None:
        assert offer_payload(make_offer(duplicate_of=uuid4()))["active"] is False


def _or_empty(key: str, condition: dict[str, Any]) -> dict[str, Any]:
    return {"should": [condition, {"is_empty": {"key": key}}]}


class TestFiltre:
    def test_sans_contrainte_seules_les_actives(self) -> None:
        assert filter_for(criteria(whole_france=True)) == {
            "must": [{"key": "active", "match": {"value": True}}]
        }

    def test_le_temps_plein_laisse_passer_l_offre_muette(self) -> None:
        must = filter_for(criteria(whole_france=True, schedule=Schedule.FULL_TIME))["must"]

        assert must[1] == _or_empty(
            "schedule", {"key": "schedule", "match": {"value": "temps_plein"}}
        )

    def test_chaque_contrainte_laisse_passer_l_offre_muette_sauf_le_temps_partiel(self) -> None:
        wanted = criteria(
            whole_france=True,
            contract_types=[ContractType.CDI, ContractType.CDD],
            remote=[RemoteMode.HYBRID],
            schedule=Schedule.PART_TIME,
            max_experience=ExperienceLevel.JUNIOR,
            salary_min_monthly=2_000,
        )

        must = filter_for(wanted)["must"]

        assert must[1:] == [
            _or_empty("contract", {"key": "contract", "match": {"any": ["cdi", "cdd"]}}),
            _or_empty("remote", {"key": "remote", "match": {"any": ["hybride"]}}),
            {"key": "schedule", "match": {"value": "temps_partiel"}},
            _or_empty(
                "experience_rank",
                {"key": "experience_rank", "range": {"lte": ExperienceLevel.JUNIOR.rank}},
            ),
            _or_empty("salary_ceiling", {"key": "salary_ceiling", "range": {"gte": 2_000}}),
        ]

    def test_l_alternance_n_accepte_pas_l_offre_muette(self) -> None:
        must = filter_for(criteria(whole_france=True, contract_types=[ContractType.ALTERNANCE]))[
            "must"
        ]

        assert must[1:] == [{"key": "contract", "match": {"any": ["alternance"]}}]

    def test_le_teletravail_seul_est_strict(self) -> None:
        must = filter_for(criteria(remote=[RemoteMode.REMOTE]))["must"]

        assert must[1:] == [{"key": "remote", "match": {"value": "teletravail"}}]

    def test_une_ville_accepte_son_cercle_et_ses_departements(self) -> None:
        location = LocationCriteria(
            label="Paris",
            latitude=PARIS["lat"],
            longitude=PARIS["lon"],
            radius_km=20,
            department_codes=["92", "75"],
        )

        place = filter_for(criteria(location=location))["must"][1]

        assert place == {
            "should": [
                {"key": "department", "match": {"any": ["75", "92"]}},
                {"key": "location", "geo_radius": {"center": PARIS, "radius": 20_000}},
            ]
        }

    def test_qui_accepte_le_teletravail_le_prend_d_ou_qu_il_vienne(self) -> None:
        location = LocationCriteria(label="Paris", latitude=PARIS["lat"], longitude=PARIS["lon"])
        wanted = criteria(location=location, remote=[RemoteMode.HYBRID, RemoteMode.REMOTE])

        place = filter_for(wanted)["must"][2]

        assert place["should"][0] == {"key": "remote", "match": {"value": "teletravail"}}
        assert len(place["should"]) == 2

    def test_le_teletravail_accepte_sans_lieu_exploitable_ne_filtre_pas(self) -> None:
        wanted = criteria(
            location=LocationCriteria(label="quelque part"),
            remote=[RemoteMode.HYBRID, RemoteMode.REMOTE],
        )

        assert len(filter_for(wanted)["must"]) == 2

    def test_un_lieu_inexploitable_ne_filtre_pas(self) -> None:
        must = filter_for(criteria(location=LocationCriteria(label="quelque part")))["must"]

        assert len(must) == 1

    def test_sans_lieu_aucun_filtre_de_lieu(self) -> None:
        assert len(filter_for(criteria())["must"]) == 1


class TestCriteres:
    async def test_un_point_par_personne_avec_son_lieu(self) -> None:
        index, transport = criteria_index()
        user = uuid4()
        location = LocationCriteria(
            label="Paris", latitude=PARIS["lat"], longitude=PARIS["lon"], department_codes=["75"]
        )

        await index.upsert(user, (0.5, 0.5), criteria(location=location), alerts_enabled=True)

        point = transport.calls[0][2]["points"][0]
        assert point["id"] == str(user)
        assert point["payload"] == {
            "alerts_enabled": True,
            "remote_anywhere": False,
            "scope": "place",
            "departments": ["75"],
            "location": PARIS,
        }

    def test_la_france_entiere_et_le_teletravail_ont_leur_portee(self) -> None:
        assert criteria_payload(criteria(whole_france=True), alerts_enabled=False) == {
            "alerts_enabled": False,
            "remote_anywhere": False,
            "scope": "france",
        }
        assert criteria_payload(criteria(remote=[RemoteMode.REMOTE]), alerts_enabled=True) == {
            "alerts_enabled": True,
            "remote_anywhere": True,
            "scope": "remote",
        }

    def test_un_lieu_sans_point_garde_ses_departements(self) -> None:
        payload = criteria_payload(
            criteria(location=LocationCriteria(label="IDF", department_codes=["75"])),
            alerts_enabled=True,
        )

        assert payload == {
            "alerts_enabled": True,
            "remote_anywhere": False,
            "scope": "place",
            "departments": ["75"],
        }

    def test_sans_lieu_la_portee_reste_locale(self) -> None:
        assert criteria_payload(criteria(), alerts_enabled=True) == {
            "alerts_enabled": True,
            "remote_anywhere": False,
            "scope": "place",
        }

    async def test_retrait_et_bascule_des_alertes(self) -> None:
        index, transport = criteria_index()
        user = uuid4()

        await index.delete(user)
        await index.set_alerts_enabled(user, enabled=False)

        assert transport.calls[0][2] == {"points": [str(user)]}
        assert transport.calls[1][2] == {
            "payload": {"alerts_enabled": False},
            "points": [str(user)],
        }

    async def test_les_semblables_sont_filtres_par_le_lieu_de_l_offre(self) -> None:
        user = uuid4()
        index, transport = criteria_index(
            JsonResponse(200, {"result": {"points": [{"id": str(user), "score": 0.8}]}})
        )
        offer = make_offer()

        matches = await index.similar((1.0, 0.0), offer, limit=50, min_similarity=0.7)

        assert [(m.user_id, m.similarity) for m in matches] == [(user, 0.8)]
        body = transport.calls[0][2]
        assert body["filter"]["must"] == [
            {"key": "alerts_enabled", "match": {"value": True}},
            criteria_place_filter(offer),
        ]
        assert (body["limit"], body["score_threshold"]) == (50, 0.7)

    async def test_une_offre_en_teletravail_vise_aussi_qui_l_accepte(self) -> None:
        index, transport = criteria_index(JsonResponse(200, {"result": {}}))

        matches = await index.similar(
            (1.0,), make_offer(remote=RemoteMode.REMOTE), limit=5, min_similarity=0.5
        )

        assert matches == []
        place = transport.calls[0][2]["filter"]["must"][1]
        assert place["should"][:3] == [
            {"key": "scope", "match": {"value": "france"}},
            {"key": "scope", "match": {"value": "remote"}},
            {"key": "remote_anywhere", "match": {"value": True}},
        ]


class TestFiltreDeLieuInverse:
    def test_une_offre_situee_vise_la_france_son_departement_et_son_cercle(self) -> None:
        assert criteria_place_filter(make_offer()) == {
            "should": [
                {"key": "scope", "match": {"value": "france"}},
                {"key": "departments", "match": {"value": "75"}},
                {
                    "key": "location",
                    "geo_radius": {"center": PARIS, "radius": MAX_RADIUS_KM * 1_000},
                },
            ]
        }

    def test_une_offre_sans_lieu_ne_vise_que_la_france(self) -> None:
        assert criteria_place_filter(make_offer(location=None)) == {
            "should": [{"key": "scope", "match": {"value": "france"}}]
        }
