"""Ports du module `documents` : ce dont les cas d'usage ont besoin, sans dire qui le fournit.

Contrat
-------
- Les dépôts parlent en objets du domaine (`domain/templates.py`).
- `CvSource` est branché dans `bootstrap/wiring.py` sur le module `cv`
  (règle 4) : `documents` lit le CV sans connaître ses tables.
- `DocumentEngine` réunit le travail lourd (analyse d'un PDF, rendu PDF par
  Gotenberg, DOCX, mesure de fidélité, aperçus, lecture « comme un ATS ») ;
  un test le remplace par une doublure en mémoire.
- `TemplateJobs` diffère l'ingestion, l'explication et l'envoi dans la file
  durable : jamais de travail lourd sur le chemin d'une requête qui n'en a
  pas besoin.
- `AccountDirectory` (l'adresse du compte, branchée sur `identity`),
  `DocumentMailer` (le centre d'e-mails du socle, avec pièces jointes) et
  `LogoFetcher` (le logo d'une entreprise, depuis notre stockage public)
  servent la lettre et l'envoi (ADR-0030, ADR-0031).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from karria.modules.documents.domain.ats import ExtractedDocument
from karria.modules.documents.domain.gate import Fidelity
from karria.modules.documents.domain.sends import DocumentSend
from karria.modules.documents.domain.templates import (
    DocumentKind,
    ExplanationStatus,
    Fit,
    GeneratedCv,
    Ownership,
    Selection,
    Template,
    TemplateVersion,
)


@dataclass(frozen=True, slots=True)
class CvSnapshot:
    """Le CV d'une personne, tel que le module `cv` l'expose (forme de `GET /cv/me`)."""

    data: dict[str, Any]
    image_key: str | None
    updated_at: datetime


@dataclass(slots=True)
class Analysis:
    """Le résultat de l'analyse d'un PDF de modèle."""

    spec: dict[str, Any]
    sample: dict[str, Any]
    labels: dict[str, Any]
    assets: dict[str, bytes]
    fonts: list[dict[str, Any]]
    fonts_report: list[dict[str, Any]]
    unlabeled: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RenderedPdf:
    """Un PDF rendu, et ce que la mise en page a dû faire."""

    data: bytes
    fit: Fit


@dataclass(frozen=True, slots=True)
class RenderedHtml:
    """Le HTML de l'atelier et son nombre de pages estimé (ADR-0029)."""

    html: str
    pages: int


@dataclass(frozen=True, slots=True)
class DocumentFile:
    """Une pièce jointe : son nom, son type et ses octets."""

    file_name: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Previews:
    """Les aperçus d'une page : pleine page PNG, aperçu PNG, vignette WebP."""

    full_png: bytes
    preview_png: bytes
    thumbnail_webp: bytes
    #: BlurHash de la vignette, affiché par l'app pendant son chargement.
    blurhash: str | None = None


class TemplateRepository(Protocol):
    """Les modèles et leurs versions."""

    async def list_published(
        self, *, category: str | None, premium: bool | None
    ) -> list[Template]: ...

    async def list_all(self) -> list[Template]: ...

    async def get(self, template_id: UUID) -> Template | None: ...

    async def get_by_name(self, name: str) -> Template | None: ...

    async def save(self, template: Template) -> None: ...

    async def next_version_number(self, template_id: UUID) -> int: ...

    async def save_version(self, version: TemplateVersion) -> None: ...

    async def get_version(self, version_id: UUID) -> TemplateVersion | None: ...

    async def versions_of(self, template_id: UUID) -> list[TemplateVersion]: ...

    async def increment_views(self, template_id: UUID) -> None: ...

    async def increment_uses(self, template_id: UUID) -> None: ...


class UserTemplateRepository(Protocol):
    """Ce que chaque personne possède et a choisi."""

    async def owned(self, user_id: UUID) -> list[Ownership]: ...

    async def owns(self, user_id: UUID, template_id: UUID) -> bool: ...

    async def add(self, ownership: Ownership) -> Ownership: ...

    async def selection(self, user_id: UUID) -> Selection | None: ...

    async def save_selection(self, selection: Selection) -> None: ...

    async def forget(self, user_id: UUID) -> None: ...


