"""Le dossier de candidature : une offre figée, une copie du CV, des propositions réversibles.

Contrat
-------
- **La copie est figée.** Le dossier part d'une copie du CV de base au
  moment de sa création (`cv_base_hash` en garde l'empreinte) ; le CV de
  base n'est jamais touché par l'atelier, et le dossier ne suit pas ses
  changements — il dit seulement s'il a bougé depuis.
- **Karria propose, la personne décide.** Chaque proposition vise un
  chemin du CV, garde `before` et `after`, et porte un état :
  `proposee`, `acceptee`, `refusee`. Accepter applique `after` ; refuser
  ou revenir remet `before`. Tout est réversible, et **rien n'est ajouté** :
  un chemin désigne un emplacement qui existe (`domain/paths.py`).
- **L'adaptation est un état lisible** : `en_cours` (Karria travaille,
  synchrone ou en tâche de fond), `faite`, `echouee` (avec sa raison).
- **Le statut se déduit** : `brouillon` tant que l'adaptation court ou
  qu'une proposition attend ; `pret` ensuite ; `candidate` quand la
  personne dit avoir candidaté (`applied_at`), d'où l'on revient.
- **La lettre n'existe que liée à une offre**, donc ici : un dossier sans
  offre n'existe pas.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from karria.modules.applications.domain.bounds import BoundsError, check_bounds
from karria.modules.applications.domain.coverage import Coverage, coverage
from karria.modules.applications.domain.errors import (
    AdaptationInProgressError,
    AdaptationNotFailedError,
    LetterMissingError,
    ParagraphNotFoundError,
    PropositionInvalidError,
    PropositionNotFoundError,
    PropositionPathError,
)
from karria.modules.applications.domain.letters import Letter
from karria.modules.applications.domain.paths import PathError, read, write

#: Juge un CV entier ; lève si la forme ne tient pas. Fourni par le cas
#: d'usage (port `CvValidator`), parce que le domaine ne voit pas `cv`.
CvCheck = Callable[[Mapping[str, Any]], object]

#: Les chemins que Karria applique d'elle-même (marqués `acceptee`, `before`
#: conservé) : l'intitulé et le « à propos ». La personne voit « mis à jour »
#: et peut revenir d'un geste. Tout le reste attend son accord.
AUTO_ACCEPTED_PATHS: Final[frozenset[str]] = frozenset({"identity.headline", "summary"})
#: Longueur gardée d'une raison de proposition.
REASON_MAX_LENGTH: Final = 200
#: Appels au modèle (créer, relancer, ébaucher, réécrire) admis par compte et
#: par fenêtre : de quoi itérer sur une lettre, pas de quoi faire tourner un
#: compteur de jetons avec un script.
AI_RATE_LIMIT: Final = 30
AI_RATE_WINDOW_SECONDS: Final = 600
#: Poids maximal du corps d'une retouche de CV : un CV de quarante compétences
#: et quinze expériences fait quelques dizaines de kilo-octets.
CV_BODY_MAX_BYTES: Final = 256 * 1024


class DossierStatus(StrEnum):
    """Où en est le dossier, pour l'espace Candidatures."""

    BROUILLON = "brouillon"
    PRET = "pret"
    CANDIDATE = "candidate"


class AdaptationState(StrEnum):
    """Où en est l'adaptation du CV par Karria."""

    EN_COURS = "en_cours"
    FAITE = "faite"
    ECHOUEE = "echouee"


class PropositionState(StrEnum):
    """L'état d'une proposition."""

    PROPOSEE = "proposee"
    ACCEPTEE = "acceptee"
    REFUSEE = "refusee"


class Decision(StrEnum):
    """Ce que la personne fait d'une proposition."""

    #: Appliquer `after` (état `acceptee`).
    ACCEPTER = "accepter"
    #: Remettre `before` et écarter (état `refusee`).
    REFUSER = "refuser"
    #: Remettre `before` sans trancher (état `proposee`) : « je verrai ».
    REVENIR = "revenir"


