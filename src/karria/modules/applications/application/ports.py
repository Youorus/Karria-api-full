"""Ports du module `applications` : ce dont les cas d'usage ont besoin, sans dire qui le rend.

Quatre familles :

- **Persistance** — `DossierRepository`, qui rend des objets du domaine,
  jamais un modèle Tortoise (AGENTS.md §6.10).
- **Les autres modules** (règle 4, branchés dans `bootstrap/wiring.py`) :
  `OfferReader` (la fiche d'une offre, chez `offers`), `BaseCvReader` (le
  CV de base, chez `cv`), `CvValidator` (la forme d'un CV, chez `cv`),
  `TemplateChecker` (le modèle est-il utilisable, chez `documents`). Leur
  implémentation par défaut **refuse** : un branchement oublié se voit.
- **Intelligence artificielle** — `CvAdapter` et `LetterWriter`, deux
  ports pour deux besoins ; ce qu'ils rendent est une donnée que le
  domaine revalide (ADR-0013).
- **La file** — `DossierJobs`, pour différer l'adaptation quand elle
  dépasse le temps d'une requête.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from karria.modules.applications.domain import AdaptationOutput, Dossier, Letter, OfferSnapshot

# ---------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------


class DossierRepository(Protocol):
    """Les dossiers d'une personne."""

    async def find(self, dossier_id: UUID, *, user_id: UUID) -> Dossier | None:
        """Le dossier s'il existe **et** appartient à cette personne."""
        ...

    async def find_any(self, dossier_id: UUID) -> Dossier | None:
        """Le dossier, quel qu'en soit le propriétaire — pour le worker."""
        ...

    async def find_by_offer(self, user_id: UUID, offer_id: UUID) -> Dossier | None:
        """Le dossier de cette personne pour cette offre, s'il existe."""
        ...

    async def list_by_user(self, user_id: UUID, *, applied_only: bool) -> list[Dossier]:
        """Les dossiers, du plus récemment modifié au plus ancien."""
        ...

    async def insert(self, dossier: Dossier) -> None:
        """Crée le dossier. Jamais un remplacement : un dossier supprimé ne renaît pas."""
        ...

    async def save(self, dossier: Dossier) -> None:
        """Remplace le dossier existant.

        Raises:
            DossierNotFoundError: Le dossier a été supprimé entre-temps — rien n'est écrit.
        """
        ...

    async def save_if_unchanged(self, dossier: Dossier, *, expected_updated_at: datetime) -> bool:
        """Remplace seulement si `updated_at` en base vaut encore `expected_updated_at`.

        Le contrôle optimiste autour des appels au modèle : vrai si écrit,
        faux si le dossier a changé ou disparu pendant l'appel — l'appelant
        relit et rejoue, il n'écrase jamais.
        """
        ...

    async def delete(self, dossier_id: UUID) -> None:
        """Supprime le dossier. Idempotent."""
        ...

    async def delete_by_user(self, user_id: UUID) -> int:
        """Efface tous les dossiers d'une personne ; rend leur nombre."""
        ...


# ---------------------------------------------------------------------
# Les autres modules
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OfferCard:
    """Ce qu'`applications` a besoin de savoir d'une offre, dans ses propres termes."""

    offer_id: UUID
    title: str
    description: str
    company: str | None = None
    logo_hash: str | None = None
    website: str | None = None
    city: str | None = None
    contract: str | None = None
    contract_label: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None


class OfferReader(Protocol):
    """La fiche d'une offre, chez le module `offers`."""

    async def read(self, offer_id: UUID) -> OfferCard | None:
        """L'offre, même inactive, ou `None` si l'index ne la connaît pas."""
        ...


@dataclass(frozen=True, slots=True)
class BaseCv:
    """Le CV de base d'une personne, au format de `GET /cv/me`, sans la photo."""

    data: dict[str, Any]
    updated_at: datetime


class BaseCvReader(Protocol):
    """Le CV de base, chez le module `cv`."""

    async def read(self, user_id: UUID) -> BaseCv | None:
        """Le CV, ou `None` s'il est encore vide."""
        ...


class CvValidator(Protocol):
    """La forme d'un CV, jugée par le module `cv` — une seule définition du CV."""

    def validate(self, cv: Mapping[str, Any]) -> dict[str, Any]:
        """Le CV normalisé.

        Raises:
            CvInvalidError: Le CV ne respecte pas la forme.
        """
        ...


class TemplateChecker(Protocol):
    """Le modèle de CV est-il utilisable par cette personne (gratuit ou possédé) ?"""

    async def usable(self, user_id: UUID, template_id: UUID) -> bool:
        """Vrai si la personne peut rendre son CV dans ce modèle."""
        ...


#: (condensat du logo) → URL publique stable, fournie par `offers`.
class LogoUrl(Protocol):
    """L'adresse publique d'un logo chez Karria, calculée sans réseau."""

    def __call__(self, logo_hash: str) -> str:
        """L'URL du logo en 128 px."""
        ...


# ---------------------------------------------------------------------
# Intelligence artificielle
# ---------------------------------------------------------------------


class CvAdapter(Protocol):
    """Propose comment adapter un CV à une offre — sans rien inventer."""

    async def adapt(self, offer: OfferSnapshot, cv: Mapping[str, Any]) -> AdaptationOutput:
        """La sortie structurée du modèle, non encore convertie en propositions.

        Raises:
            ModelUnavailableError: Le modèle est injoignable ou refuse.
            ModelOutputInvalidError: La réponse ne respecte pas le schéma imposé.
        """
        ...


class LetterWriter(Protocol):
    """Écrit une lettre de motivation, ou en réécrit un paragraphe."""

    async def draft(self, offer: OfferSnapshot, cv: Mapping[str, Any]) -> Letter:
        """La première ébauche, revalidée par le domaine.

        Raises:
            ModelUnavailableError: Le modèle est injoignable ou refuse.
            ModelOutputInvalidError: La réponse ne respecte pas le schéma imposé.
        """
        ...

    async def rewrite_paragraph(
        self,
        offer: OfferSnapshot,
        cv: Mapping[str, Any],
        letter: Letter,
        index: int,
        instruction: str | None,
    ) -> str:
        """Le texte du paragraphe `index`, réécrit ; `instruction` est une donnée, pas un ordre.

        Raises:
            ModelUnavailableError: Le modèle est injoignable ou refuse.
            ModelOutputInvalidError: La réponse ne respecte pas le schéma imposé.
        """
        ...


# ---------------------------------------------------------------------
# La file
# ---------------------------------------------------------------------


class DossierJobs(Protocol):
    """Différer l'adaptation d'un dossier sur la file durable."""

    async def defer_adaptation(self, dossier_id: UUID) -> None:
        """Une adaptation par dossier à la fois."""
        ...


__all__ = [
    "BaseCv",
    "BaseCvReader",
    "CvAdapter",
    "CvValidator",
    "DossierJobs",
    "DossierRepository",
    "LetterWriter",
    "LogoUrl",
    "OfferCard",
    "OfferReader",
    "TemplateChecker",
]
