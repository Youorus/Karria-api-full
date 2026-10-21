"""Le client Gemini : un seul endroit qui connaît l'API REST du fournisseur.

Pourquoi l'API REST et non le SDK `google-genai`
------------------------------------------------
Le SDK tire une douzaine de paquets (gRPC, auth Google, protobuf) pour
trois appels `generateContent`. Le socle a déjà un client JSON sortant
(`shared/http/outbound.py`) avec reprise sur 429/5xx et traduction des
pannes ; l'API Gemini est du JSON par-dessus HTTPS, et le contenu binaire
(audio, image, PDF) y passe en `inline_data` encodé en base64, jusqu'à
20 Mo par requête — largement au-dessus de nos plafonds. La clé voyage
dans l'en-tête `x-goog-api-key`, jamais dans l'URL : le client sortant ne
journalise que l'hôte (ADR-0013).

Ce que ce fichier fait, et ne fait pas
--------------------------------------
Il compose la requête, décode la réponse, et traduit les pannes et les
refus en erreurs du domaine. Il ne connaît **aucun prompt** et aucun
chapitre : ce sont les adaptateurs (`interpreter`, `transcriber`,
`enhancer`, `extractor`) qui savent quoi demander. Il est donc le seul
fichier à retoucher si Google change la forme de son API.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from pydantic import BaseModel

from karria.modules.cv.application import GeneratedImage
from karria.modules.cv.domain import ModelOutputInvalidError, ModelUnavailableError
from karria.shared.config import GeminiSettings
from karria.shared.http.outbound import JsonClient, JsonResponse, OutboundError
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Motifs d'arrêt qui signifient « le modèle refuse », tels que relevés sur
#: la V1 (`profile_photo/generator.py`) et dans la documentation de l'API.
#: Un refus n'est pas une panne : on ne le retente pas, on le dit.
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

#: Clés de schéma que Gemini ne connaît pas ou interprète mal. `format`
#: est retiré parce que Pydantic l'emploie pour `uuid` et `date-time`, que
#: le modèle n'a pas à produire. Les bornes de longueur sont retirées
#: aussi : constaté le 2026-10-21, le schéma complet d'extraction est
#: refusé (`400 INVALID_ARGUMENT`) tant qu'elles y figurent, et accepté
#: sans — Pydantic les applique de toute façon à la validation.
_SCHEMA_NOISE: Final[frozenset[str]] = frozenset(
    {"title", "default", "format", "minLength", "maxLength", "minItems", "maxItems"}
)


@dataclass(frozen=True, slots=True)
class InlinePart:
    """Un contenu binaire — audio, image, PDF — joint à la requête."""

    data: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """Tout ce qu'un appel `generateContent` peut porter.

    Attributes:
        parts: Texte et contenus binaires, dans l'ordre où le modèle les lit.
        system: Consigne de système — la règle, séparée des données.
        response_schema: Schéma JSON imposé à la sortie (texte uniquement).
        temperature: Basse pour extraire, un peu plus haute pour créer.
        response_modalities: `("IMAGE",)` pour un générateur d'image.
        aspect_ratio: Format de l'image produite (`"3:4"` pour un portrait).
        thinking_budget: `0` coupe la réflexion interne des modèles Flash :
            deux à trois fois plus rapide, pour une extraction qui n'en a
            pas besoin.
    """

    model: str
    parts: tuple[str | InlinePart, ...]
    system: str | None = None
    response_schema: Mapping[str, Any] | None = None
    temperature: float = 0.2
    response_modalities: tuple[str, ...] | None = None
    aspect_ratio: str | None = None
    thinking_budget: int | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Ce que le modèle a rendu, décodé mais non interprété."""

    text: str | None
    image: GeneratedImage | None
    finish_reason: str | None
    block_reason: str | None

    @property
    def refused(self) -> bool:
        """Le modèle a refusé de répondre, pour l'entrée ou pour la sortie."""
        return self.block_reason is not None or self.finish_reason in REFUSAL_REASONS

    def json(self) -> Any:
        """Le texte, décodé en JSON.

        Raises:
            ModelOutputInvalidError: Pas de texte, ou pas du JSON.
        """
        if self.text is None:
            raise ModelOutputInvalidError("réponse sans texte")
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            logger.warning("gemini.output_not_json", excerpt=self.text[:200])
            raise ModelOutputInvalidError("réponse qui n'est pas du JSON") from exc


class GenerationTransport(Protocol):
    """Port interne à l'infrastructure : ce que les adaptateurs attendent du client.

    Déclaré pour que chaque adaptateur se teste avec un transport factice
    qui rend une `GenerationResult` toute faite, sans réseau.
    """

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Exécute un appel `generateContent`."""
        ...


class JsonPoster(Protocol):
    """Ce que le client Gemini attend du client HTTP : un `POST` JSON."""

    async def post_json(self, path: str, payload: Any) -> JsonResponse:
        """Envoie un corps JSON et rend la réponse décodée."""
        ...


class DisabledTransport:
    """Le transport quand aucune clé n'est configurée.

    Refuse **à l'appel**, pas au démarrage : on doit pouvoir lancer l'API
    et exercer tout le reste sans compte Google ; la configuration l'annonce
    au démarrage (`config.gemini_disabled`) sans le bloquer.
    """

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Lève toujours : la fonctionnalité n'est pas configurée."""
        logger.warning("gemini.disabled", model=request.model)
        raise ModelUnavailableError("GEMINI__API_KEY n'est pas configurée")


