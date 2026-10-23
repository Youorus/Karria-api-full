"""La mémoire que Karria garde de chaque personne : des faits stables, résumés, bornés.

Contrat
-------
Séparée de la **constitution** (qui est Karria : identique pour tous,
versionnée dans le code), la mémoire dit ce que **cette** personne a
confié : les métiers visés, sa mobilité, ses contraintes, ses préférences,
sa situation. Elle est écrite par le modèle après chaque tour, à travers
un schéma imposé et des bornes de taille, puis **fusionnée** ici par une
règle : rien n'y entre sans passer par la validation, rien n'y grossit sans
limite, et la personne peut la lire et l'effacer.

Le contexte d'un tour = constitution + résumé de la mémoire + courte
fenêtre récente ; jamais la conversation entière. Karria se souvient de la
personne sans que son identité dérive avec la longueur de l'échange.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from karria.modules.offers.domain.normalization import fold

#: Bornes d'une mémoire : des faits, pas un journal.
MEMORY_LIST_MAX: Final = 8
MEMORY_ITEM_MAX_LENGTH: Final = 120
MEMORY_SUMMARY_MAX_LENGTH: Final = 600
#: Messages récents relus par le modèle à chaque tour, en plus de la mémoire.
RECENT_WINDOW: Final = 8


class MemoryFacts(BaseModel):
    """Ce que le modèle propose de retenir : la forme imposée à sa sortie."""

    model_config = ConfigDict(extra="forbid")

    target_jobs: list[str] = Field(default_factory=list, description="Métiers ou postes visés.")
    locations: list[str] = Field(
        default_factory=list, description="Lieux et mobilité (ville, rayon, télétravail, permis…)."
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Contraintes fermes : horaires, contrat, salaire minimum, dates.",
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="Souhaits souples : ambiance, taille d'entreprise, valeurs.",
    )
    situation: list[str] = Field(
        default_factory=list,
        description="Situation : expérience, formation en cours, reconversion, disponibilité.",
    )
    summary: str = Field(
        default="", description="La personne en deux ou trois phrases, pour Karria."
    )

    @field_validator("target_jobs", "locations", "constraints", "preferences", "situation")
    @classmethod
    def _bounded(cls, values: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for value in values:
            cleaned = " ".join(value.split())[:MEMORY_ITEM_MAX_LENGTH]
            if cleaned and fold(cleaned) not in seen:
                seen[fold(cleaned)] = cleaned
        return list(seen.values())[:MEMORY_LIST_MAX]

    @field_validator("summary")
    @classmethod
    def _short(cls, value: str) -> str:
        return " ".join(value.split())[:MEMORY_SUMMARY_MAX_LENGTH]

    @property
    def is_empty(self) -> bool:
        """Rien de retenu."""
        return not any(
            (
                self.target_jobs,
                self.locations,
                self.constraints,
                self.preferences,
                self.situation,
                self.summary,
            )
        )

    def render(self) -> str:
        """Le texte donné au modèle à chaque tour : court, en données."""
        lines = [self.summary] if self.summary else []
        for label, values in (
            ("Métiers visés", self.target_jobs),
            ("Lieux et mobilité", self.locations),
            ("Contraintes", self.constraints),
            ("Préférences", self.preferences),
            ("Situation", self.situation),
        ):
            if values:
                lines.append(f"{label} : " + " ; ".join(values))
        return "\n".join(lines)


def merge_memory(current: MemoryFacts, proposed: MemoryFacts) -> MemoryFacts:
    """Les faits nouveaux rejoignent les connus ; les plus récents restent quand la liste déborde."""

    def merged(old: list[str], new: list[str]) -> list[str]:
        kept = [v for v in old if fold(v) not in {fold(n) for n in new}]
        return (kept + new)[-MEMORY_LIST_MAX:]

    return MemoryFacts(
        target_jobs=merged(current.target_jobs, proposed.target_jobs),
        locations=merged(current.locations, proposed.locations),
        constraints=merged(current.constraints, proposed.constraints),
        preferences=merged(current.preferences, proposed.preferences),
        situation=merged(current.situation, proposed.situation),
        summary=proposed.summary or current.summary,
    )


class KarriaMemory(BaseModel):
    """La mémoire d'une personne, telle qu'elle est gardée et montrée."""

    model_config = ConfigDict(extra="forbid")

    facts: MemoryFacts = Field(default_factory=MemoryFacts)
    #: La version de la constitution sous laquelle elle a été écrite en dernier.
    constitution_version: str = ""
    updated_at: datetime | None = None
