"""Gemini par l'API REST : `POST models/{modèle}:generateContent`.

Pourquoi l'API REST et non le SDK `google-genai` : une douzaine de paquets
(gRPC, auth, protobuf) pour un appel JSON, et un `Client()` qui lit la clé
dans l'environnement à notre insu (ADR-0013). Le client sortant du socle a
déjà la reprise sur 429/5xx, la traduction des pannes et la journalisation
sans secret : la clé voyage dans l'en-tête `x-goog-api-key`, jamais dans
l'URL. Le binaire part en `inline_data` base64 (20 Mo par requête, loin
au-dessus de nos plafonds). Gemini lit texte, image, audio et PDF, et sait
rendre une image : c'est le seul adaptateur qui porte toutes les capacités.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Mapping
from typing import Any, Final, Protocol

from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError
from karria.shared.llm.ports import (
    Blob,
    LlmRequest,
    LlmResult,
    LlmUnavailableError,
    Provider,
)
from karria.shared.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta"
#: Motifs d'arrêt qui signifient « le modèle refuse » (relevés sur la V1 et
#: dans la documentation). Un refus n'est pas une panne : on ne le retente
#: pas, on le dit.
REFUSAL_REASONS: Final[frozenset[str]] = frozenset(
    {
        "SAFETY",
        "IMAGE_SAFETY",
        "PROHIBITED_CONTENT",
        "IMAGE_PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "RECITATION",
        "IMAGE_OTHER",
        "NO_IMAGE",
    }
)
_CLIENT_ERROR_MIN: Final = 400
_CLIENT_ERROR_MAX: Final = 500
_TOO_MANY_REQUESTS: Final = 429


class JsonPoster(Protocol):
    """Ce que l'adaptateur attend du client HTTP : un `POST` JSON."""

    async def post_json(self, path: str, payload: Any) -> JsonResponse:
        """Envoie un corps JSON et rend la réponse décodée."""
        ...


def is_our_fault(status: int | None) -> bool:
    """Un 4xx autre que 429 : requête mal formée ou clé invalide, pas une panne."""
    return (
        status is not None
        and _CLIENT_ERROR_MIN <= status < _CLIENT_ERROR_MAX
        and status != _TOO_MANY_REQUESTS
    )


class GeminiAdapter:
    """Implémente `ProviderAdapter` pour Gemini."""

    provider = Provider.GEMINI

    def __init__(self, http: JsonPoster) -> None:
        self._http = http

    @classmethod
    def from_credentials(
        cls, api_key: str, *, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = 60.0
    ) -> GeminiAdapter:
        """Le client sortant du socle, la clé en en-tête."""
        return cls(
            JsonClient(
                base_url=base_url,
                headers={"x-goog-api-key": api_key},
                timeout_seconds=timeout_seconds,
            )
        )

    async def generate(self, model: str, request: LlmRequest) -> LlmResult:
        """Appelle `generateContent` et décode la réponse."""
        started = time.monotonic()
        try:
            body = (
                await self._http.post_json(f"models/{model}:generateContent", payload(request))
            ).mapping
        except OutboundError as exc:
            log = logger.error if is_our_fault(exc.status) else logger.warning
            log(
                "llm.call_failed",
                provider="gemini",
                model=model,
                status=exc.status,
                detail=exc.detail,
            )
            raise LlmUnavailableError(
                f"HTTP {exc.status}" if exc.status else "injoignable", status=exc.status
            ) from exc
        result = decode(body, model, latency_ms=int((time.monotonic() - started) * 1000))
        logger.info(
            "llm.generated",
            provider="gemini",
            model=model,
            task=request.task,
            finish_reason=result.finish_reason,
            refused=result.refused,
            usage_in=result.input_tokens,
            usage_out=result.output_tokens,
            latency_ms=result.latency_ms,
        )
        return result


def payload(request: LlmRequest) -> dict[str, Any]:
    """Le corps `generateContent`."""
    contents: list[dict[str, Any]] = [
        {"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]}
        for role, text in request.history
    ]
    parts: list[dict[str, Any]] = []
    for part in request.parts:
        if isinstance(part, Blob):
            parts.append(
                {
                    "inline_data": {
                        "mime_type": part.content_type,
                        "data": base64.b64encode(part.data).decode("ascii"),
                    }
                }
            )
        else:
            parts.append({"text": part})
    contents.append({"role": "user", "parts": parts})

    generation_config: dict[str, Any] = {"temperature": request.temperature}
    if request.max_output_tokens is not None:
        generation_config["max_output_tokens"] = request.max_output_tokens
    if request.response_schema is not None:
        generation_config["response_mime_type"] = "application/json"
        generation_config["response_json_schema"] = dict(request.response_schema)
    if request.image_output:
        generation_config["response_modalities"] = ["IMAGE"]
        if request.aspect_ratio is not None:
            generation_config["image_config"] = {"aspect_ratio": request.aspect_ratio}
    elif not request.thinking:
        # Les modèles Flash « réfléchissent » par défaut ; pour une
        # extraction, c'est deux à trois secondes d'attente pour rien.
        generation_config["thinking_config"] = {"thinking_budget": 0}

    body: dict[str, Any] = {"contents": contents, "generation_config": generation_config}
    if request.system is not None:
        body["system_instruction"] = {"parts": [{"text": request.system}]}
    return body


def decode(body: Mapping[str, Any], model: str, *, latency_ms: int) -> LlmResult:
    """La réponse `generateContent`, sans l'interpréter."""
    usage = body.get("usageMetadata") or {}
    feedback = body.get("promptFeedback") or {}
    block_reason = feedback.get("blockReason")
    candidates = body.get("candidates") or []
    if not candidates:
        return LlmResult(
            text=None,
            image=None,
            finish_reason=str(block_reason or "NO_CANDIDATE"),
            refused=True,
            provider=Provider.GEMINI,
            model=model,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            latency_ms=latency_ms,
        )
    candidate = candidates[0]
    texts: list[str] = []
    image: Blob | None = None
    for part in (candidate.get("content") or {}).get("parts") or []:
        if part.get("thought"):
            continue
        if "text" in part:
            texts.append(str(part["text"]))
        inline = part.get("inlineData")
        if inline and image is None:
            image = Blob(
                data=base64.b64decode(inline.get("data", "")),
                content_type=str(inline.get("mimeType", "application/octet-stream")),
            )
    finish = candidate.get("finishReason")
    return LlmResult(
        text="".join(texts) if texts else None,
        image=image,
        finish_reason=finish,
        refused=block_reason is not None or finish in REFUSAL_REASONS,
        provider=Provider.GEMINI,
        model=model,
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        latency_ms=latency_ms,
    )
