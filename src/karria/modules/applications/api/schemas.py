"""Contrat HTTP du module `applications` — `docs/contrat-api-candidatures.md`, en exécutable.

Les schémas sont distincts du domaine : tout changement cassant du contrat
se voit ici. Le `cv` d'un dossier a la forme de `GET /cv/me` **sans**
`image_url` ni `id` de CV : la photo reste celle du CV de base, et c'est
l'app qui passe ce CV au module `documents` avec le `dossier_id` pour le
rendu, les exports et l'envoi.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from karria.modules.applications.application import LogoUrl
from karria.modules.applications.domain import (
    INSTRUCTION_MAX_LENGTH,
    AdaptationState,
    Decision,
    Dossier,
    DossierStatus,
    Letter,
    LetterShape,
    OfferSnapshot,
    ParagraphKind,
    Proposition,
    PropositionState,
)

# ---------------------------------------------------------------------
# Lectures
# ---------------------------------------------------------------------


class OfferSnapshotRead(BaseModel):
    """L'offre telle qu'elle était à l'ouverture du dossier — lisible même si elle a disparu."""

    offer_id: UUID
    title: str
    company: str | None
    logo_url: str | None = Field(
        description="Logo carré 128 px chez Karria, adresse stable ; `null` : initiales."
    )
    website: str | None
    city: str | None
    contract: str | None = Field(description="`cdi`, `cdd`, `alternance`… ou `null`.")
    contract_label: str | None
    source_url: str | None = Field(description="Où postuler (lien de l'annonce).")
    excerpt: str = Field(description="Les premières lignes de l'annonce, sur une ligne.")
    description: str = Field(description="Le texte complet de l'annonce, figé.")
    keywords: list[str] = Field(
        description="Les mots-clés de l'offre, figés, dans l'ordre d'importance."
    )
    published_at: datetime | None

    @classmethod
    def present(cls, offer: OfferSnapshot, logo_url: LogoUrl | None) -> OfferSnapshotRead:
        """Projette ; l'URL du logo est calculée, jamais stockée."""
        return cls(
            offer_id=offer.offer_id,
            title=offer.title,
            company=offer.company,
            logo_url=logo_url(offer.logo_hash) if (offer.logo_hash and logo_url) else None,
            website=offer.website,
            city=offer.city,
            contract=offer.contract,
            contract_label=offer.contract_label,
            source_url=offer.source_url,
            excerpt=offer.excerpt,
            description=offer.description,
            keywords=list(offer.keywords),
            published_at=offer.published_at,
        )


class PropositionRead(BaseModel):
    """Une proposition de Karria, et ce que la personne en a fait."""

    id: UUID
    path: str = Field(
        description="Le chemin dans `cv` : `identity.headline`, `summary`, "
        "`experiences.1.missions.0`, `skills` (ordre), `experiences` (ordre)."
    )
    before: Any = Field(description="La valeur d'origine, pour revenir.")
    after: Any = Field(description="La valeur proposée.")
    reason: str = Field(description="Pourquoi, en une phrase.")
    state: PropositionState = Field(description="`proposee`, `acceptee` ou `refusee`.")

    @classmethod
    def present(cls, proposition: Proposition) -> PropositionRead:
        return cls(
            id=proposition.id,
            path=proposition.path,
            before=proposition.before,
            after=proposition.after,
            reason=proposition.reason,
            state=proposition.state,
        )


class ParagraphRead(BaseModel):
    """Un paragraphe de la lettre."""

    kind: ParagraphKind
    text: str


class LetterRead(BaseModel):
    """La lettre de motivation du dossier."""

    recipient: str
    subject: str
    paragraphs: list[ParagraphRead]
    closing: str

    @classmethod
    def present(cls, letter: Letter) -> LetterRead:
        return cls(
            recipient=letter.recipient,
            subject=letter.subject,
            paragraphs=[ParagraphRead(kind=p.kind, text=p.text) for p in letter.paragraphs],
            closing=letter.closing,
        )


class CoverageRead(BaseModel):
    """Les mots-clés de l'offre présents et absents du CV adapté."""

    present: list[str]
    missing: list[str]
    ratio: float = Field(description="Part couverte, entre 0 et 1.")


