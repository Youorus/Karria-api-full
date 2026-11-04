"""L'adaptation du CV à une offre : de la sortie du modèle aux propositions.

Contrat
-------
Le modèle rend une **sortie structurée** (`AdaptationOutput`) : un intitulé,
un « à propos », des reformulations de postes et de missions, un ordre pour
les compétences et pour les expériences — chacun avec sa raison courte. Ce
n'est qu'une donnée (AGENTS.md §6.14) : `propositions_from` la transforme en
propositions **sur des chemins qui existent**, et écarte :

- ce qui ne change rien (`after == before`) ;
- un index qui ne désigne aucun élément ;
- un ordre qui n'est pas une permutation des éléments présents ;
- une reformulation qui **introduit un chiffre** absent du CV de base — la
  garde mécanique de « Karria n'invente jamais un fait » : un résultat
  chiffré que la personne n'a pas donné n'entre pas, quoi qu'en dise le
  modèle. Ce que le texte affirme par ailleurs reste à sa décision : c'est
  pour cela que tout arrive sous forme de proposition.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from karria.modules.applications.domain.coverage import cv_text
from karria.modules.applications.domain.dossiers import REASON_MAX_LENGTH, Proposition

Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=REASON_MAX_LENGTH)]


class Rewrite(BaseModel):
    """Un texte réécrit, et pourquoi."""

    model_config = ConfigDict(extra="ignore")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
    reason: Reason = ""


class MissionRewrite(BaseModel):
    """Une mission reformulée, désignée par son index dans l'expérience."""

    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    reason: Reason = ""


class ExperienceRewrite(BaseModel):
    """Ce que le modèle propose sur une expérience, désignée par son index."""

    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    position: Rewrite | None = None
    missions: list[MissionRewrite] = Field(default_factory=list)


class Reorder(BaseModel):
    """Un nouvel ordre : les index d'origine, dans l'ordre voulu."""

    model_config = ConfigDict(extra="ignore")

    order: list[int]
    reason: Reason = ""


class AdaptationOutput(BaseModel):
    """La forme imposée à la sortie du modèle pour l'adaptation."""

    model_config = ConfigDict(extra="ignore")

    headline: Rewrite | None = Field(
        default=None,
        description="L'intitulé visé, formulé comme dans l'offre ; null si rien à changer.",
    )
    summary: Rewrite | None = Field(
        default=None, description="Le « à propos » réécrit, mêmes faits ; null si rien à changer."
    )
    experiences: list[ExperienceRewrite] = Field(default_factory=list)
    skills_order: Reorder | None = Field(
        default=None,
        description="Les compétences les plus pertinentes d'abord ; null si l'ordre convient.",
    )
    experiences_order: Reorder | None = Field(
        default=None,
        description="Les expériences les plus pertinentes d'abord ; null si l'ordre convient.",
    )


_FIGURE: Final = re.compile(r"\d+(?:[.,]\d+)?")


def invented_figures(text: str, reference: str) -> tuple[str, ...]:
    """Les nombres de `text` qu'on ne lit nulle part dans `reference`."""
    known = set(_FIGURE.findall(reference))
    return tuple(dict.fromkeys(f for f in _FIGURE.findall(text) if f not in known))


def _is_permutation(order: Sequence[int], size: int) -> bool:
    return size > 0 and sorted(order) == list(range(size)) and list(order) != list(range(size))


@dataclass(frozen=True, slots=True)
class Conversion:
    """Les propositions retenues, et ce qui a été écarté (pour les journaux)."""

    propositions: tuple[Proposition, ...]
    discarded: tuple[str, ...]


def propositions_from(output: AdaptationOutput, cv: Mapping[str, Any]) -> Conversion:
    """Transforme la sortie du modèle en propositions sur le CV, avec les gardes."""
    reference = cv_text(cv)
    kept: list[Proposition] = []
    discarded: list[str] = []

    def text_proposal(path: str, before: Any, rewrite: Rewrite) -> None:
        if rewrite.text == before:
            discarded.append(f"{path}:unchanged")
            return
        if invented_figures(rewrite.text, reference):
            discarded.append(f"{path}:invented_figure")
            return
        kept.append(Proposition(uuid4(), path, before, rewrite.text, rewrite.reason))

    identity = cv.get("identity")
    if output.headline is not None:
        if isinstance(identity, Mapping):
            text_proposal("identity.headline", identity.get("headline"), output.headline)
        else:
            discarded.append("identity.headline:no_identity")
    if output.summary is not None:
        text_proposal("summary", cv.get("summary"), output.summary)

    experiences = cv.get("experiences")
    experiences = experiences if isinstance(experiences, list) else []
    for rewrite in output.experiences:
        if rewrite.index >= len(experiences) or not isinstance(experiences[rewrite.index], Mapping):
            discarded.append(f"experiences.{rewrite.index}:no_such_experience")
            continue
        experience = experiences[rewrite.index]
        if rewrite.position is not None:
            text_proposal(
                f"experiences.{rewrite.index}.position",
                experience.get("position"),
                rewrite.position,
            )
        missions = experience.get("missions")
        missions = missions if isinstance(missions, list) else []
        for mission in rewrite.missions:
            path = f"experiences.{rewrite.index}.missions.{mission.index}"
            if mission.index >= len(missions):
                discarded.append(f"{path}:no_such_mission")
                continue
            text_proposal(
                path, missions[mission.index], Rewrite(text=mission.text, reason=mission.reason)
            )

    for name, reorder in (
        ("skills", output.skills_order),
        ("experiences", output.experiences_order),
    ):
        if reorder is None:
            continue
        items = cv.get(name)
        items = items if isinstance(items, list) else []
        if not _is_permutation(reorder.order, len(items)):
            discarded.append(f"{name}:not_a_permutation")
            continue
        kept.append(
            Proposition(uuid4(), name, items, [items[i] for i in reorder.order], reorder.reason)
        )
    return Conversion(tuple(kept), tuple(discarded))
