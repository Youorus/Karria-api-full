"""Ports du module `cv` : ce dont les cas d'usage ont besoin, sans dire qui le rend.

Deux familles :

- **Persistance** — brouillons, CV, photos en attente. Les dépôts rendent
  des objets du domaine, jamais un modèle Tortoise (AGENTS.md §6.10).
- **Intelligence artificielle** — interpréter une réponse, transcrire une
  voix, améliorer un portrait, lire un CV existant. Quatre ports distincts
  pour quatre besoins, même si un seul fournisseur les sert aujourd'hui :
  remplacer l'un n'oblige pas à toucher les autres, et chaque doublure de
  test tient en quelques lignes.

Ce que les ports d'IA **rendent** est une donnée brute, pas une décision :
le cas d'usage la valide contre le schéma du chapitre avant d'en faire
quoi que ce soit (ADR-0013).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from karria.modules.cv.domain import (
    AudioInfo,
    Chapter,
    CurriculumVitae,
    CvPhoto,
    DocumentInfo,
    Draft,
)

# ---------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------


class DraftRepository(Protocol):
    """Le brouillon d'une personne — un seul à la fois."""

    async def find_by_user(self, user_id: UUID) -> Draft | None:
        """Le brouillon en cours, ou `None`."""
        ...

    async def save(self, draft: Draft) -> None:
        """Crée ou remplace le brouillon."""
        ...

    async def delete(self, draft_id: UUID) -> None:
        """Supprime le brouillon. Idempotent."""
        ...


class CvRepository(Protocol):
    """Le CV d'une personne — un seul."""

    async def find_by_user(self, user_id: UUID) -> CurriculumVitae | None:
        """Le CV, ou `None` si la personne n'en a pas encore."""
        ...

    async def save(self, cv: CurriculumVitae) -> None:
        """Crée ou remplace le CV."""
        ...

    async def delete_by_user(self, user_id: UUID) -> None:
        """Efface le CV de la personne. Idempotent."""
        ...


class PhotoRepository(Protocol):
    """Les photos envoyées, en attente d'être retenues."""

    async def add(self, photo: CvPhoto) -> None:
        """Enregistre une photo écrite dans le bucket."""
        ...

    async def find(self, photo_id: UUID, *, user_id: UUID) -> CvPhoto | None:
        """La photo si elle existe **et** appartient à cette personne."""
        ...

    async def list_by_user(self, user_id: UUID) -> list[CvPhoto]:
        """Toutes les photos en attente de la personne."""
        ...

    async def delete(self, photo_ids: Sequence[UUID]) -> None:
        """Oublie des photos. Idempotent."""
        ...


# ---------------------------------------------------------------------
# Fichiers reçus
# ---------------------------------------------------------------------


class UploadInspector(Protocol):
    """Lit ce qu'un fichier reçu est vraiment, avant qu'une règle le juge.

    Inspecter — compter les pages d'un PDF, mesurer la durée d'un M4A,
    extraire le texte d'un DOCX — demande de parser des formats de fichier :
    c'est de l'infrastructure. Le juger est une règle du domaine
    (`domain/uploads.py`). Le port sépare les deux.
    """

    def document(self, payload: bytes, content_type: str) -> DocumentInfo:
        """Inspecte un document de CV.

        Raises:
            UnsupportedUploadError: Le fichier ne se lit pas comme son type l'annonce.
        """
        ...

    def audio(self, payload: bytes, content_type: str) -> AudioInfo:
        """Inspecte un enregistrement audio. Ne lève jamais : la durée peut rester inconnue."""
        ...


# ---------------------------------------------------------------------
# Intelligence artificielle
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interpretation:
    """Ce que l'interprète tire d'une réponse. `proposal` n'est **pas** encore validé."""

    proposal: dict[str, Any] | None
    missing: tuple[str, ...]
    follow_up: str | None
    off_topic: bool


class ChapterInterpreter(Protocol):
    """Transforme la réponse libre d'une personne en premier jet structuré."""

    async def interpret(
        self, chapter: Chapter, answer: str, correction_of: dict[str, Any] | None
    ) -> Interpretation:
        """Interprète `answer` pour `chapter`.

        Args:
            correction_of: Le jet précédent quand la personne corrige ;
                la réponse dit alors ce qui ne va pas.

        Raises:
            ModelUnavailableError: Le modèle est injoignable ou refuse.
            ModelOutputInvalidError: La réponse ne respecte pas le schéma imposé.
        """
        ...


class AudioTranscriber(Protocol):
    """La voix devient du texte."""

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        """Transcrit en français. Rend une chaîne, éventuellement vide.

        Raises:
            ModelUnavailableError: Le modèle est injoignable ou refuse.
            ModelOutputInvalidError: La réponse ne respecte pas le schéma imposé.
        """
        ...


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """Une image produite par le générateur, telle que reçue."""

    payload: bytes
    content_type: str


class PortraitEnhancer(Protocol):
    """Fait d'une photo un portrait professionnel, la personne restant elle-même."""

    async def enhance(self, image: bytes, content_type: str) -> GeneratedImage:
        """Produit le portrait.

        Raises:
            PhotoEnhanceRefusedError: Le générateur refuse cette image.
            PhotoEnhanceUnavailableError: Panne, ou rien d'exploitable rendu.
        """
        ...


class CvDocumentExtractor(Protocol):
    """Lit un CV existant (PDF, image, texte) et en tire les chapitres."""

    async def extract(self, document: bytes, content_type: str) -> dict[Chapter, Any]:
        """Rend, par chapitre, une valeur (chapitre simple) ou une liste (boucle).

        Brut, non validé : le cas d'usage revalide élément par élément.

        Raises:
            ModelUnavailableError: Le modèle est injoignable ou refuse.
            ModelOutputInvalidError: La réponse ne respecte pas le schéma imposé.
        """
        ...