class DossierRead(BaseModel):
    """Un dossier de candidature, entier."""

    id: UUID
    offer: OfferSnapshotRead
    template_id: UUID | None
    cv: dict[str, Any] = Field(
        description="La copie adaptée du CV, au format `GET /cv/me` (sans `image_url` ni `id`)."
    )
    cv_base_hash: str
    base_changed: bool = Field(
        description="Vrai : le CV de base a changé (ou disparu) depuis la copie."
    )
    propositions: list[PropositionRead]
    letter: LetterRead | None
    coverage: CoverageRead
    status: DossierStatus = Field(description="`brouillon`, `pret` ou `candidate`.")
    adaptation: AdaptationState = Field(description="`en_cours`, `faite` ou `echouee`.")
    adaptation_error: str | None = Field(
        description="`ai_unavailable`, `ai_output_invalid`, `queue_unavailable`… si `echouee`."
    )
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def present(
        cls, dossier: Dossier, *, base_changed: bool, logo_url: LogoUrl | None
    ) -> DossierRead:
        """Projette le dossier sur le contrat."""
        return cls(
            id=dossier.id,
            offer=OfferSnapshotRead.present(dossier.offer, logo_url),
            template_id=dossier.template_id,
            cv=dossier.cv,
            cv_base_hash=dossier.cv_base_hash,
            base_changed=base_changed,
            propositions=[PropositionRead.present(p) for p in dossier.propositions],
            letter=LetterRead.present(dossier.letter) if dossier.letter else None,
            coverage=CoverageRead(
                present=list(dossier.coverage.present),
                missing=list(dossier.coverage.missing),
                ratio=dossier.coverage.ratio,
            ),
            status=dossier.status,
            adaptation=dossier.adaptation,
            adaptation_error=dossier.adaptation_error,
            applied_at=dossier.applied_at,
            created_at=dossier.created_at,
            updated_at=dossier.updated_at,
        )


class OfferCardRead(BaseModel):
    """L'offre d'un dossier, pour une carte de liste."""

    offer_id: UUID
    title: str
    company: str | None
    logo_url: str | None
    city: str | None
    contract_label: str | None
    source_url: str | None


class DossierCardRead(BaseModel):
    """Un dossier dans la liste : sans le CV, les propositions ni la lettre."""

    id: UUID
    offer: OfferCardRead
    template_id: UUID | None
    status: DossierStatus
    adaptation: AdaptationState
    coverage_ratio: float
    pending_propositions: int = Field(description="Propositions qui attendent une décision.")
    has_letter: bool
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def present(cls, dossier: Dossier, logo_url: LogoUrl | None) -> DossierCardRead:
        offer = dossier.offer
        return cls(
            id=dossier.id,
            offer=OfferCardRead(
                offer_id=offer.offer_id,
                title=offer.title,
                company=offer.company,
                logo_url=logo_url(offer.logo_hash) if (offer.logo_hash and logo_url) else None,
                city=offer.city,
                contract_label=offer.contract_label,
                source_url=offer.source_url,
            ),
            template_id=dossier.template_id,
            status=dossier.status,
            adaptation=dossier.adaptation,
            coverage_ratio=dossier.coverage.ratio,
            pending_propositions=dossier.pending_count,
            has_letter=dossier.letter is not None,
            applied_at=dossier.applied_at,
            created_at=dossier.created_at,
            updated_at=dossier.updated_at,
        )


class DossierListRead(BaseModel):
    """La liste des dossiers, du plus récemment modifié au plus ancien."""

    items: list[DossierCardRead]


# ---------------------------------------------------------------------
# Requêtes
# ---------------------------------------------------------------------


class CreateDossierRequest(BaseModel):
    """« Adapter mon CV à cette offre »."""

    model_config = ConfigDict(extra="forbid")

    offer_id: UUID
    template_id: UUID | None = Field(
        default=None, description="Le modèle de CV choisi ; vérifié (gratuit ou possédé)."
    )


class DecisionRequest(BaseModel):
    """Ce que la personne fait d'une proposition."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision = Field(description="`accepter`, `refuser` ou `revenir`.")


class CvUpdateRequest(BaseModel):
    """Le CV du dossier retouché à la main, entier."""

    model_config = ConfigDict(extra="forbid")

    cv: dict[str, Any] = Field(description="Au format `GET /cv/me` ; revalidé par le serveur.")


class LetterWriteRequest(LetterShape):
    """La lettre entière, éditée à la main. Même forme que `LetterRead`."""

    model_config = ConfigDict(extra="forbid")


class RegenerateRequest(BaseModel):
    """Réécrire un paragraphe, avec une consigne courte facultative."""

    model_config = ConfigDict(extra="forbid")

    instruction: str | None = Field(
        default=None,
        max_length=INSTRUCTION_MAX_LENGTH,
        description="« plus court », « insiste sur mon alternance »… Donnée, pas ordre.",
    )
