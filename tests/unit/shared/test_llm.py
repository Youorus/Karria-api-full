"""Le port LLM du socle : vocabulaire, schémas, trois adaptateurs, routeur, assemblage — sans réseau."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from karria.shared.config import (
    AppSettings,
    Environment,
    GeminiSettings,
    LlmProviderSettings,
    LlmProvidersSettings,
    LlmSettings,
    LlmTaskRoutes,
    MailProvider,
    MailSettings,
    SecuritySettings,
    Settings,
    StorageSettings,
)
from karria.shared.config.providers import Provider as StorageProvider
from karria.shared.config.settings import LLM_PROVIDERS
from karria.shared.http.outbound import JsonResponse, OutboundError
from karria.shared.llm import (
    AnthropicAdapter,
    Blob,
    DisabledLanguageModel,
    GeminiAdapter,
    LlmCapabilityError,
    LlmConfigurationError,
    LlmOutputInvalidError,
    LlmRequest,
    LlmResult,
    LlmRouter,
    LlmUnavailableError,
    ModelRoute,
    OpenAIAdapter,
    Provider,
    RoutingTable,
    build_language_model,
    response_schema_for,
    strict_schema,
    structured,
)
from karria.shared.llm import anthropic as anthropic_adapter
from karria.shared.llm import gemini as gemini_adapter
from karria.shared.llm import openai as openai_adapter

pytestmark = pytest.mark.unit


class FakePoster:
    """Ce qu'un adaptateur attend du client HTTP : rend ou lève, dans l'ordre, garde les appels."""

    def __init__(self, *responses: JsonResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, Any]] = []

    async def post_json(self, path: str, payload: Any) -> JsonResponse:
        self.calls.append((path, payload))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Shape(BaseModel):
    id: str = "x"
    name: str
    tags: list[str] = []


def request(**changes: Any) -> LlmRequest:
    base: dict[str, Any] = {"task": "cv_interpret", "parts": ("Bonjour",), "system": "Règle"}
    base.update(changes)
    return LlmRequest(**base)


def result(text: str | None = "{}", **changes: Any) -> LlmResult:
    base: dict[str, Any] = {
        "text": text,
        "image": None,
        "finish_reason": "STOP",
        "refused": False,
        "provider": Provider.GEMINI,
        "model": "m",
    }
    base.update(changes)
    return LlmResult(**base)


class FakeAdapter:
    def __init__(self, provider: Provider, *outcomes: LlmResult | Exception) -> None:
        self.provider = provider
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, LlmRequest]] = []

    async def generate(self, model: str, request: LlmRequest) -> LlmResult:
        self.calls.append((model, request))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestVocabulaire:
    def test_une_route_se_lit_et_se_dit(self) -> None:
        route = ModelRoute.parse(" Gemini : gemini-3.8-flash ")
        assert (route.provider, route.model) == (Provider.GEMINI, "gemini-3.8-flash")
        assert str(route) == "gemini:gemini-3.8-flash"

    @pytest.mark.parametrize("value", ["gemini", "gemini:", ":x", "mistral:large", ""])
    def test_une_route_mal_formee_est_une_erreur_de_configuration(self, value: str) -> None:
        with pytest.raises(LlmConfigurationError):
            ModelRoute.parse(value)

    def test_un_blob_connait_sa_famille(self) -> None:
        assert Blob(b"x", "audio/mp4").kind == "audio"
        assert request(parts=("a", Blob(b"x", "image/png"))).blobs == (Blob(b"x", "image/png"),)

    def test_le_json_du_resultat_est_verifie(self) -> None:
        assert result('{"a": 1}').json() == {"a": 1}
        with pytest.raises(LlmOutputInvalidError):
            result(None).json()
        with pytest.raises(LlmOutputInvalidError):
            result("pas du json").json()

    async def test_structured_revalide_avec_pydantic(self) -> None:
        class Llm:
            async def generate(self, req: LlmRequest) -> LlmResult:
                return result(json.dumps({"name": "Awa", "tags": ["a"]}))

        shape = await structured(Llm(), request(), Shape)
        assert shape is not None
        assert (shape.name, shape.tags) == ("Awa", ["a"])

    async def test_structured_rend_none_sur_refus_et_leve_hors_schema(self) -> None:
        class Refusing:
            async def generate(self, req: LlmRequest) -> LlmResult:
                return result(None, refused=True, finish_reason="SAFETY")

        class Wrong:
            async def generate(self, req: LlmRequest) -> LlmResult:
                return result('{"name": 3}')

        assert await structured(Refusing(), request(), Shape) is None
        with pytest.raises(LlmOutputInvalidError):
            await structured(Wrong(), request(), Shape)


class TestSchema:
    def test_le_schema_est_autonome_sobre_et_sans_identifiant(self) -> None:
        schema = response_schema_for(Shape)
        assert "$defs" not in json.dumps(schema)
        assert "title" not in schema
        assert "id" not in schema["properties"]
        assert "id" not in schema.get("required", [])
        assert schema["properties"]["tags"]["type"] == "array"

    def test_la_variante_stricte_ferme_les_objets_et_rend_tout_obligatoire(self) -> None:
        strict = strict_schema(response_schema_for(Shape))
        assert strict["additionalProperties"] is False
        assert sorted(strict["required"]) == ["name", "tags"]


class TestGemini:
    async def test_le_corps_porte_consigne_historique_donnees_et_schema(self) -> None:
        poster = FakePoster(
            JsonResponse(
                200,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "pensée", "thought": True},
                                    {"text": "{"},
                                    {"text": "}"},
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3},
                },
            )
        )
        out = await GeminiAdapter(poster).generate(
            "flash",
            request(
                parts=("Lis", Blob(b"\x00", "audio/mp4")),
                history=(("user", "Salut"), ("assistant", "Bonjour")),
                response_schema={"type": "object"},
                max_output_tokens=200,
            ),
        )
        path, body = poster.calls[0]
        assert path == "models/flash:generateContent"
        assert body["system_instruction"] == {"parts": [{"text": "Règle"}]}
        assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
        assert body["contents"][-1]["parts"][1]["inline_data"]["mime_type"] == "audio/mp4"
        assert body["generation_config"]["response_json_schema"] == {"type": "object"}
        assert body["generation_config"]["thinking_config"] == {"thinking_budget": 0}
        assert body["generation_config"]["max_output_tokens"] == 200
        assert (out.text, out.input_tokens, out.output_tokens, out.refused) == ("{}", 12, 3, False)

    async def test_une_image_demandee_declare_la_modalite_et_la_reflexion_reste(self) -> None:
        poster = FakePoster(
            JsonResponse(
                200,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "image/jpeg",
                                            "data": base64.b64encode(b"img").decode(),
                                        }
                                    }
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ]
                },
            )
        )
        out = await GeminiAdapter(poster).generate(
            "img", request(image_output=True, aspect_ratio="3:4", thinking=True)
        )
        config = poster.calls[0][1]["generation_config"]
        assert config["response_modalities"] == ["IMAGE"]
        assert config["image_config"] == {"aspect_ratio": "3:4"}
        assert "thinking_config" not in config
        assert out.image == Blob(b"img", "image/jpeg")

    @pytest.mark.parametrize(
        "body",
        [
            {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []},
            {"candidates": []},
            {"candidates": [{"content": {"parts": []}, "finishReason": "PROHIBITED_CONTENT"}]},
        ],
    )
    async def test_un_blocage_ou_un_motif_de_securite_est_un_refus(
        self, body: dict[str, Any]
    ) -> None:
        out = await GeminiAdapter(FakePoster(JsonResponse(200, body))).generate("flash", request())
        assert out.refused is True
        assert out.text is None

    async def test_une_panne_sortante_est_une_indisponibilite(self) -> None:
        for error in (
            OutboundError("503", status=503),
            OutboundError("400", status=400),
            JsonResponse(200, []),
        ):
            with pytest.raises(LlmUnavailableError):
                await GeminiAdapter(FakePoster(error)).generate("flash", request())

    def test_l_assemblage_porte_la_cle_en_en_tete(self) -> None:
        adapter = GeminiAdapter.from_credentials("k", timeout_seconds=5)
        assert adapter._http.headers == {"x-goog-api-key": "k"}  # type: ignore[attr-defined]
        assert gemini_adapter.is_our_fault(400)
        assert not gemini_adapter.is_our_fault(429)


class TestAnthropic:
    async def test_le_corps_messages_porte_system_blocs_et_schema_strict(self) -> None:
        poster = FakePoster(
            JsonResponse(
                200,
                {
                    "model": "claude-x",
                    "content": [{"type": "text", "text": '{"name": "Awa"}'}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                },
            )
        )
        out = await AnthropicAdapter(poster).generate(
            "claude-x",
            request(
                parts=("Lis", Blob(b"img", "image/png"), Blob(b"%PDF", "application/pdf")),
                history=(("user", "a"), ("assistant", "b")),
                response_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            ),
        )
        path, body = poster.calls[0]
        assert path == "/v1/messages"
        assert body["system"] == "Règle"
        assert body["max_tokens"] == anthropic_adapter.DEFAULT_MAX_TOKENS
        assert body["output_config"]["effort"] == "low"
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
        assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
        blocks = body["messages"][-1]["content"]
        assert [b["type"] for b in blocks] == ["text", "image", "document"]
        assert "temperature" not in body
        assert (out.text, out.model, out.input_tokens, out.output_tokens) == (
            '{"name": "Awa"}',
            "claude-x",
            10,
            4,
        )

    async def test_un_arret_refusal_est_un_refus(self) -> None:
        poster = FakePoster(JsonResponse(200, {"content": [], "stop_reason": "refusal"}))
        out = await AnthropicAdapter(poster).generate("claude-x", request())
        assert out.refused
        assert out.text is None

    async def test_l_audio_et_l_image_en_sortie_ne_sont_pas_servis(self) -> None:
        adapter = AnthropicAdapter(FakePoster())
        with pytest.raises(LlmCapabilityError):
            await adapter.generate("claude-x", request(parts=(Blob(b"x", "audio/mp4"),)))
        with pytest.raises(LlmCapabilityError):
            await adapter.generate("claude-x", request(image_output=True))

    async def test_une_panne_sortante_est_une_indisponibilite(self) -> None:
        with pytest.raises(LlmUnavailableError) as caught:
            await AnthropicAdapter(FakePoster(OutboundError("429", status=429))).generate(
                "c", request()
            )
        assert caught.value.status == 429

    def test_l_assemblage_porte_la_cle_et_la_version(self) -> None:
        adapter = AnthropicAdapter.from_credentials("k")
        assert adapter._http.headers == {
            "x-api-key": "k",
            "anthropic-version": anthropic_adapter.API_VERSION,
        }  # type: ignore[attr-defined]


class TestOpenAI:
    async def test_le_corps_chat_completions_porte_system_schema_et_contenus(self) -> None:
        poster = FakePoster(
            JsonResponse(
                200,
                {
                    "model": "gpt-x",
                    "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 2},
                },
            )
        )
        out = await OpenAIAdapter(poster).generate(
            "gpt-x",
            request(
                parts=(
                    "Lis",
                    Blob(b"img", "image/png"),
                    Blob(b"wav", "audio/wav"),
                    Blob(b"%PDF", "application/pdf"),
                ),
                response_schema={"type": "object", "properties": {}},
                max_output_tokens=50,
            ),
        )
        path, body = poster.calls[0]
        assert path == "/chat/completions"
        assert body["messages"][0] == {"role": "system", "content": "Règle"}
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["max_completion_tokens"] == 50
        assert body["temperature"] == 0.2
        parts = body["messages"][-1]["content"]
        assert [p["type"] for p in parts] == ["text", "image_url", "input_audio", "file"]
        assert parts[2]["input_audio"]["format"] == "wav"
        assert (out.text, out.provider, out.input_tokens) == ("{}", Provider.OPENAI, 7)

    async def test_le_mode_json_object_rappelle_le_schema_dans_la_consigne(self) -> None:
        poster = FakePoster(
            JsonResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {"content": [{"type": "text", "text": "{}"}]},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        )
        adapter = OpenAIAdapter(
            poster, provider=Provider.COMPATIBLE, structured_output="json_object"
        )
        out = await adapter.generate("local", request(response_schema={"type": "object"}))
        body = poster.calls[0][1]
        assert body["response_format"] == {"type": "json_object"}
        assert "schéma" in body["messages"][0]["content"]
        assert (out.text, out.provider) == ("{}", Provider.COMPATIBLE)

    async def test_un_refus_un_filtre_ou_aucun_choix_sont_des_refus(self) -> None:
        for body in (
            {"choices": [{"message": {"refusal": "non"}, "finish_reason": "stop"}]},
            {"choices": [{"message": {"content": "x"}, "finish_reason": "content_filter"}]},
            {"choices": []},
        ):
            out = await OpenAIAdapter(FakePoster(JsonResponse(200, body))).generate(
                "gpt", request()
            )
            assert out.refused

    async def test_un_audio_inconnu_et_une_image_en_sortie_ne_sont_pas_servis(self) -> None:
        adapter = OpenAIAdapter(FakePoster())
        with pytest.raises(LlmCapabilityError):
            await adapter.generate("gpt", request(parts=(Blob(b"x", "audio/mp4"),)))
        with pytest.raises(LlmCapabilityError):
            await adapter.generate("gpt", request(image_output=True))

    async def test_une_panne_sortante_est_une_indisponibilite(self) -> None:
        with pytest.raises(LlmUnavailableError):
            await OpenAIAdapter(FakePoster(OutboundError("coupé"))).generate("gpt", request())

    def test_l_assemblage_sans_cle_pour_un_modele_local(self) -> None:
        local = OpenAIAdapter.from_credentials(
            "", base_url="http://vllm:8000/v1", provider=Provider.COMPATIBLE
        )
        assert local._http.headers == {}  # type: ignore[attr-defined]
        assert OpenAIAdapter.from_credentials("k")._http.headers == {"Authorization": "Bearer k"}  # type: ignore[attr-defined]
        assert openai_adapter.AUDIO_FORMATS["audio/mpeg"] == "mp3"


class TestRouteur:
    def test_la_table_se_lit_ignore_le_vide_et_nomme_la_variable_fautive(self) -> None:
        table = RoutingTable.parse({"a": "gemini:g", "b": ""}, {"a": "openai:o"})
        assert table.routes == {"a": ModelRoute(Provider.GEMINI, "g")}
        assert table.fallbacks == {"a": ModelRoute(Provider.OPENAI, "o")}
        assert table.providers() == {Provider.GEMINI, Provider.OPENAI}
        with pytest.raises(LlmConfigurationError, match="LLM__TASKS__A"):
            RoutingTable.parse({"a": "mistral:x"})
        with pytest.raises(LlmConfigurationError, match="LLM__FALLBACKS__A"):
            RoutingTable.parse({}, {"a": "x"})

    def test_la_validation_dit_la_cle_qui_manque_et_la_tache_sans_route(self) -> None:
        table = RoutingTable.parse({"a": "anthropic:c"})
        problems = table.validate(
            {Provider.ANTHROPIC: False}, {"a": "LLM__TASKS__A", "b": "LLM__TASKS__B"}
        )
        assert any("LLM__TASKS__B" in p for p in problems)
        assert any("LLM__PROVIDERS__ANTHROPIC__API_KEY" in p for p in problems)
        assert table.validate({Provider.ANTHROPIC: True}, {"a": "LLM__TASKS__A"}) == []

    async def test_la_tache_choisit_l_adaptateur_et_le_modele(self) -> None:
        gemini = FakeAdapter(Provider.GEMINI, result("ok"))
        router = LlmRouter(
            RoutingTable.parse({"cv_interpret": "gemini:flash"}), {Provider.GEMINI: gemini}
        )
        out = await router.generate(request())
        assert out.text == "ok"
        assert gemini.calls[0][0] == "flash"
        assert router.route_for("cv_interpret") == ModelRoute(Provider.GEMINI, "flash")
        assert router.route_for("autre") is None

    async def test_une_tache_sans_route_ou_un_fournisseur_absent_refusent_a_l_appel(self) -> None:
        router = LlmRouter(RoutingTable.parse({"a": "openai:o"}), {})
        with pytest.raises(LlmUnavailableError, match="aucun modèle"):
            await router.generate(request(task="zz"))
        with pytest.raises(LlmUnavailableError, match="LLM__PROVIDERS__OPENAI__API_KEY"):
            await router.generate(request(task="a"))

    async def test_le_repli_prend_la_panne_et_la_capacite_absente_mais_pas_le_refus(self) -> None:
        primary = FakeAdapter(
            Provider.ANTHROPIC,
            LlmUnavailableError("503"),
            LlmCapabilityError("audio"),
            result(None, refused=True),
        )
        backup = FakeAdapter(Provider.GEMINI, result("secours"), result("secours"))
        router = LlmRouter(
            RoutingTable.parse({"t": "anthropic:c"}, {"t": "gemini:g"}),
            {Provider.ANTHROPIC: primary, Provider.GEMINI: backup},
        )
        assert (await router.generate(request(task="t"))).text == "secours"
        assert (await router.generate(request(task="t"))).text == "secours"
        assert (await router.generate(request(task="t"))).refused is True
        assert len(backup.calls) == 2

    async def test_sans_repli_la_panne_remonte(self) -> None:
        primary = FakeAdapter(Provider.GEMINI, LlmUnavailableError("503"))
        router = LlmRouter(
            RoutingTable.parse({"t": "gemini:g"}, {"t": "gemini:g"}), {Provider.GEMINI: primary}
        )
        with pytest.raises(LlmUnavailableError):
            await router.generate(request(task="t"))

    async def test_le_modele_desactive_refuse_a_l_appel(self) -> None:
        with pytest.raises(LlmUnavailableError):
            await DisabledLanguageModel().generate(request())


def production(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "app": AppSettings(environment=Environment.PRODUCTION, debug=False),
        "security": SecuritySettings(
            secret_key=SecretStr("s" * 40),
            token_pepper=SecretStr("p" * 40),
            allowed_hosts=["api.karria.fr"],
            allowed_origins=["https://karria.fr"],
        ),
        "storage": StorageSettings(
            provider=StorageProvider.SCALEWAY,
            region="fr-par",
            access_key=SecretStr("a"),
            secret_key=SecretStr("b"),
        ),
        "mail": MailSettings(
            provider=MailProvider.SMTP,
            host="smtp",
            username="u@karria.fr",
            password=SecretStr("x"),
            from_address="u@karria.fr",
        ),
    }
    base.update(changes)
    return Settings(**base)


class TestConfiguration:
    def test_la_configuration_et_le_routeur_connaissent_les_memes_fournisseurs(self) -> None:
        assert set(LLM_PROVIDERS) == {p.value for p in Provider}

    def test_les_routes_par_defaut_viennent_de_gemini(self) -> None:
        settings = Settings(
            gemini=GeminiSettings(api_key=SecretStr("k"), text_model="t", image_model="i")
        )
        assert settings.llm.tasks.search_dialogue == "gemini:t"
        assert settings.llm.tasks.portrait == "gemini:i"
        assert settings.llm.providers.gemini.api_key.get_secret_value() == "k"
        assert settings.llm.providers.gemini.base_url == settings.gemini.base_url
        assert settings.llm_problems() == []

    def test_une_route_explicite_est_respectee(self) -> None:
        settings = Settings(
            llm=LlmSettings(
                providers=LlmProvidersSettings(
                    anthropic=LlmProviderSettings(api_key=SecretStr("a"))
                ),
                tasks=LlmTaskRoutes(cv_extract="anthropic:claude-x"),
            )
        )
        assert settings.llm.tasks.cv_extract == "anthropic:claude-x"
        assert settings.llm.tasks.cv_interpret == "gemini:gemini-3.8-flash"
        assert settings.llm_problems() == []

    def test_une_route_explicite_sans_cle_bloque_le_demarrage_deploye(self) -> None:
        with pytest.raises(ValueError, match="LLM__PROVIDERS__OPENAI__API_KEY"):
            production(llm=LlmSettings(tasks=LlmTaskRoutes(transcribe="openai:gpt-x")))

    def test_une_route_mal_formee_bloque_le_demarrage_deploye(self) -> None:
        with pytest.raises(ValueError, match="LLM__TASKS__CV_EXTRACT"):
            production(llm=LlmSettings(tasks=LlmTaskRoutes(cv_extract="claude-x")))

    def test_une_cle_gemini_absente_par_defaut_ne_bloque_pas(self) -> None:
        settings = production()
        assert settings.llm_problems() == []
        assert isinstance(build_language_model(settings), DisabledLanguageModel)

    def test_l_assemblage_cree_un_adaptateur_par_fournisseur_configure(self) -> None:
        settings = Settings(
            gemini=GeminiSettings(api_key=SecretStr("g")),
            llm=LlmSettings(
                providers=LlmProvidersSettings(
                    anthropic=LlmProviderSettings(api_key=SecretStr("a")),
                    compatible=LlmProviderSettings(
                        base_url="http://vllm:8000/v1", structured_output="json_object"
                    ),
                ),
                tasks=LlmTaskRoutes(cv_extract="anthropic:c", enrich="compatible:local"),
            ),
        )
        llm = build_language_model(settings)
        assert isinstance(llm, LlmRouter)
        adapters = llm._adapters  # type: ignore[attr-defined]
        assert set(adapters) == {Provider.GEMINI, Provider.ANTHROPIC, Provider.COMPATIBLE}
        assert adapters[Provider.COMPATIBLE].provider is Provider.COMPATIBLE

    def test_une_route_incomplete_en_local_avertit_mais_assemble(self) -> None:
        settings = Settings(
            gemini=GeminiSettings(api_key=SecretStr("g")),
            llm=LlmSettings(tasks=LlmTaskRoutes(transcribe="openai:gpt")),
        )
        assert settings.llm_problems()
        assert isinstance(build_language_model(settings), LlmRouter)
