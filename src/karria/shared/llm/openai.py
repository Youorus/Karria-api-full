"""OpenAI, et toute API compatible, par `POST /chat/completions`.

Un seul adaptateur pour deux fournisseurs du routeur : `openai` (l'API
d'OpenAI, clé `Bearer`) et `compatible` (vLLM, Mistral, un modèle local —
même forme de requête, une autre `base_url`, et une sortie structurée qui
n'est pas toujours servie). Formes vérifiées sur la documentation le
2026-10-22 :

- sortie structurée par `response_format = {type: json_schema, json_schema:
  {name, schema, strict}}` ; en mode `compatible`, repli possible sur
  `{type: json_object}` avec le schéma rappelé dans la consigne
  (`structured_output="json_object"`) ;
- image en `image_url` (URL `data:`), audio en `input_audio` (`wav` ou `mp3`
  seulement), PDF en `file` (`file_data` en URL `data:`) ;
- `temperature` acceptée ; pas de génération d'image par cette route.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from typing import Any, Final, Literal

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

DEFAULT_BASE_URL: Final = "https://api.openai.com/v1"
#: Les formats audio que l'API lit en `input_audio`.
AUDIO_FORMATS: Final[dict[str, str]] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
}
StructuredOutput = Literal["json_schema", "json_object"]


class OpenAIAdapter:
    """Implémente `ProviderAdapter` pour OpenAI et les API compatibles."""

    def __init__(
        self,
        http: JsonPoster,
        *,
        provider: Provider = Provider.OPENAI,
        structured_output: StructuredOutput = "json_schema",
    ) -> None:
        self._http = http
        self.provider = provider
        self._structured_output = structured_output

    @classmethod
    def from_credentials(
        cls,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        provider: Provider = Provider.OPENAI,
        structured_output: StructuredOutput = "json_schema",
    ) -> OpenAIAdapter:
        """Le client sortant du socle, la clé en `Bearer` (facultative pour un modèle local)."""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        return cls(
            JsonClient(base_url=base_url, headers=headers, timeout_seconds=timeout_seconds),
            provider=provider,
            structured_output=structured_output,
        )

    async def generate(self, model: str, request: LlmRequest) -> LlmResult:
        """Appelle `/chat/completions` et décode la réponse."""
        if request.image_output:
            raise LlmCapabilityError(f"{self.provider.value} ne génère pas d'image par cette route")
        started = time.monotonic()
        try:
            body = (
                await self._http.post_json(
                    "/chat/completions", payload(model, request, self._structured_output)
                )
            ).mapping
        except OutboundError as exc:
            log = logger.error if is_our_fault(exc.status) else logger.warning
            log(
                "llm.call_failed",
                provider=self.provider.value,
                model=model,
                status=exc.status,
                detail=exc.detail,
            )
            raise LlmUnavailableError(
                f"HTTP {exc.status}" if exc.status else "injoignable", status=exc.status
            ) from exc
        result = decode(
            body, model, self.provider, latency_ms=int((time.monotonic() - started) * 1000)
        )
        logger.info(
            "llm.generated",
            provider=self.provider.value,
            model=model,
            task=request.task,
            finish_reason=result.finish_reason,
            refused=result.refused,
            usage_in=result.input_tokens,
            usage_out=result.output_tokens,
            latency_ms=result.latency_ms,
        )
        return result


def _content(part: str | Blob) -> dict[str, Any]:
    if isinstance(part, str):
        return {"type": "text", "text": part}
    encoded = base64.b64encode(part.data).decode("ascii")
    if part.kind == "image":
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{part.content_type};base64,{encoded}"},
        }
    if part.kind == "audio":
        audio_format = AUDIO_FORMATS.get(part.content_type)
        if audio_format is None:
            raise LlmCapabilityError(f"audio {part.content_type} non lu par cette route (wav, mp3)")
        return {"type": "input_audio", "input_audio": {"data": encoded, "format": audio_format}}
    return {
        "type": "file",
        "file": {
            "filename": "document.pdf",
            "file_data": f"data:{part.content_type};base64,{encoded}",
        },
    }


def payload(model: str, request: LlmRequest, structured_output: StructuredOutput) -> dict[str, Any]:
    """Le corps `/chat/completions`."""
    messages: list[dict[str, Any]] = []
    system = request.system
    if request.response_schema is not None and structured_output == "json_object":
        reminder = "Réponds uniquement par un objet JSON conforme à ce schéma :\n" + json.dumps(
            dict(request.response_schema), ensure_ascii=False
        )
        system = f"{system}\n\n{reminder}" if system else reminder
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.extend({"role": role, "content": text} for role, text in request.history)
    messages.append({"role": "user", "content": [_content(part) for part in request.parts]})
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": request.temperature,
    }
    if request.max_output_tokens is not None:
        body["max_completion_tokens"] = request.max_output_tokens
    if request.response_schema is not None:
        if structured_output == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "reponse",
                    "schema": strict_schema(request.response_schema),
                    "strict": True,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
    return body


def decode(
    body: Mapping[str, Any], model: str, provider: Provider, *, latency_ms: int
) -> LlmResult:
    """La réponse `/chat/completions`, sans l'interpréter."""
    usage = body.get("usage") or {}
    choices = body.get("choices") or []
    if not choices:
        return LlmResult(
            text=None,
            image=None,
            finish_reason="no_choice",
            refused=True,
            provider=provider,
            model=str(body.get("model") or model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # certains serveurs compatibles rendent des blocs
        content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    refusal = message.get("refusal")
    finish = choice.get("finish_reason")
    return LlmResult(
        text=str(content) if content else None,
        image=None,
        finish_reason=str(finish) if finish is not None else None,
        refused=bool(refusal) or finish == "content_filter",
        provider=provider,
        model=str(body.get("model") or model),
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        latency_ms=latency_ms,
    )
