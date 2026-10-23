"""France Travail : jeton, pagination, débit, partition des fenêtres, mise au format — sans réseau."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import Message
from typing import Any

import pytest

from karria.collector.france_travail import (
    DEPARTMENTS,
    MAX_RESULTS,
    Credentials,
    FranceTravailClient,
    FranceTravailError,
    RateLimiter,
    apply_urls,
    collect_window,
    contract_of,
    experience_of,
    expired_record,
    location_label,
    offer_to_record,
    window_params,
)

pytestmark = pytest.mark.unit

CREDENTIALS = Credentials(
    client_id="id",
    client_secret="secret",
    scope="api_offresdemploiv2 o2dsoffre",
    token_url="https://auth.test/token",
    search_url="https://api.test/offres/search",
)
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class Response:
    """Ce que `urlopen` rend : statut, en-têtes, corps."""

    def __init__(
        self, status: int, body: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self._body = json.dumps(body).encode() if body is not None else b""

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def http_error(status: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://api.test", status, "err", message, io.BytesIO(b"{}"))


class FakeHttp:
    """Un `urlopen` scripté : une file de réponses par URL (préfixe)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.scripts: dict[str, list[Any]] = {}

    def on(self, prefix: str, *responses: Any) -> None:
        self.scripts.setdefault(prefix, []).extend(responses)

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any:
        self.calls.append((request.full_url, dict(request.headers)))
        for prefix, queue in self.scripts.items():
            if request.full_url.startswith(prefix) and queue:
                outcome = queue.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise AssertionError(f"appel imprévu : {request.full_url}")


def token_response(expires_in: int = 1_499) -> Response:
    return Response(200, {"access_token": "tok", "expires_in": expires_in})


def page(offers: list[dict[str, Any]], start: int, total: int) -> Response:
    end = start + len(offers) - 1
    return Response(206, {"resultats": offers}, {"Content-Range": f"offres {start}-{end}/{total}"})


def client(http: FakeHttp, **options: Any) -> tuple[FranceTravailClient, list[float]]:
    sleeps: list[float] = []
    clock = {"now": 1_000.0}

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    made = FranceTravailClient(
        CREDENTIALS,
        opener=http,
        clock=lambda: clock["now"],
        sleep=sleep,
        limiter=RateLimiter(rate=1_000, clock=lambda: clock["now"], sleep=sleep),
        **options,
    )
    return made, sleeps


def params_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


