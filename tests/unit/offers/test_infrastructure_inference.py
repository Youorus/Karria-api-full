"""Les serveurs TEI : lots, préfixes E5, ordre des scores, traduction des pannes."""

from __future__ import annotations

from typing import Any

import pytest

from karria.modules.offers.domain import SearchUnavailableError
from karria.modules.offers.infrastructure.inference import (
    TeiEmbedder,
    needs_prefixes,
)
from karria.shared.config import InferenceSettings
from karria.shared.http.outbound import JsonResponse, OutboundError

pytestmark = pytest.mark.unit


class EchoPoster:
    """Rend un vecteur par texte reçu (sa longueur), ou les réponses préparées."""

    def __init__(self, *responses: JsonResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, Any]] = []

    async def post_json(self, path: str, payload: Any) -> JsonResponse:
        self.calls.append((path, payload))
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return JsonResponse(200, [[float(len(text))] for text in payload["inputs"]])


class TestPrefixes:
    def test_les_modeles_e5_attendent_leurs_prefixes(self) -> None:
        assert needs_prefixes("intfloat/multilingual-e5-base@int8")
        assert not needs_prefixes("BAAI/bge-m3")


class TestPlongements:
    async def test_les_documents_partent_par_paquets_et_reviennent_dans_l_ordre(self) -> None:
        http = EchoPoster()
        embedder = TeiEmbedder(
            http, model="intfloat/multilingual-e5-base", batch_size=2, concurrency=2
        )

        vectors = await embedder.embed_documents(["a", "bb", "ccc"])

        assert vectors == [(10.0,), (11.0,), (12.0,)]  # « passage: » + texte
        assert [len(payload["inputs"]) for _, payload in http.calls] == [2, 1]
        assert http.calls[0][1]["inputs"][0] == "passage: a"
        assert http.calls[0][1]["normalize"] is True

    async def test_la_requete_porte_son_prefixe(self) -> None:
        http = EchoPoster()
        embedder = TeiEmbedder(http, model="intfloat/multilingual-e5-base", batch_size=8)

        assert await embedder.embed_query("serveur") == (len("query: serveur"),)
        assert embedder.version == "intfloat/multilingual-e5-base"

    async def test_un_modele_sans_prefixe_envoie_le_texte_brut(self) -> None:
        http = EchoPoster()
        embedder = TeiEmbedder(http, model="BAAI/bge-m3", batch_size=0, concurrency=0)

        await embedder.embed_documents(["abc"])

        assert http.calls[0][1]["inputs"] == ["abc"]

    async def test_une_panne_devient_une_indisponibilite(self) -> None:
        embedder = TeiEmbedder(
            EchoPoster(OutboundError("boom", status=503)), model="m", batch_size=4
        )

        with pytest.raises(SearchUnavailableError):
            await embedder.embed_query("x")

    async def test_une_reponse_mal_formee_est_une_panne(self) -> None:
        embedder = TeiEmbedder(EchoPoster(JsonResponse(200, {"oops": 1})), model="m", batch_size=4)

        with pytest.raises(SearchUnavailableError):
            await embedder.embed_query("x")

    async def test_un_nombre_de_vecteurs_inattendu_est_une_panne(self) -> None:
        embedder = TeiEmbedder(
            EchoPoster(JsonResponse(200, [[1.0], [2.0]])), model="m", batch_size=4
        )

        with pytest.raises(SearchUnavailableError):
            await embedder.embed_query("x")

    def test_la_configuration_regle_le_client(self) -> None:
        settings = InferenceSettings(embeddings_url="http://tei:80", embedding_concurrency=3)

        embedder = TeiEmbedder.from_settings(settings)

        assert embedder.version == settings.embedding_model


class TestConfigurationDesServeurs:
    def test_le_serveur_est_renseigne_par_defaut(self) -> None:
        assert InferenceSettings().enabled is True

    def test_un_serveur_absent_desactive(self) -> None:
        assert InferenceSettings(embeddings_url="").enabled is False