@dataclass(frozen=True, slots=True)
class OfferSnapshot:
    """L'offre telle qu'elle était quand le dossier a été ouvert.

    `logo_hash` est le condensat du logo chez Karria (bucket public) ; l'URL
    se calcule à la lecture, jamais en base (AGENTS.md §6.12).
    """

    offer_id: UUID
    title: str
    company: str | None
    logo_hash: str | None
    website: str | None
    city: str | None
    contract: str | None
    contract_label: str | None
    source_url: str | None
    excerpt: str
    description: str
    keywords: tuple[str, ...]
    published_at: datetime | None

    def as_json(self) -> dict[str, Any]:
        """La forme stockée."""
        return {
            "offer_id": str(self.offer_id),
            "title": self.title,
            "company": self.company,
            "logo_hash": self.logo_hash,
            "website": self.website,
            "city": self.city,
            "contract": self.contract,
            "contract_label": self.contract_label,
            "source_url": self.source_url,
            "excerpt": self.excerpt,
            "description": self.description,
            "keywords": list(self.keywords),
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> OfferSnapshot:
        """Relit la forme stockée."""
        published = data.get("published_at")
        return cls(
            offer_id=UUID(str(data["offer_id"])),
            title=str(data["title"]),
            company=data.get("company"),
            logo_hash=data.get("logo_hash"),
            website=data.get("website"),
            city=data.get("city"),
            contract=data.get("contract"),
            contract_label=data.get("contract_label"),
            source_url=data.get("source_url"),
            excerpt=str(data.get("excerpt", "")),
            description=str(data.get("description", "")),
            keywords=tuple(str(k) for k in data.get("keywords", [])),
            published_at=datetime.fromisoformat(published) if published else None,
        )


@dataclass(frozen=True, slots=True)
class Proposition:
    """Une proposition de Karria sur un chemin du CV."""

    id: UUID
    path: str
    before: Any
    after: Any
    reason: str
    state: PropositionState = PropositionState.PROPOSEE

    def as_json(self) -> dict[str, Any]:
        """La forme stockée et servie."""
        return {
            "id": str(self.id),
            "path": self.path,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
            "state": self.state.value,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Proposition:
        """Relit la forme stockée."""
        return cls(
            id=UUID(str(data["id"])),
            path=str(data["path"]),
            before=data.get("before"),
            after=data.get("after"),
            reason=str(data.get("reason", "")),
            state=PropositionState(data.get("state", PropositionState.PROPOSEE.value)),
        )


def cv_hash(cv: Mapping[str, Any]) -> str:
    """L'empreinte d'un CV : même contenu, même empreinte, quel que soit l'ordre des clés."""
    canonical = json.dumps(cv, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Dossier:
    """Un dossier de candidature. Immuable : chaque geste rend une nouvelle valeur."""

    id: UUID
    user_id: UUID
    offer: OfferSnapshot
    template_id: UUID | None
    cv: dict[str, Any]
    cv_base_hash: str
    created_at: datetime
    updated_at: datetime
    propositions: tuple[Proposition, ...] = ()
    letter: Letter | None = None
    coverage: Coverage = field(default_factory=Coverage)
    adaptation: AdaptationState = AdaptationState.EN_COURS
    adaptation_error: str | None = None
    applied_at: datetime | None = None
    #: La couleur principale posée par la personne sur le modèle du dossier
    #: (`#RRGGBB`) ; `None` : la couleur du modèle (ADR-0034).
    primary_color: str | None = None

    @classmethod
    def open(
        cls,
        *,
        user_id: UUID,
        offer: OfferSnapshot,
        base_cv: Mapping[str, Any],
        template_id: UUID | None,
        now: datetime,
    ) -> Dossier:
        """Un dossier neuf : copie du CV de base, offre figée, adaptation à faire."""
        cv = json.loads(json.dumps(base_cv))
        return cls(
            id=uuid4(),
            user_id=user_id,
            offer=offer,
            template_id=template_id,
            cv=cv,
            cv_base_hash=cv_hash(base_cv),
            created_at=now,
            updated_at=now,
            coverage=coverage(offer.keywords, cv),
        )

    # ------------------------------------------------------------ états

    @property
    def status(self) -> DossierStatus:
        """Déduit : candidaté, sinon prêt quand plus rien n'attend, sinon brouillon."""
        if self.applied_at is not None:
            return DossierStatus.CANDIDATE
        if self.adaptation is AdaptationState.EN_COURS or any(
            p.state is PropositionState.PROPOSEE for p in self.propositions
        ):
            return DossierStatus.BROUILLON
        return DossierStatus.PRET

    @property
    def is_adapting(self) -> bool:
        """Karria travaille encore sur le CV."""
        return self.adaptation is AdaptationState.EN_COURS

    def ensure_not_adapting(self) -> None:
        """Refuse un geste qui entrerait en conflit avec l'adaptation en cours.

        Raises:
            AdaptationInProgressError: L'adaptation court encore.
        """
        if self.is_adapting:
            raise AdaptationInProgressError(self.id)

    def base_changed(self, current_base: Mapping[str, Any] | None) -> bool:
        """Le CV de base a-t-il changé depuis la copie ? Sans CV de base : oui, il a disparu."""
        return current_base is None or cv_hash(current_base) != self.cv_base_hash

    # ------------------------------------------------------------ adaptation

    def adaptation_started(self, *, now: datetime) -> Dossier:
        """Repart (après un échec) : les anciennes propositions sont oubliées."""
        if self.adaptation is not AdaptationState.ECHOUEE:
            raise AdaptationNotFailedError(self.id, self.adaptation.value)
        return replace(
            self,
            adaptation=AdaptationState.EN_COURS,
            adaptation_error=None,
            propositions=(),
            updated_at=now,
        )

    def adapted(
        self,
        propositions: Sequence[Proposition],
        *,
        now: datetime,
        check: CvCheck | None = None,
    ) -> Dossier:
        """Les propositions de Karria arrivent.

        Celles qui visent un chemin d'`AUTO_ACCEPTED_PATHS` sont **appliquées**
        et marquées `acceptee`, `before` conservé ; les autres restent
        `proposee`. Une proposition dont le chemin ne désigne plus rien (la
        personne a retouché le CV entre-temps) est écartée. Une valeur hors
        des bornes de son chemin, ou un CV que `check` refuse une fois la
        valeur posée, **ne s'écrit pas** : la proposition reste `proposee` —
        la sortie du modèle est une donnée, pas une décision.
        """
        cv = self.cv
        kept: list[Proposition] = []
        for proposition in propositions:
            try:
                current = read(cv, proposition.path)
            except PathError:
                continue
            # `before` est ce qu'il y a **maintenant** : si l'adaptation est rejouée
            # sur un dossier retouché entre-temps, « revenir » rend la retouche.
            refreshed = replace(proposition, before=current)
            if proposition.path in AUTO_ACCEPTED_PATHS:
                try:
                    candidate = self._applied(cv, refreshed, refreshed.after, check)
                except PropositionInvalidError:
                    kept.append(replace(refreshed, state=PropositionState.PROPOSEE))
                    continue
                cv = candidate
                kept.append(replace(refreshed, state=PropositionState.ACCEPTEE))
            else:
                kept.append(replace(refreshed, state=PropositionState.PROPOSEE))
        return replace(
            self,
            cv=cv,
            propositions=tuple(kept),
            coverage=coverage(self.offer.keywords, cv),
            adaptation=AdaptationState.FAITE,
            adaptation_error=None,
            updated_at=now,
        )

    @staticmethod
    def _applied(
        cv: Mapping[str, Any], proposition: Proposition, value: Any, check: CvCheck | None
    ) -> dict[str, Any]:
        """Le CV avec `value` posée au chemin, ou `PropositionInvalidError` / `PathError`."""
        try:
            check_bounds(proposition.path, value)
        except BoundsError as exc:
            raise PropositionInvalidError(exc.path, exc.why) from exc
        candidate = write(cv, proposition.path, value)
        if check is not None:
            try:
                check(candidate)
            except Exception as exc:
                raise PropositionInvalidError(proposition.path, "cv_invalid") from exc
        return candidate

    def adaptation_failed(self, reason: str, *, now: datetime) -> Dossier:
        """Karria n'a pas pu adapter ; le dossier reste utilisable à la main."""
        return replace(
            self,
            adaptation=AdaptationState.ECHOUEE,
            adaptation_error=reason[:64],
            updated_at=now,
        )

    # ------------------------------------------------------------ propositions

    def decide(
        self,
        proposition_id: UUID,
        decision: Decision,
        *,
        now: datetime,
        check: CvCheck | None = None,
    ) -> Dossier:
        """Accepter, refuser ou revenir sur une proposition — et modifier le CV en conséquence.

        Raises:
            PropositionNotFoundError: Aucune proposition de cet identifiant.
            PropositionPathError: Le chemin ne désigne plus rien dans le CV.
            PropositionInvalidError: La valeur n'a pas la forme du chemin, ou `check` refuse le CV.
        """
        index = next((i for i, p in enumerate(self.propositions) if p.id == proposition_id), None)
        if index is None:
            raise PropositionNotFoundError(proposition_id)
        proposition = self.propositions[index]
        value = proposition.after if decision is Decision.ACCEPTER else proposition.before
        try:
            cv = self._applied(self.cv, proposition, value, check)
        except PathError as exc:
            raise PropositionPathError(proposition.path) from exc
        state = {
            Decision.ACCEPTER: PropositionState.ACCEPTEE,
            Decision.REFUSER: PropositionState.REFUSEE,
            Decision.REVENIR: PropositionState.PROPOSEE,
        }[decision]
        propositions = list(self.propositions)
        propositions[index] = replace(proposition, state=state)
        return replace(
            self,
            cv=cv,
            propositions=tuple(propositions),
            coverage=coverage(self.offer.keywords, cv),
            updated_at=now,
        )

    # ------------------------------------------------------------ CV et lettre

    def with_cv(self, cv: Mapping[str, Any], *, now: datetime) -> Dossier:
        """Le CV retouché à la main par la personne ; la couverture est recalculée."""
        copied = json.loads(json.dumps(cv))
        return replace(
            self, cv=copied, coverage=coverage(self.offer.keywords, copied), updated_at=now
        )

    def with_letter(self, letter: Letter, *, now: datetime) -> Dossier:
        """La lettre, ébauchée par Karria ou envoyée par la personne."""
        return replace(self, letter=letter, updated_at=now)

    def with_template(self, template_id: UUID | None, *, now: datetime) -> Dossier:
        """Le modèle de CV du dossier, changé à tout moment ; `None` : le modèle choisi de la personne.

        Idempotent : redemander le modèle déjà posé ne touche pas `updated_at`.
        Le droit d'employer le modèle (gratuit ou possédé) n'est pas une règle
        du dossier : le cas d'usage le vérifie par le port `TemplateChecker`.
        """
        if template_id == self.template_id:
            return self
        return replace(self, template_id=template_id, updated_at=now)

    def with_primary_color(self, primary_color: str | None, *, now: datetime) -> Dossier:
        """La couleur principale du dossier (`#RRGGBB`, déjà normalisée), ou `None` : celle du modèle.

        Idempotent, comme le modèle. Elle survit au changement de modèle : une
        couleur choisie est un choix de la personne, pas un attribut du modèle.
        """
        if primary_color == self.primary_color:
            return self
        return replace(self, primary_color=primary_color, updated_at=now)

    def with_paragraph(self, index: int, text: str, *, now: datetime) -> Dossier:
        """Un seul paragraphe remplacé.

        Raises:
            LetterMissingError: Pas de lettre.
            ParagraphNotFoundError: L'index dépasse.
        """
        if self.letter is None:
            raise LetterMissingError(self.id)
        if index < 0 or index >= len(self.letter.paragraphs):
            raise ParagraphNotFoundError(index)
        return replace(self, letter=self.letter.with_paragraph(index, text), updated_at=now)

    # ------------------------------------------------------------ candidature

    def mark_applied(self, *, now: datetime) -> Dossier:
        """La personne dit avoir candidaté. Idempotent : la première date reste."""
        if self.applied_at is not None:
            return self
        return replace(self, applied_at=now, updated_at=now)

    def unmark_applied(self, *, now: datetime) -> Dossier:
        """Elle s'est trompée : retour à l'état déduit."""
        if self.applied_at is None:
            return self
        return replace(self, applied_at=None, updated_at=now)

    # ------------------------------------------------------------ lecture

    @property
    def pending_count(self) -> int:
        """Les propositions qui attendent encore une décision."""
        return sum(1 for p in self.propositions if p.state is PropositionState.PROPOSEE)