class TestJeton:
    def test_le_jeton_est_obtenu_une_fois_et_envoye_en_bearer(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(CREDENTIALS.search_url, page([], 0, 0), page([], 0, 0))
        made, _ = client(http)

        made.search({"range": "0-0"})
        made.search({"range": "0-0"})

        token_calls = [c for c in http.calls if c[0] == CREDENTIALS.token_url]
        assert len(token_calls) == 1
        assert http.calls[1][1]["Authorization"] == "Bearer tok"

    def test_le_jeton_est_renouvele_avant_son_expiration(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response(expires_in=100), token_response())
        http.on(CREDENTIALS.search_url, page([], 0, 0), page([], 0, 0))
        made, _ = client(http)

        made.search({"range": "0-0"})
        made._token_expires_at = 0  # l'horloge a dépassé la marge
        made.search({"range": "0-0"})

        assert len([c for c in http.calls if c[0] == CREDENTIALS.token_url]) == 2

    def test_une_authentification_refusee_leve(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, Response(400, {"error": "invalid_client"}))
        made, _ = client(http)

        with pytest.raises(FranceTravailError) as caught:
            made.token()
        assert caught.value.status == 400

    def test_un_401_renouvelle_le_jeton_et_rejoue(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response(), token_response())
        http.on(CREDENTIALS.search_url, http_error(401), page([{"id": "A"}], 0, 1))
        made, _ = client(http)

        found = made.search({"range": "0-0"})

        assert [o["id"] for o in found.offers] == ["A"]
        assert len([c for c in http.calls if c[0] == CREDENTIALS.token_url]) == 2


class TestRecherche:
    def test_le_total_vient_de_content_range_et_204_vaut_vide(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(CREDENTIALS.search_url, page([{"id": "A"}], 0, 5_045), Response(204))
        made, _ = client(http)

        assert made.count({"motsCles": "cuisinier"}) == 5_045
        assert made.search({"range": "0-0"}).total == 0
        assert params_of(http.calls[1][0]) == {"motsCles": "cuisinier", "range": "0-0"}

    def test_sans_content_range_le_total_est_la_page(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(CREDENTIALS.search_url, Response(200, {"resultats": [{"id": "A"}, "bruit"]}))
        made, _ = client(http)

        found = made.search({"range": "0-0"})

        assert (len(found.offers), found.total) == (1, 1)

    def test_fetch_all_pagine_par_150_et_s_arrete_au_total(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        first = [{"id": str(i)} for i in range(150)]
        second = [{"id": str(i)} for i in range(150, 200)]
        http.on(CREDENTIALS.search_url, page(first, 0, 200), page(second, 150, 200))
        made, _ = client(http)

        found = made.fetch_all({"motsCles": "x"})

        assert len(found) == 200
        ranges = [params_of(c[0])["range"] for c in http.calls[1:]]
        assert ranges == ["0-149", "150-299"]

    def test_fetch_all_ne_depasse_jamais_l_index_3149(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        pages = [
            page([{"id": str(s + i)} for i in range(150)], s, 10_000) for s in range(0, 3_150, 150)
        ]
        http.on(CREDENTIALS.search_url, *pages)
        made, _ = client(http)

        found = made.fetch_all({"motsCles": "x"})

        assert len(found) == MAX_RESULTS
        assert params_of(http.calls[-1][0])["range"] == "3000-3149"

    def test_une_page_vide_arrete_la_pagination(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(
            CREDENTIALS.search_url,
            Response(206, {"resultats": []}, {"Content-Range": "offres 0-0/900"}),
        )
        made, _ = client(http)

        assert made.fetch_all({}) == []

    def test_un_429_attend_retry_after_et_rejoue(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(
            CREDENTIALS.search_url, http_error(429, {"Retry-After": "2"}), page([{"id": "A"}], 0, 1)
        )
        made, sleeps = client(http)

        assert made.search({"range": "0-0"}).total == 1
        assert 2.0 in sleeps

    def test_un_5xx_se_reprend_avec_recul_puis_abandonne(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(CREDENTIALS.search_url, *[http_error(503) for _ in range(5)])
        made, sleeps = client(http)

        with pytest.raises(FranceTravailError) as caught:
            made.search({"range": "0-0"})

        assert caught.value.status == 503
        assert sleeps[:4] == [1.0, 2.0, 4.0, 8.0]

    def test_un_400_est_une_erreur_a_nous_sans_reprise(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(CREDENTIALS.search_url, http_error(400))
        made, _ = client(http)

        with pytest.raises(FranceTravailError):
            made.search({"range": "3000-3150"})
        assert len([c for c in http.calls if c[0].startswith(CREDENTIALS.search_url)]) == 1

    def test_une_panne_reseau_est_une_erreur_sans_statut(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(CREDENTIALS.search_url, *[urllib.error.URLError("coupé") for _ in range(5)])
        made, _ = client(http)

        with pytest.raises(FranceTravailError) as caught:
            made.search({"range": "0-0"})
        assert caught.value.status is None

    def test_l_existence_d_une_annonce_lit_204_comme_retiree(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on("https://api.test/offres/215NPDX", Response(200, {"id": "215NPDX"}))
        http.on("https://api.test/offres/000XXXX", Response(204))
        made, _ = client(http)

        assert made.exists("215NPDX") is True
        assert made.exists("000XXXX") is False


class TestDebit:
    def test_le_seau_espace_les_appels(self) -> None:
        clock = {"now": 0.0}
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["now"] += seconds

        limiter = RateLimiter(rate=4, clock=lambda: clock["now"], sleep=sleep)
        for _ in range(3):
            limiter.wait()

        assert sleeps == [0.25, 0.25]

    def test_un_appel_tardif_n_attend_pas(self) -> None:
        clock = {"now": 0.0}
        sleeps: list[float] = []
        limiter = RateLimiter(rate=4, clock=lambda: clock["now"], sleep=sleeps.append)
        limiter.wait()
        clock["now"] = 10.0
        limiter.wait()

        assert sleeps == []


class StubClient:
    """Un client dont on scripte les totaux : (département, largeur de fenêtre) → total."""

    def __init__(self, totals: Callable[[str | None, timedelta], int]) -> None:
        self._totals = totals
        self.counted: list[tuple[str | None, str, str]] = []
        self.fetched: list[dict[str, str]] = []
        self.requests = 0

    def count(self, params: dict[str, str]) -> int:
        start = datetime.fromisoformat(params["minCreationDate"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(params["maxCreationDate"].replace("Z", "+00:00"))
        self.counted.append(
            (params.get("departement"), params["minCreationDate"], params["maxCreationDate"])
        )
        return self._totals(params.get("departement"), end - start)

    def fetch_all(self, params: dict[str, str]) -> list[dict[str, Any]]:
        self.fetched.append(dict(params))
        return [{"id": f"{params.get('departement', 'FR')}-{len(self.fetched)}"}]


class TestFenetres:
    def test_les_parametres_sont_a_l_heure_de_paris_tries_par_date(self) -> None:
        # 12 h UTC le 22 octobre = 14 h à Paris : l'API lit ses bornes dans ce fuseau.
        assert window_params(NOW - timedelta(hours=1), NOW) == {
            "minCreationDate": "2026-10-22T13:00:00Z",
            "maxCreationDate": "2026-10-22T14:00:00Z",
            "sort": "1",
        }

    def test_une_fenetre_sous_le_plafond_se_lit_d_un_trait(self) -> None:
        stub = StubClient(lambda dept, width: 60)

        found = list(collect_window(stub, NOW - timedelta(minutes=3), NOW))  # type: ignore[arg-type]

        assert len(found) == 1
        assert len(stub.fetched) == 1
        assert "departement" not in stub.fetched[0]

    def test_une_fenetre_vide_ne_lit_rien(self) -> None:
        stub = StubClient(lambda dept, width: 0)

        assert list(collect_window(stub, NOW - timedelta(minutes=3), NOW)) == []  # type: ignore[arg-type]
        assert stub.fetched == []

    def test_une_fenetre_trop_large_se_partitionne_par_departement(self) -> None:
        stub = StubClient(lambda dept, width: 24_518 if dept is None else 200)

        found = list(collect_window(stub, NOW - timedelta(days=1), NOW))  # type: ignore[arg-type]

        assert len(found) == len(DEPARTMENTS)
        assert [p["departement"] for p in stub.fetched] == list(DEPARTMENTS)

    def test_un_departement_trop_large_se_coupe_en_deux_dans_le_temps(self) -> None:
        def totals(dept: str | None, width: timedelta) -> int:
            if dept is None:
                return 50_000
            if dept != "75":
                return 10
            return 5_000 if width > timedelta(hours=12) else 1_000

        stub = StubClient(totals)

        list(collect_window(stub, NOW - timedelta(days=1), NOW))  # type: ignore[arg-type]

        paris = [p for p in stub.fetched if p["departement"] == "75"]
        assert len(paris) == 2
        assert paris[0]["maxCreationDate"] == paris[1]["minCreationDate"] == "2026-10-22T02:00:00Z"

    def test_sous_cinq_minutes_on_prend_ce_que_l_api_donne(self) -> None:
        stub = StubClient(lambda dept, width: 50_000)

        found = list(collect_window(stub, NOW - timedelta(minutes=4), NOW))  # type: ignore[arg-type]

        assert len(found) == len(DEPARTMENTS)
        assert all("departement" in p for p in stub.fetched)


OFFER: dict[str, Any] = {
    "id": "215NPDX",
    "intitule": "Cuisinier / Cuisinière",
    "description": "Vous préparez les plats.",
    "dateCreation": "2026-10-22T12:28:37.803Z",
    "dateActualisation": "2026-10-22T12:30:00.000Z",
    "lieuTravail": {
        "libelle": "86 - FLEURE",
        "latitude": 46.478499,
        "longitude": 0.520047,
        "codePostal": "86340",
        "commune": "86099",
    },
    "romeCode": "G1609",
    "romeLibelle": "Cuisinier / Cuisinière",
    "entreprise": {"nom": "Traiteur du Poitou", "logo": "https://img.test/logo.png"},
    "typeContrat": "CDI",
    "typeContratLibelle": "CDI",
    "natureContrat": "Contrat travail",
    "experienceExige": "E",
    "experienceLibelle": "1 An(s)",
    "salaire": {"libelle": "Mensuel de 2300.0 Euros - Selon expérience"},
    "dureeTravailLibelle": "35H/semaine\nTravail en journée",
    "dureeTravailLibelleConverti": "Temps plein",
    "alternance": False,
    "origineOffre": {
        "origine": "1",
        "urlOrigine": "https://candidat.francetravail.fr/offres/recherche/detail/215NPDX",
    },
    "contact": {"urlPostulation": "https://postuler.test/215NPDX"},
}


class TestMiseAuFormat:
    def test_une_annonce_complete(self) -> None:
        record = offer_to_record(OFFER)

        assert record is not None
        assert (record["source"], record["source_id"], record["title"]) == (
            "france_travail",
            "215NPDX",
            "Cuisinier / Cuisinière",
        )
        assert record["company_name"] == "Traiteur du Poitou"
        assert record["location_label"] == "Fleure (86)"
        assert (record["latitude"], record["longitude"], record["postal_code"]) == (
            46.478499,
            0.520047,
            "86340",
        )
        # « 12:28:37.803Z » est l'heure de Paris : 10 h 28 UTC.
        assert record["posted_at"] == "2026-10-22T10:28:37+00:00"
        assert record["updated_at"] == "2026-10-22T10:30:00+00:00"
        assert record["date_posted"] == "2026-10-22"
        assert record["contract_type"] == "cdi"
        assert record["schedule"] == "temps_plein"
        assert (record["experience"], record["experience_years"]) == (None, 1.0)
        assert record["salary_text"] == "Mensuel de 2300.0 Euros - Selon expérience"
        assert record["job_url"] == OFFER["origineOffre"]["urlOrigine"]
        assert record["job_url_direct"] == "https://postuler.test/215NPDX"
        assert record["expired"] is False
        assert record["raw"]["romeCode"] == "G1609"
        assert record["raw"]["commune"] == "86099"

    def test_sans_identifiant_ou_sans_titre_rien(self) -> None:
        assert offer_to_record({**OFFER, "id": " "}) is None
        assert offer_to_record({**OFFER, "intitule": None}) is None

    def test_les_champs_absents_restent_absents(self) -> None:
        record = offer_to_record({"id": "X", "intitule": "Serveur", "lieuTravail": "?"})

        assert record is not None
        assert record["location_label"] is None
        assert record["latitude"] is None
        assert record["contract_type"] is None
        assert record["schedule"] is None
        assert record["posted_at"] is None
        assert record["job_url"] is None
        assert record["description"] == ""

    @pytest.mark.parametrize(
        ("lieu", "expected"),
        [
            ({"libelle": "86 - FLEURE"}, "Fleure (86)"),
            ({"libelle": "75 - PARIS 08"}, "Paris 08 (75)"),
            ({"libelle": "75 - Paris (Dept.)"}, "Paris (75)"),
            ({"libelle": "2A - Ajaccio"}, "Ajaccio (2A)"),
            ({"libelle": "974 - Saint-Denis"}, "Saint-Denis (974)"),
            ({"libelle": "FRANCE"}, "FRANCE"),
            ({"libelle": "24 - Razac-sur-l'Isle"}, "Razac-sur-l'Isle (24)"),
            ({"libelle": "  "}, None),
            ({}, None),
        ],
    )
    def test_le_libelle_de_lieu_devient_lisible(
        self, lieu: dict[str, Any], expected: str | None
    ) -> None:
        assert location_label(lieu) == expected

    @pytest.mark.parametrize(
        ("offer", "expected"),
        [
            ({"typeContrat": "CDI"}, "cdi"),
            ({"typeContrat": "DIN"}, "cdi"),
            ({"typeContrat": "CDD"}, "cdd"),
            ({"typeContrat": "MIS"}, "interim"),
            ({"typeContrat": "SAI"}, "saisonnier"),
            ({"typeContrat": "LIB"}, "freelance"),
            ({"typeContrat": "CCE"}, None),
            ({"typeContrat": "CDD", "alternance": True}, "alternance"),
            ({"typeContrat": "CDD", "natureContrat": "Contrat apprentissage"}, "alternance"),
            ({"typeContrat": "CDI", "natureContrat": "Cont. professionnalisation"}, "alternance"),
            ({}, None),
        ],
    )
    def test_le_contrat(self, offer: dict[str, Any], expected: str | None) -> None:
        assert contract_of(offer) == expected

    @pytest.mark.parametrize(
        ("offer", "expected"),
        [
            ({"experienceExige": "D", "experienceLibelle": "Débutant accepté"}, ("debutant", None)),
            ({"experienceExige": "E", "experienceLibelle": "2 An(s)"}, (None, 2.0)),
            ({"experienceExige": "S", "experienceLibelle": "6 Mois"}, (None, 0.5)),
            ({"experienceExige": "E", "experienceLibelle": "3 An(s) - en cuisine"}, (None, 3.0)),
            ({"experienceLibelle": "Expérience exigée"}, (None, None)),
            ({}, (None, None)),
        ],
    )
    def test_l_experience(
        self, offer: dict[str, Any], expected: tuple[str | None, float | None]
    ) -> None:
        assert experience_of(offer) == expected

    def test_une_offre_partenaire_sans_lien_de_candidature_pointe_vers_le_partenaire(self) -> None:
        offer = {
            "origineOffre": {
                "origine": "2",
                "urlOrigine": "https://candidat.francetravail.fr/offres/recherche/detail/1",
                "partenaires": [{"nom": "HELLOWORK", "url": "https://www.hellowork.com/x.html"}],
            }
        }

        assert apply_urls(offer) == (
            "https://candidat.francetravail.fr/offres/recherche/detail/1",
            "https://www.hellowork.com/x.html",
        )
        assert apply_urls({}) == (None, None)

    def test_la_ligne_de_retrait(self) -> None:
        assert expired_record("215NPDX") == {
            "source": "france_travail",
            "source_id": "215NPDX",
            "expired": True,
        }


class TestRobustesse:
    def test_un_retry_after_illisible_vaut_le_recul_par_defaut(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(
            CREDENTIALS.search_url,
            http_error(429, {"Retry-After": "bientôt"}),
            page([{"id": "A"}], 0, 1),
        )
        made, sleeps = client(http)

        made.search({"range": "0-0"})

        assert sleeps == [1.0]

    def test_un_instant_illisible_ou_naif_de_la_source(self) -> None:
        record = offer_to_record(
            {
                "id": "X",
                "intitule": "Serveur",
                "dateCreation": "hier",
                "dateActualisation": "2026-10-22T10:00:00",
            }
        )

        assert record is not None
        assert record["posted_at"] is None
        assert record["updated_at"] == "2026-10-22T08:00:00+00:00"

    def test_un_libelle_de_departement_sans_nom_reste_tel_quel(self) -> None:
        assert location_label({"libelle": "75 - (Dept.)"}) == "75 - (Dept.)"

    def test_un_total_illisible_vaut_absent(self) -> None:
        http = FakeHttp()
        http.on(CREDENTIALS.token_url, token_response())
        http.on(
            CREDENTIALS.search_url,
            Response(206, {"resultats": [{"id": "A"}]}, {"Content-Range": "?"}),
        )
        made, _ = client(http)

        assert made.search({"range": "0-0"}).total == 1
