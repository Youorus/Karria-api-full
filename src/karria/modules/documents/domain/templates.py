"""Les modèles de CV, leurs versions, ce qu'une personne en possède, choisit et exporte.

Contrat
-------
- Un **modèle** (`Template`) est la fiche de galerie ; il pointe sa version
  **publiée**. Une **version** (`TemplateVersion`) est immuable une fois
  analysée : sa spécification, ses mesures, ses aperçus et ses polices ne
  changent plus (ADR-0020 § 1, § 5). Un export garde la version qui l'a produit.
- Une version n'est publiée que si la porte de qualité passe
  (`domain/gate.py`) ; sinon elle reste `review`, visible des seuls
  administrateurs avec ses mesures.
- Un modèle gratuit se rend pour tout le monde ; un modèle Pro seulement pour
  qui le possède (`TemplateLockedError`).
- Ce qui se lit dans la spécification pour la galerie (couleur principale,
  famille principale, profil ATS du dessin) est **mesuré**, pas déclaré.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from karria.modules.documents.domain.errors import InvalidFormatError, TemplateLockedError

FORMATS: Final = ("pdf", "docx")
#: Interlettrage au-delà duquel un intitulé du dessin se lit lettre à lettre (em).
SPACED_TITLE_EM: Final = 0.1


class VersionStatus(StrEnum):
    """Où en est une version de modèle."""

    PROCESSING = "processing"
    PUBLISHED = "published"
    REVIEW = "review"
    FAILED = "failed"


class ExplanationStatus(StrEnum):
    """Où en est l'explication d'un score ATS."""

    PENDING = "pending"
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Template:
    """La fiche d'un modèle (galerie)."""

    id: UUID
    name: str
    label: str
    category: str
    description: str
    tags: tuple[str, ...]
    is_premium: bool
    price: Decimal | None
    primary_color: str | None
    font_family: str | None
    has_photo: bool
    columns: int
    published_version_id: UUID | None
    published_version: int | None
    created_at: datetime
    updated_at: datetime
    views_count: int = 0
    uses_count: int = 0
    rating_avg: float = 0.0
    rating_count: int = 0

    @property
    def is_published(self) -> bool:
        """Une version a passé la porte de qualité."""
        return self.published_version_id is not None


@dataclass(frozen=True, slots=True)
class TemplateVersion:
    """Une version analysée d'un modèle : la vérité du rendu."""

    id: UUID
    template_id: UUID
    version: int
    status: VersionStatus
    source_key: str
    created_at: datetime
    spec: dict[str, Any] | None = None
    sample: dict[str, Any] | None = None
    labels: dict[str, Any] | None = None
    report: dict[str, Any] = field(default_factory=dict)
    assets: dict[str, str] = field(default_factory=dict)
    fonts: tuple[dict[str, Any], ...] = ()
    thumbnail_key: str | None = None
    thumbnail_blurhash: str | None = None
    preview_key: str | None = None
    full_key: str | None = None
    error: str | None = None
    published_at: datetime | None = None

    def with_status(self, status: VersionStatus, **changes: Any) -> TemplateVersion:
        """La même version, à un autre état."""
        return replace(self, status=status, **changes)


@dataclass(frozen=True, slots=True)
class Ownership:
    """Un modèle que la personne possède (gratuit ajouté, ou acheté)."""

    id: UUID
    user_id: UUID
    template_id: UUID
    source: str
    acquired_at: datetime


@dataclass(frozen=True, slots=True)
class Selection:
    """Le modèle choisi par la personne : le modèle par défaut de ses exports."""

    user_id: UUID
    template_id: UUID
    ats_safe: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Fit:
    """Ce que la mise en page a dû faire pour tenir (espacements, corps, pages)."""

    spacing: float = 1.0
    font_scale: float = 1.0
    pages: int = 1

    def to_json(self) -> dict[str, Any]:
        """Forme du contrat."""
        return {"spacing": self.spacing, "font_scale": self.font_scale, "pages": self.pages}


@dataclass(frozen=True, slots=True)
class GeneratedCv:
    """Un CV exporté : ses fichiers (clés), sa mise en page et son score ATS."""

    id: UUID
    user_id: UUID
    template_id: UUID
    template_version_id: UUID
    template_label: str
    template_version: int
    ats_safe: bool
    created_at: datetime
    pdf_key: str | None = None
    docx_key: str | None = None
    fit: Fit = field(default_factory=Fit)
    ats: dict[str, Any] = field(default_factory=dict)
    ats_docx: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    explanation_status: ExplanationStatus = ExplanationStatus.PENDING
    offer_id: UUID | None = None
    full_name: str = ""

    @staticmethod
    def new_id() -> UUID:
        """Un identifiant d'export."""
        return uuid4()


def check_formats(formats: list[str] | None) -> tuple[str, ...]:
    """Les formats demandés, dans l'ordre canonique ; les deux par défaut.

    Raises:
        InvalidFormatError: Un format inconnu.
    """
    if not formats:
        return FORMATS
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise InvalidFormatError(unknown)
    return tuple(f for f in FORMATS if f in formats)


def ensure_usable(template: Template, *, owned: bool) -> None:
    """Un modèle Pro ne se rend que pour qui le possède.

    Raises:
        TemplateLockedError: Modèle Pro non possédé.
    """
    if template.is_premium and not owned:
        raise TemplateLockedError()


def slugify(text: str) -> str:
    """`Meridian Pro` → `meridian-pro` (nom technique d'un modèle)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-") or "modele"


def export_file_name(first_name: str, last_name: str, extension: str) -> str:
    """« CV - Prénom Nom.pdf » : le nom proposé au téléchargement."""
    name = " ".join(p for p in (first_name.strip(), last_name.strip()) if p)
    return f"CV - {name}.{extension}" if name else f"CV.{extension}"


def gallery_traits(spec: dict[str, Any]) -> dict[str, Any]:
    """Ce que la galerie affiche, **mesuré** dans la spécification.

    - `primary_color` : la couleur des titres de rubrique ;
    - `font_family` : la famille la plus employée par les styles ;
    - `has_photo`, `columns` : lus dans l'en-tête et les bandes ;
    - `ats` : ce que le **dessin** coûte aux ATS avant tout contenu
      (`spaced_titles` si un style dépasse 0,1 em d'interlettrage, `photo`,
      `multi_column`) — `readiness` vaut `warning` dès qu'il y en a un.
    """
    styles: dict[str, dict[str, Any]] = spec["styles"]
    families = Counter(s["family"] for s in styles.values())
    columns = max((len(b["columns"]) for b in spec["bands"]), default=1)
    warnings = []
    if any(
        float(s["letter_spacing_pt"]) > SPACED_TITLE_EM * float(s["size_pt"])
        and not s.get("synthesized")
        for s in styles.values()
    ):
        warnings.append("spaced_titles")
    if spec["header"].get("photo"):
        warnings.append("photo")
    if columns > 1:
        warnings.append("multi_column")
    return {
        "primary_color": styles.get("section_title", {}).get("color"),
        "font_family": families.most_common(1)[0][0] if families else None,
        "has_photo": bool(spec["header"].get("photo")),
        "columns": columns,
        "ats": {"readiness": "warning" if warnings else "good", "warnings": warnings},
        "fields_added": ["headline"]
        if any(e.get("synthesized") for e in spec["header"]["elements"])
        else [],
    }


def ats_safe_helps(spec: dict[str, Any]) -> bool:
    """La variante « optimisée ATS » change quelque chose à ce modèle."""
    return "spaced_titles" in gallery_traits(spec)["ats"]["warnings"]