class GeneratedCvRepository(Protocol):
    """Les exports."""

    async def add(self, generated: GeneratedCv) -> None: ...

    async def get(self, generated_id: UUID) -> GeneratedCv | None: ...

    async def history(
        self,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
        kind: DocumentKind | None = None,
        dossier_id: UUID | None = None,
    ) -> tuple[list[GeneratedCv], int]: ...

    async def delete(self, generated_id: UUID) -> None: ...

    async def set_explanation(
        self, generated_id: UUID, explanation: dict[str, Any] | None, status: ExplanationStatus
    ) -> None: ...

    async def of_user(self, user_id: UUID) -> list[GeneratedCv]: ...

    async def forget(self, user_id: UUID) -> None: ...


class CvSource(Protocol):
    """Le CV d'une personne (module `cv`), ou `None` s'il n'est pas finalisé."""

    async def snapshot(self, user_id: UUID) -> CvSnapshot | None: ...


class SendRepository(Protocol):
    """Le journal des envois par e-mail (ADR-0031)."""

    async def add(self, send: DocumentSend) -> None: ...

    async def get(self, send_id: UUID) -> DocumentSend | None: ...

    async def save(self, send: DocumentSend) -> None: ...

    async def lock_account(self, user_id: UUID) -> None:
        """Sérialise les demandes d'un compte le temps de la transaction (plafond atomique)."""
        ...

    async def count_between(self, user_id: UUID, start: datetime, end: datetime) -> int:
        """Les envois demandés par un compte sur la période, échecs non comptés."""
        ...

    async def count_to_between(
        self, recipient_key: str, start: datetime, end: datetime, *, user_id: UUID | None = None
    ) -> int:
        """Les envois vers une clé canonique de destinataire ; d'un compte, ou de tous."""
        ...

    async def forget(self, user_id: UUID) -> None: ...


class OriginThrottle(Protocol):
    """Port : compte les envois par origine (adresse IP) ; rend les secondes à attendre, ou `None`."""

    def hit(self, key: str) -> int | None: ...


class AccountDirectory(Protocol):
    """Port : l'adresse d'un compte, pour `to: null`. Branché sur `identity` par `bootstrap`."""

    async def email_of(self, user_id: UUID) -> str | None: ...


class DocumentMailer(Protocol):
    """Port : remettre des documents en pièces jointes, signés du nom de la personne."""

    async def send(self, *, to: str, sender_name: str, documents: list[DocumentFile]) -> None: ...


class LogoFetcher(Protocol):
    """Port : le logo d'une entreprise (octets PNG/WebP), ou `None` s'il est injoignable."""

    async def fetch(self, url: str) -> bytes | None: ...


class DocumentEngine(Protocol):
    """Analyse, rendus, mesures : le travail lourd, derrière un seul port."""

    async def analyze(self, pdf: bytes) -> Analysis: ...

    async def analyze_letter(self, pdf: bytes, *, accent: dict[str, Any] | None = None) -> Analysis:
        """La lettre appariée d'un modèle → sa spécification de lettre (ADR-0036)."""
        ...

    async def render_pdf(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo: bytes | None,
    ) -> RenderedPdf: ...

    async def render_html(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo_url: str | None,
        anchored: bool,
    ) -> RenderedHtml: ...

    async def render_docx(
        self,
        spec: dict[str, Any],
        view: dict[str, Any],
        *,
        assets: dict[str, bytes],
        photo: bytes | None,
        fit: Fit,
    ) -> bytes: ...

    async def docx_to_pdf(self, docx: bytes) -> bytes: ...

    async def measure(
        self,
        source_pdf: bytes,
        rendered_pdf: bytes,
        *,
        fonts_report: list[dict[str, Any]] | None = None,
    ) -> Fidelity:
        """`fonts_report` : les substitutions déclarées, pour juger la police servie (ADR-0036)."""
        ...

    async def previews(self, pdf: bytes) -> Previews: ...

    async def page_images(self, pdf: bytes) -> list[tuple[bytes, int, int]]: ...

    def read(self, data: bytes, fmt: str) -> ExtractedDocument: ...


class AtsExplainer(Protocol):
    """Rédige l'explication d'un score ATS déjà calculé."""

    async def explain(self, report: dict[str, Any]) -> dict[str, Any] | None: ...


class TemplateJobs(Protocol):
    """La file durable du module."""

    async def defer_ingestion(self, version_id: UUID) -> None: ...

    async def defer_explanation(self, generated_id: UUID) -> None: ...

    async def defer_send(self, send_id: UUID) -> None: ...
