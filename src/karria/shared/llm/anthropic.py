"""Anthropic par l'API REST Messages : `POST /v1/messages`.

Même choix que pour Gemini (ADR-0013, ADR-0018) : pas de SDK, le client
sortant du socle, la clé dans l'en-tête `x-api-key`. Formes vérifiées sur
la documentation de l'API le 2026-10-22 :

- `system` en champ de premier niveau, `messages` alternés `user`/`assistant` ;
- sortie structurée par `output_config.format = {type: json_schema, schema}` ;
- image en bloc `{"type": "image", "source": {"type": "base64", …}}`, PDF
  en bloc `{"type": "document", …}` ; **pas d'audio** : une requête qui en
  porte lève `LlmCapabilityError`, et le routeur doit la confier ailleurs ;
- la réflexion des modèles Claude 5.x est toujours active et la température
  n'est plus réglable : la requête n'envoie ni l'une ni l'autre, et
  `output_config.effort = "low"` tient le coût d'une extraction ;
- un arrêt `refusal` est un refus de contenu, pas une panne.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Mapping
from typing import Any, Final

from karria.shared.http.outbound import JsonClient, OutboundError
from karria.shared.llm.gemini import JsonPoster, is_our_fault
from karria.shared.llm.ports import (
    Blob,
    LlmCapabilityError,
    LlmRequest,
    LlmResult,
    LlmUnavailableError,
    Provider,
)
from karria.shared.llm.schema import strict_schema
from karria.shared.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL: Final = "https://api.anthropic.com"
API_VERSION: Final = "2023-06-01"
#: Sans plafond, l'API exige `max_tokens` : une valeur large, qui ne coupe
#: aucune de nos sorties (le CV extrait tient en quelques milliers de jetons).
DEFAULT_MAX_TOKENS: Final = 8_192
#: Effort de réflexion : bas pour nos tâches, qui sont des extractions et
#: un dialogue court ; c'est le réglage de coût et de latence des modèles 5.x.
EFFORT: Final = "low"


class AnthropicAdapter:
    """Implémente `ProviderAdapter` pour l'API Messages."""

    provider = Provider.ANTHROPIC

    def __init__(self, http: JsonPoster) -> None:
        self._http = http

    @classmethod
    def from_credentials(
        cls, api_key: str, *, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = 60.0
    ) -> AnthropicAdapter:
        """Le client sortant du socle, la clé et la version en en-tête."""
        return cls(
            JsonClient(
                base_url=base_url,
                headers={"x-api-key": api_key, "anthropic-version": API_VERSION},
                timeout_seconds=timeout_seconds,
            )
        )

    async def generate(self, model: str, request: LlmRequest) -> LlmResult:
        """Appelle `/v1/messages` et décode la réponse."""
        if request.image_output:
            raise LlmCapabilityError("Anthropic ne génère pas d'image")
        if any(blob.kind == "audio" for blob in request.blobs):
            raise LlmCapabilityError("Anthropic ne lit pas l'audio")
        started = time.monotonic()
        try:
            body = (await self._http.post_json("/v1/messages", payload(model, request))).mapping
        except OutboundError as exc:
            log = logger.error if is_our_fault(exc.status) else logger.warning
            log(
                "llm.call_failed",
                provider="anthropic",
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
            provider="anthropic",
            model=model,
            task=request.task,
            finish_reason=result.finish_reason,
            refused=result.refused,
            usage_in=result.input_tokens,
            usage_out=result.output_tokens,
            latency_ms=result.latency_ms,
        )
        return result


def _block(part: str | Blob) -> dict[str, Any]:
    if isinstance(part, str):
        return {"type": "text", "text": part}
    source = {
        "type": "base64",
        "media_type": part.content_type,
        "data": base64.b64encode(part.data).decode("ascii"),
    }
    return {"type": "image" if part.kind == "image" else "document", "source": source}


def payload(model: str, request: LlmRequest) -> dict[str, Any]:
    """Le corps `/v1/messages`."""
    messages: list[dict[str, Any]] = [
        {"role": role, "content": text} for role, text in request.history
    ]
    messages.append({"role": "user", "content": [_block(part) for part in request.parts]})
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_output_tokens or DEFAULT_MAX_TOKENS,
        "messages": messages,
        "output_config": {"effort": EFFORT},
    }
    if request.system is not None:
        body["system"] = request.system
    if request.response_schema is not None:
        body["output_config"]["format"] = {
            "type": "json_schema",
            "schema": strict_schema(request.response_schema),
        }
    return body


def decode(body: Mapping[str, Any], model: str, *, latency_ms: int) -> LlmResult:
    """La réponse Messages, sans l'interpréter."""
    usage = body.get("usage") or {}
    texts = [
        str(block.get("text", ""))
        for block in body.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    stop = body.get("stop_reason")
    return LlmResult(
        text="".join(texts) if texts else None,
        image=None,
        finish_reason=str(stop) if stop is not None else None,
        refused=stop == "refusal",
        provider=Provider.ANTHROPIC,
        model=str(body.get("model") or model),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        latency_ms=latency_ms,
    )
