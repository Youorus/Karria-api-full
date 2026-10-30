"""Le consentement à l'IA : ce qu'on accepte avant qu'une donnée parte chez un modèle tiers.

Pourquoi un second consentement, distinct des conditions
--------------------------------------------------------
« En continuant » couvre les conditions et la politique de confidentialité.
Apple (règle 5.1.2 i) exige, **avant la première utilisation** d'une
fonctionnalité qui envoie des données à un tiers, une information claire
sur ce qui part, chez qui, pourquoi — et un accord explicite. Un oui
glissé dans les conditions ne le prouve pas ; une version horodatée par
usage, si.

Pourquoi la version se **calcule** au lieu de se décréter
---------------------------------------------------------
La liste des fournisseurs vient du routage réel des modèles
(`LLM__TASKS__*`, `LLM__FALLBACKS__*`), pas d'un texte figé : si demain
une tâche passe de Gemini à Anthropic, le consentement doit se redemander
— et il se redemande seul, parce que la version porte l'empreinte des
fournisseurs et des usages. Une révision de la formulation se signale en
changeant `AI_DISCLOSURE_REVISION`.

Ce que ce fichier décide, et rien d'autre
-----------------------------------------
Quels usages existent (les outils d'IA de l'app), quelles tâches du
routage servent chaque usage, comment une version se forme, et quand un
consentement **vaut** : donné pour la version en cours, et non retiré.
D'où viennent les fournisseurs — la configuration — est un détail de
l'application (port `AiProviderCatalog`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

#: Révision de la **formulation** de l'information donnée à la personne.
#: À changer quand ce qu'on lui dit change (finalités, conservation) ; un
#: changement de fournisseur se reflète seul dans la version.
AI_DISCLOSURE_REVISION: Final = "2026-10"


class AiPurpose(StrEnum):
    """Les outils d'IA de l'app, tels que la personne les reconnaît."""

    #: Parler à Karria : la conversation, la recherche et sa mémoire.
    ASSISTANT = "assistant"
    #: Dicter au lieu d'écrire.
    TRANSCRIPTION = "transcription"
    #: Le CV en conversation : interprétation des réponses, chapitre par chapitre.
    CV_CREATION = "cv_creation"
    #: L'import d'un CV existant : extraction des chapitres.
    CV_IMPORT = "cv_import"
    #: L'amélioration de la photo du CV.
    PHOTO_ENHANCEMENT = "photo_enhancement"
    #: L'explication du score ATS d'un export.
    TEMPLATE_ANALYSIS = "template_analysis"


#: Les tâches du routage (`LLM__TASKS__<TACHE>`) qui servent chaque usage.
#: Ce qui n'y figure pas (`enrich`, `template_extract`) traite des offres ou
#: des modèles déposés par l'équipe — jamais une donnée de la personne.
PURPOSE_TASKS: Final[Mapping[AiPurpose, tuple[str, ...]]] = {
    AiPurpose.ASSISTANT: ("search_dialogue", "memory"),
    AiPurpose.TRANSCRIPTION: ("transcribe",),
    AiPurpose.CV_CREATION: ("cv_interpret",),
    AiPurpose.CV_IMPORT: ("cv_extract",),
    AiPurpose.PHOTO_ENHANCEMENT: ("portrait",),
    AiPurpose.TEMPLATE_ANALYSIS: ("ats_explain",),
}

#: Le nom lisible de chaque fournisseur du routage (`shared/llm/ports.py::Provider`).
PROVIDER_NAMES: Final[Mapping[str, str]] = {
    "gemini": "Google Gemini",
    "anthropic": "Anthropic Claude",
    "openai": "OpenAI",
    "compatible": "un fournisseur compatible OpenAI",
}


def user_data_tasks() -> tuple[str, ...]:
    """Toutes les tâches du routage qui reçoivent des données de la personne."""
    return tuple(dict.fromkeys(task for tasks in PURPOSE_TASKS.values() for task in tasks))


def provider_name(provider: str) -> str:
    """Le nom à afficher ; l'identifiant lui-même si le fournisseur est inconnu."""
    return PROVIDER_NAMES.get(provider, provider)


@dataclass(frozen=True, slots=True)
class AiDisclosure:
    """Ce que la personne doit accepter **aujourd'hui** : la version, les fournisseurs, les usages."""

    version: str
    providers: tuple[str, ...]
    purposes: tuple[AiPurpose, ...]

    @classmethod
    def build(cls, providers: Iterable[str]) -> AiDisclosure:
        """Compose l'information courante à partir des fournisseurs effectivement routés.

        La version est `<révision>.<empreinte>` : l'empreinte change dès
        qu'un fournisseur ou un usage entre ou sort, et le consentement
        précédent cesse de valoir — c'est voulu.
        """
        unique = tuple(sorted(dict.fromkeys(p.strip().lower() for p in providers if p.strip())))
        purposes = tuple(AiPurpose)
        digest = hashlib.sha256(f"{'|'.join(unique)}#{'|'.join(purposes)}".encode()).hexdigest()[:8]
        return cls(
            version=f"{AI_DISCLOSURE_REVISION}.{digest}", providers=unique, purposes=purposes
        )

    @property
    def provider_names(self) -> tuple[str, ...]:
        """Les fournisseurs, lisibles, dans le même ordre que `providers`."""
        return tuple(provider_name(p) for p in self.providers)


@dataclass(frozen=True, slots=True)
class AiConsent:
    """Un accord donné par une personne, pour une version précise de l'information.

    `revoked_at` garde la trace du retrait au lieu d'effacer la ligne : la
    personne a consenti un jour, puis s'est ravisée, et les deux dates font
    partie de la preuve. Un nouvel accord **remplace** la ligne (une seule
    par compte) ; la suppression du compte l'emporte (ADR-0015).
    """

    user_id: UUID
    version: str
    providers: tuple[str, ...]
    purposes: tuple[AiPurpose, ...]
    accepted_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def given(cls, user_id: UUID, disclosure: AiDisclosure, *, at: datetime) -> AiConsent:
        """L'accord pour l'information courante, horodaté par le serveur."""
        return cls(
            user_id=user_id,
            version=disclosure.version,
            providers=disclosure.providers,
            purposes=disclosure.purposes,
            accepted_at=at,
        )

    @property
    def active(self) -> bool:
        """Donné et non retiré."""
        return self.revoked_at is None

    def covers(self, disclosure: AiDisclosure) -> bool:
        """Cet accord vaut-il pour l'information d'aujourd'hui ?"""
        return self.active and self.version == disclosure.version

    def revoked(self, *, at: datetime) -> AiConsent:
        """Le retrait. Idempotent : un second retrait garde la première date."""
        if self.revoked_at is not None:
            return self
        return replace(self, revoked_at=at)


@dataclass(frozen=True, slots=True)
class AiConsentStatus:
    """Où en est la personne vis-à-vis de l'information courante."""

    disclosure: AiDisclosure
    consent: AiConsent | None

    @property
    def granted(self) -> bool:
        """Vrai si un outil d'IA peut servir sans redemander."""
        return self.consent is not None and self.consent.covers(self.disclosure)

    @property
    def required(self) -> bool:
        """Vrai si l'app doit (re)montrer l'information avant le premier usage."""
        return not self.granted
