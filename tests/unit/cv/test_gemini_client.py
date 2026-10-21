"""Le client Gemini : composition de la requête, décodage, pannes — sans réseau."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from doubles import FakePoster
from karria.modules.cv.domain import (
    Experience,
    ModelOutputInvalidError,
    ModelUnavailableError,
)
from karria.modules.cv.infrastructure import (
    DisabledTransport,
    GeminiClient,
    GenerationRequest,
    GenerationResult,
    InlinePart,
    response_schema_for,
)
from karria.shared.config import GeminiSettings
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError

pytestmark = pytest.mark.unit


def ok(body: dict[str, Any]) -> JsonResponse:
    return JsonResponse(200, body)


def candidate(*parts: dict[str, Any], finish: str = "STOP", **extra: Any) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": list(parts)}, "finishReason": finish}],
        "usageMetadata": {"totalTokenCount": 12},
        **extra,
    }


class TestRequete:
    async def test_le_corps_porte_consigne_donnees_et_schema(self) -> None:
        poster = FakePoster(ok(candidate({"text": "{}"})))
        request = GenerationRequest(
            model="gemini-x",
            parts=("bonjour", InlinePart(b"\x00\x01", "audio/mp4")),
            system="règle",
            response_schema={"type": "object"},
            temperature=0.3,
            thinking_budget=0,
        )

        await GeminiClient(poster).generate(request)

        [(path, payload)] = poster.calls
        assert path == "models/gemini-x:generateContent"
        assert payload["system_instruction"] == {"parts": [{"text": "règle"}]}
        assert payload["contents"] == [
            {
                "role": "user",
                "parts": [
                    {"text": "bonjour"},
                    {
                        "inline_data": {
                            "mime_type": "audio/mp4",
                            "data": base64.b64encode(b"\x00\x01").decode(),
                        }
                    },
                ],
            }
        ]
        assert payload["generation_config"] == {
            "temperature": 0.3,
            "response_mime_type": "application/json",
            "response_json_schema": {"type": "object"},
            "thinking_config": {"thinking_budget": 0},
        }

    async def test_une_requete_image_declare_modalite_et_format(self) -> None:
        poster = FakePoster(
            ok(candidate({"inlineData": {"mimeType": "image/png", "data": "AAEC"}}))
        )

        result = await GeminiClient(poster).generate(
            GenerationRequest(
                model="img", parts=("x",), response_modalities=("IMAGE",), aspect_ratio="3:4"
            )
        )

        [(_, payload)] = poster.calls
        config = payload["generation_config"]
        assert config["response_modalities"] == ["IMAGE"]
        assert config["image_config"] == {"aspect_ratio": "3:4"}
        assert "system_instruction" not in payload
        assert "response_mime_type" not in config
        assert result.image is not None
        assert result.image.payload == b"\x00\x01\x02"
        assert result.image.content_type == "image/png"


class TestDecodage:
    async def test_les_textes_sont_concatenes_et_les_pensees_ignorees(self) -> None:
        poster = FakePoster(
            ok(candidate({"text": "pensée", "thought": True}, {"text": '{"a":'}, {"text": " 1}"}))
        )

        result = await GeminiClient(poster).generate(GenerationRequest(model="m", parts=("x",)))

        assert result.text == '{"a": 1}'
        assert result.json() == {"a": 1}
        assert result.finish_reason == "STOP"
        assert not result.refused

    async def test_un_blocage_du_prompt_est_un_refus(self) -> None:
        poster = FakePoster(ok({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}))

        result = await GeminiClient(poster).generate(GenerationRequest(model="m", parts=("x",)))

        assert result.refused
        assert result.block_reason == "PROHIBITED_CONTENT"
        assert result.text is None

    async def test_aucun_candidat_est_un_refus_nomme(self) -> None:
        poster = FakePoster(ok({"candidates": []}))

        result = await GeminiClient(poster).generate(GenerationRequest(model="m", parts=("x",)))

        assert result.block_reason == "NO_CANDIDATE"
        assert result.refused

    async def test_un_motif_darret_de_securite_est_un_refus(self) -> None:
        poster = FakePoster(ok(candidate(finish="IMAGE_SAFETY")))

        result = await GeminiClient(poster).generate(GenerationRequest(model="m", parts=("x",)))

        assert result.refused
        assert result.image is None
        assert result.text is None

    def test_json_refuse_le_vide_et_le_non_json(self) -> None:
        with pytest.raises(ModelOutputInvalidError) as excinfo:
            GenerationResult(None, None, "STOP", None).json()
        assert excinfo.value.details["reason"] == "ai_output_invalid"
        with pytest.raises(ModelOutputInvalidError):
            GenerationResult("pas du json", None, "STOP", None).json()

    async def test_la_premiere_image_seulement_est_retenue(self) -> None:
        poster = FakePoster(
            ok(
                candidate(
                    {"inlineData": {"mimeType": "image/jpeg", "data": "AA=="}},
                    {"inlineData": {"mimeType": "image/png", "data": "AQ=="}},
                )
            )
        )

        result = await GeminiClient(poster).generate(GenerationRequest(model="m", parts=("x",)))

        assert result.image is not None
        assert result.image.content_type == "image/jpeg"


class TestPannes:
    @pytest.mark.parametrize(
        ("status", "expected_reason"),
        [(400, "HTTP 400"), (429, "HTTP 429"), (503, "HTTP 503"), (None, "injoignable")],
    )
    async def test_une_erreur_sortante_devient_indisponibilite(
        self, status: int | None, expected_reason: str
    ) -> None:
        poster = FakePoster(OutboundError("boum", status=status))

        with pytest.raises(ModelUnavailableError) as excinfo:
            await GeminiClient(poster).generate(GenerationRequest(model="m", parts=("x",)))

        assert excinfo.value.details == {
            "dependency": "Gemini",
            "reason": "ai_unavailable",
            "detail": expected_reason,
        }
        assert isinstance(excinfo.value.__cause__, OutboundError)

    async def test_un_corps_qui_nest_pas_un_objet_est_une_panne(self) -> None:
        poster = FakePoster(JsonResponse(200, ["pas", "un", "objet"]))

        with pytest.raises(ModelUnavailableError):
            await GeminiClient(poster).generate(GenerationRequest(model="m", parts=("x",)))

    async def test_sans_cle_le_transport_refuse_a_lappel(self) -> None:
        with pytest.raises(ModelUnavailableError):
            await DisabledTransport().generate(GenerationRequest(model="m", parts=("x",)))


class TestAssemblage:
    def test_le_client_sortant_porte_la_cle_en_entete_et_le_delai(self) -> None:
        settings = GeminiSettings(api_key=SecretStr("k-secret"), base_url="https://g.test/v1beta")

        client = GeminiClient.from_settings(settings, timeout_seconds=42.0)

        http = client._http
        assert isinstance(http, JsonClient)
        assert http.base_url == "https://g.test/v1beta"
        assert http.headers == {"x-goog-api-key": "k-secret"}
        assert http.timeout_seconds == 42.0

    def test_la_configuration_sait_si_elle_est_active(self) -> None:
        assert not GeminiSettings().enabled
        assert GeminiSettings(api_key=SecretStr("x")).enabled


def all_keys(node: Any) -> set[str]:
    """Toutes les clés d'un schéma, à toute profondeur."""
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in all_keys(v)}
    if isinstance(node, list):
        return {k for item in node for k in all_keys(item)}
    return set()


class TestSchema:
    def test_le_schema_est_autonome_sobre_et_sans_identifiants(self) -> None:
        class Out(BaseModel):
            proposal: Experience | None
            missing: list[str]

        schema = response_schema_for(Out)

        keys = all_keys(schema)
        assert "$ref" not in keys
        assert "$defs" not in keys
        for noise in ("title", "default", "format", "minLength", "maxLength", "maxItems"):
            assert noise not in keys, noise
        experience = schema["properties"]["proposal"]["anyOf"][0]
        assert "id" not in experience["properties"]
        assert "id" not in experience["required"]
        assert experience["properties"]["missions"]["items"] == {"type": "string"}
        assert schema["required"] == ["proposal", "missing"]
        assert "description" in experience["properties"]["start_date"]