class GeminiClient:
    """Implémente `GenerationTransport` au-dessus de l'API REST."""

    def __init__(self, http: JsonPoster) -> None:
        self._http = http

    @classmethod
    def from_settings(cls, settings: GeminiSettings, *, timeout_seconds: float) -> GeminiClient:
        """Assemble le client avec le client sortant du socle."""
        return cls(
            JsonClient(
                base_url=settings.base_url,
                headers={"x-goog-api-key": settings.api_key.get_secret_value()},
                timeout_seconds=timeout_seconds,
            )
        )

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Appelle `generateContent` et décode la réponse.

        Raises:
            ModelUnavailableError: Panne réseau, quota, erreur du fournisseur.
        """
        try:
            response = await self._http.post_json(
                f"models/{request.model}:generateContent", _payload(request)
            )
            # `mapping` lève si le corps n'est pas un objet JSON : c'est une
            # panne du fournisseur comme une autre, traitée ici.
            body = response.mapping
        except OutboundError as exc:
            # Un 4xx autre que 429 est une requête que **nous** avons mal
            # formée, ou une clé invalide : une erreur à nos yeux, pas une
            # panne du fournisseur. Elle se journalise plus fort.
            log = logger.error if _is_our_fault(exc.status) else logger.warning
            log("gemini.call_failed", model=request.model, status=exc.status, detail=exc.detail)
            raise ModelUnavailableError(
                f"HTTP {exc.status}" if exc.status else "injoignable"
            ) from exc

        result = _decode(body)
        logger.info(
            "gemini.generated",
            model=request.model,
            finish_reason=result.finish_reason,
            block_reason=result.block_reason,
            usage=body.get("usageMetadata"),
        )
        return result


#: Bornes des statuts HTTP « erreur du client », et le seul d'entre eux qui
#: ne dit rien contre nous : 429, « trop vite ».
_CLIENT_ERROR_MIN: Final = 400
_CLIENT_ERROR_MAX: Final = 500
_TOO_MANY_REQUESTS: Final = 429


def _is_our_fault(status: int | None) -> bool:
    """Un 4xx autre que 429 : requête mal formée ou clé invalide, pas une panne."""
    return (
        status is not None
        and _CLIENT_ERROR_MIN <= status < _CLIENT_ERROR_MAX
        and status != _TOO_MANY_REQUESTS
    )


def _payload(request: GenerationRequest) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for part in request.parts:
        if isinstance(part, InlinePart):
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

    generation_config: dict[str, Any] = {"temperature": request.temperature}
    if request.response_schema is not None:
        generation_config["response_mime_type"] = "application/json"
        generation_config["response_json_schema"] = dict(request.response_schema)
    if request.response_modalities is not None:
        generation_config["response_modalities"] = list(request.response_modalities)
    if request.aspect_ratio is not None:
        generation_config["image_config"] = {"aspect_ratio": request.aspect_ratio}
    if request.thinking_budget is not None:
        generation_config["thinking_config"] = {"thinking_budget": request.thinking_budget}

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generation_config": generation_config,
    }
    if request.system is not None:
        payload["system_instruction"] = {"parts": [{"text": request.system}]}
    return payload


def _decode(body: Mapping[str, Any]) -> GenerationResult:
    feedback = body.get("promptFeedback") or {}
    block_reason = feedback.get("blockReason")
    candidates = body.get("candidates") or []
    if not candidates:
        return GenerationResult(
            text=None, image=None, finish_reason=None, block_reason=block_reason or "NO_CANDIDATE"
        )

    candidate = candidates[0]
    texts: list[str] = []
    image: GeneratedImage | None = None
    for part in (candidate.get("content") or {}).get("parts") or []:
        if part.get("thought"):
            continue
        if "text" in part:
            texts.append(str(part["text"]))
        inline = part.get("inlineData")
        if inline and image is None:
            image = GeneratedImage(
                payload=base64.b64decode(inline.get("data", "")),
                content_type=str(inline.get("mimeType", "application/octet-stream")),
            )
    return GenerationResult(
        text="".join(texts) if texts else None,
        image=image,
        finish_reason=candidate.get("finishReason"),
        block_reason=block_reason,
    )


def response_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Le schéma JSON à imposer au modèle, dérivé d'un modèle Pydantic.

    Pydantic produit des `$ref` vers `$defs`, des `title`, des `default` et
    des `format` ; Gemini veut un schéma autonome et sobre. On déréférence,
    on retire le bruit, et on retire les champs `id` : ils sont attribués
    par nous à la validation, le modèle n'a pas à les inventer.
    """
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    return cast(dict[str, Any], _clean(schema, definitions))


def _clean(node: Any, definitions: Mapping[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return _clean(definitions[name], definitions)
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in _SCHEMA_NOISE:
                continue
            if key == "properties" and isinstance(value, dict):
                cleaned[key] = {
                    name: _clean(prop, definitions) for name, prop in value.items() if name != "id"
                }
            elif key == "required" and isinstance(value, list):
                cleaned[key] = [name for name in value if name != "id"]
            else:
                cleaned[key] = _clean(value, definitions)
        return cleaned
    if isinstance(node, list):
        return [_clean(item, definitions) for item in node]
    return node
