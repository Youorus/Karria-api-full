"""Le port vers un modèle de langage, indépendant du fournisseur.

Contrat
-------
Un appel = une `LlmRequest` (la tâche, la consigne de système, des parties
texte ou binaires, un schéma de sortie imposé) → une `LlmResult` (le texte
ou l'image rendus, le motif d'arrêt, les jetons consommés). Rien ici ne
connaît Gemini, Anthropic ni OpenAI : chaque fournisseur a son adaptateur
(`gemini.py`, `anthropic.py`, `openai.py`), et le routeur (`router.py`)
choisit l'adaptateur et le modèle **par tâche**, selon la configuration.

Ce que le port impose, quel que soit le fournisseur (ADR-0013, ADR-0018) :

- la règle voyage en consigne de système, la donnée dans les parties ;
- la sortie structurée a un **schéma imposé**, et reste une donnée que
  l'appelant revalide (`structured()` le fait avec Pydantic) ;
- un refus de contenu n'est pas une panne (`LlmResult.refused`) ;
- une panne de fournisseur est une `LlmUnavailableError`, une sortie hors
  forme une `LlmOutputInvalidError`, une capacité absente (de l'audio sur
  un fournisseur qui n'en lit pas) une `LlmCapabilityError` — toutes
  traduites par le module appelant dans ses propres erreurs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from karria.shared.logging import get_logger

logger = get_logger(__name__)


class LlmError(Exception):
    """Racine des erreurs du port."""


class LlmUnavailableError(LlmError):
    """Le fournisseur ne répond pas, refuse la requête, ou la configuration manque à l'appel."""

    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class LlmOutputInvalidError(LlmError):
    """La sortie du modèle n'a pas la forme imposée."""


class LlmCapabilityError(LlmError):
    """Le fournisseur routé ne sait pas faire ce que la requête demande (audio, image…)."""


class LlmConfigurationError(LlmError):
    """La configuration du routage est incohérente : détectée au démarrage, jamais à l'appel."""


class Provider(StrEnum):
    """Les fournisseurs connus du routeur."""

    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    #: Toute API compatible OpenAI (vLLM, Mistral, un modèle local).
    COMPATIBLE = "compatible"


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Un fournisseur et un modèle : `gemini:gemini-3.8-flash`."""

    provider: Provider
    model: str

    @classmethod
    def parse(cls, value: str) -> ModelRoute:
        """Lit `fournisseur:modèle`.

        Raises:
            LlmConfigurationError: Forme ou fournisseur inconnu.
        """
        provider, sep, model = value.strip().partition(":")
        if not sep or not model.strip():
            raise LlmConfigurationError(
                f"route « {value} » : la forme attendue est fournisseur:modèle"
            )
        try:
            known = Provider(provider.strip().lower())
        except ValueError as exc:
            raise LlmConfigurationError(
                f"route « {value} » : fournisseur inconnu « {provider} » "
                f"(connus : {', '.join(p.value for p in Provider)})"
            ) from exc
        return cls(known, model.strip())

    def __str__(self) -> str:
        return f"{self.provider.value}:{self.model}"


@dataclass(frozen=True, slots=True)
class Blob:
    """Un contenu binaire joint à la requête — audio, image, PDF — ou rendu par le modèle."""

    data: bytes
    content_type: str

    @property
    def kind(self) -> str:
        """`audio`, `image`, `application`… : la famille du type MIME."""
        return self.content_type.split("/", 1)[0]


Part = str | Blob


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """Tout ce qu'un appel peut porter, dans le vocabulaire du port.

    Attributes:
        task: La tâche (`search_dialogue`, `cv_interpret`…) : c'est elle que le
            routeur traduit en fournisseur et modèle.
        parts: Texte et contenus binaires du message courant, dans l'ordre.
        system: La consigne — la règle, séparée des données.
        history: Les tours précédents, `("user" | "assistant", texte)`.
        response_schema: Schéma JSON imposé à la sortie (texte seulement).
        temperature: Basse pour extraire, un peu plus haute pour converser.
        max_output_tokens: Plafond de sortie ; `None` : celui du fournisseur.
        thinking: Réflexion interne autorisée. Coupée par défaut : deux à
            trois fois plus rapide pour une extraction qui n'en a pas besoin.
        image_output: Le modèle doit rendre une image (portrait).
        aspect_ratio: Format de l'image rendue (`"3:4"`).
    """

    task: str
    parts: tuple[Part, ...]
    system: str | None = None
    history: tuple[tuple[str, str], ...] = ()
    response_schema: Mapping[str, Any] | None = None
    temperature: float = 0.2
    max_output_tokens: int | None = None
    thinking: bool = False
    image_output: bool = False
    aspect_ratio: str | None = None

    @property
    def blobs(self) -> tuple[Blob, ...]:
        """Les contenus binaires joints."""
        return tuple(part for part in self.parts if isinstance(part, Blob))


@dataclass(frozen=True, slots=True)
class LlmResult:
    """Ce que le modèle a rendu, décodé mais non interprété."""

    text: str | None
    image: Blob | None
    finish_reason: str | None
    refused: bool
    provider: Provider
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0

    def json(self) -> Any:
        """Le texte, décodé en JSON.

        Raises:
            LlmOutputInvalidError: Pas de texte, ou pas du JSON.
        """
        if self.text is None:
            raise LlmOutputInvalidError("réponse sans texte")
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            logger.warning("llm.output_not_json", model=self.model, excerpt=self.text[:200])
            raise LlmOutputInvalidError("réponse qui n'est pas du JSON") from exc


class LanguageModel(Protocol):
    """Port : le modèle de langage, tel que les modules le voient."""

    async def generate(self, request: LlmRequest) -> LlmResult:
        """Exécute un appel.

        Raises:
            LlmUnavailableError: Panne, quota, clé ou route absentes à l'appel.
            LlmCapabilityError: Le fournisseur routé ne lit pas ce contenu.
        """
        ...


class ProviderAdapter(Protocol):
    """Port interne au socle : un fournisseur, tous ses modèles."""

    @property
    def provider(self) -> Provider:
        """Qui il est."""
        ...

    async def generate(self, model: str, request: LlmRequest) -> LlmResult:
        """Un appel à ce modèle."""
        ...


async def structured[T: BaseModel](
    llm: LanguageModel, request: LlmRequest, shape: type[T]
) -> T | None:
    """Un appel à schéma imposé, revalidé par Pydantic. `None` si le modèle a refusé.

    La sortie reste une donnée (ADR-0013) : le schéma envoyé borne la forme,
    Pydantic vérifie le contenu, l'appelant décide quoi en faire.

    Raises:
        LlmOutputInvalidError: La sortie ne tient pas dans `shape`.
    """
    result = await llm.generate(request)
    if result.refused:
        return None
    try:
        return shape.model_validate(result.json())
    except ValidationError as exc:
        logger.warning(
            "llm.output_invalid", task=request.task, model=result.model, errors=exc.error_count()
        )
        raise LlmOutputInvalidError("sortie hors schéma") from exc


def text_parts(parts: Sequence[Part]) -> list[str]:
    """Les parties texte, dans l'ordre."""
    return [part for part in parts if isinstance(part, str)]
