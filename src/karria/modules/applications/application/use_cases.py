"""Cas d'usage du module `applications` : ouvrir un dossier, l'adapter, décider, écrire, candidater.

Chaque cas d'usage orchestre des ports ; aucune règle n'est ici (elles
vivent dans le domaine), aucun appel direct à l'ORM ni au modèle
(AGENTS.md §6.2). Un dossier n'est jamais lu sans son propriétaire
(`find(dossier_id, user_id=…)`) : un identifiant deviné ne donne rien.

L'adaptation, synchrone ou différée
-----------------------------------
`CreateDossierFromOffer` lance l'adaptation **dans la requête**, bornée à
`ADAPTATION_INLINE_SECONDS` : le plus souvent, la personne arrive dans
l'atelier avec les propositions prêtes. Si le modèle est plus lent, la
requête rend le dossier en `adaptation: en_cours` et la suite part sur la
file durable (`DossierJobs`) ; l'app relit le dossier jusqu'à `faite` ou
`echouee`. Dans les deux cas, c'est le même `AdaptCv` qui travaille.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from karria.modules.applications.application.events import DossierApplied, DossierUnapplied
from karria.modules.applications.application.ports import (
    BaseCvReader,
    CvAdapter,
    CvValidator,
    DossierJobs,
    DossierRepository,
    LetterWriter,
    OfferCard,
    OfferReader,
    TemplateChecker,
)
from karria.modules.applications.domain import (
    BaseCvMissingError,
    Decision,
    Dossier,
    DossierConflictError,
    DossierNotFoundError,
    Letter,
    LetterMissingError,
    ModelOutputInvalidError,
    ModelUnavailableError,
    OfferNotFoundError,
    OfferSnapshot,
    ParagraphNotFoundError,
    TemplateLockedError,
    extract_keywords,
    propositions_from,
    validated_paragraph,
)
from karria.shared.clock import Clock
from karria.shared.events import EventBus
from karria.shared.logging import get_logger

logger = get_logger(__name__)

#: Temps laissé à l'adaptation dans la requête de création. Au-delà, elle
#: continue sur la file : une requête HTTP qui attend un modèle plus de dix
#: secondes fait croire à une panne, et l'app sait relire un état.
ADAPTATION_INLINE_SECONDS: Final = 10.0
#: Longueur de l'extrait de l'offre gardé pour les cartes.
EXCERPT_LENGTH: Final = 280
#: Rejeux d'une écriture sous contrôle optimiste avant d'abandonner.
SETTLE_ATTEMPTS: Final = 3


def excerpt_of(description: str) -> str:
    """Les premières lignes, sur une ligne, coupées sur un mot."""
    flat = " ".join(description.split())
    if len(flat) <= EXCERPT_LENGTH:
        return flat
    return flat[:EXCERPT_LENGTH].rsplit(" ", 1)[0] + "…"


def snapshot_of(card: OfferCard) -> OfferSnapshot:
    """L'offre figée dans le dossier, mots-clés compris."""
    return OfferSnapshot(
        offer_id=card.offer_id,
        title=card.title,
        company=card.company,
        logo_hash=card.logo_hash,
        website=card.website,
        city=card.city,
        contract=card.contract,
        contract_label=card.contract_label,
        source_url=card.source_url,
        excerpt=excerpt_of(card.description),
        description=card.description,
        keywords=tuple(extract_keywords(card.title, card.description)),
        published_at=card.published_at,
    )


class AdaptCv:
    """Demande au modèle ses propositions et les range dans le dossier.

    Un seul chemin de travail, deux portes d'entrée : `run_inline` depuis la
    requête (bornée, puis différée), `execute` depuis le worker (sans borne).
    Un échec du modèle ne casse rien : le dossier passe en `echouee` et reste
    utilisable à la main ; `POST /{id}/adapt` relance.
    """

    def __init__(
        self,
        *,
        dossiers: DossierRepository,
        adapter: CvAdapter,
        jobs: DossierJobs,
        validator: CvValidator,
        clock: Clock,
        inline_seconds: float | None = None,
    ) -> None:
        self._dossiers = dossiers
        self._adapter = adapter
        self._jobs = jobs
        self._validator = validator
        self._clock = clock
        # Lu à l'assemblage et non à la définition : un test peut raccourcir la borne.
        self._inline_seconds = (
            ADAPTATION_INLINE_SECONDS if inline_seconds is None else inline_seconds
        )

    async def execute(self, dossier_id: UUID) -> Dossier | None:
        """Depuis le worker : adapte si le dossier existe encore et attend toujours."""
        dossier = await self._dossiers.find_any(dossier_id)
        if dossier is None:
            logger.info("applications.adapt_skipped", dossier_id=str(dossier_id), why="deleted")
            return None
        if not dossier.is_adapting:
            logger.info("applications.adapt_skipped", dossier_id=str(dossier_id), why="done")
            return dossier
        return await self._settle(dossier, await self._ask(dossier))

    async def run_inline(self, dossier: Dossier) -> Dossier:
        """Depuis la requête : borné ; passé le délai, la suite part sur la file."""
        try:
            async with asyncio.timeout(self._inline_seconds):
                outcome = await self._ask(dossier)
        except TimeoutError:
            return await self._defer(dossier)
        return await self._settle(dossier, outcome)

    async def _ask(self, dossier: Dossier) -> Dossier:
        try:
            output = await self._adapter.adapt(dossier.offer, dossier.cv)
        except (ModelUnavailableError, ModelOutputInvalidError) as exc:
            reason = str(exc.details.get("reason", "ai_unavailable"))
            logger.warning("applications.adapt_failed", dossier_id=str(dossier.id), reason=reason)
            return dossier.adaptation_failed(reason, now=self._clock.now())
        conversion = propositions_from(output, dossier.cv)
        adapted = dossier.adapted(
            conversion.propositions, now=self._clock.now(), check=self._validator.validate
        )
        logger.info(
            "applications.cv_adapted",
            dossier_id=str(dossier.id),
            propositions=len(adapted.propositions),
            auto_applied=len(adapted.propositions) - adapted.pending_count,
            discarded=list(conversion.discarded),
            coverage=adapted.coverage.ratio,
        )
        return adapted

    async def _settle(self, before: Dossier, after: Dossier) -> Dossier:
        """Écrit le résultat sous contrôle optimiste : jamais par-dessus une retouche.

        L'écriture n'a lieu que si le dossier n'a pas bougé depuis `before`.
        Sinon on relit l'état courant et on y **rejoue** les propositions (les
        chemins disparus sont écartés) ; si le dossier a été supprimé, le
        résultat est jeté. Deux rejeux au plus : passé cela, la personne
        écrit plus vite que le modèle, l'adaptation est marquée `echouee`.
        """
        expected = before.updated_at
        for _ in range(SETTLE_ATTEMPTS):
            if await self._dossiers.save_if_unchanged(after, expected_updated_at=expected):
                return after
            current = await self._dossiers.find_any(before.id)
            if current is None:
                logger.info("applications.adapt_skipped", dossier_id=str(before.id), why="deleted")
                return after
            logger.info("applications.adapt_rebased", dossier_id=str(before.id))
            after = (
                current.adapted(
                    after.propositions, now=self._clock.now(), check=self._validator.validate
                )
                if after.adaptation_error is None
                else current.adaptation_failed(after.adaptation_error, now=self._clock.now())
            )
            expected = current.updated_at
        failed = after.adaptation_failed("stale", now=self._clock.now())
        if not await self._dossiers.save_if_unchanged(failed, expected_updated_at=expected):
            logger.warning("applications.adapt_unsettled", dossier_id=str(before.id))
        return failed

    async def _defer(self, dossier: Dossier) -> Dossier:
        try:
            await self._jobs.defer_adaptation(dossier.id)
        except Exception as exc:
            logger.error(
                "applications.adapt_defer_failed", dossier_id=str(dossier.id), error=str(exc)
            )
            failed = dossier.adaptation_failed("queue_unavailable", now=self._clock.now())
            await self._dossiers.save(failed)
            return failed
        logger.info("applications.adapt_deferred", dossier_id=str(dossier.id))
        return dossier


@dataclass(frozen=True, slots=True)
class CreationResult:
    """Le dossier, et s'il vient d'être créé (sinon : celui qui existait pour cette offre)."""

    dossier: Dossier
    created: bool


class CreateDossierFromOffer:
    """« Adapter mon CV à cette offre » : copie le CV, fige l'offre, lance l'adaptation.

    Un dossier par offre et par personne : redemander rend l'existant,
    sans rien relancer — l'app propose alors de le reprendre ou de le
    supprimer pour repartir du CV de base.
    """

    def __init__(
        self,
        *,
        dossiers: DossierRepository,
        offers: OfferReader,
        base_cvs: BaseCvReader,
        templates: TemplateChecker,
        adapt: AdaptCv,
        clock: Clock,
    ) -> None:
        self._dossiers = dossiers
        self._offers = offers
        self._base_cvs = base_cvs
        self._templates = templates
        self._adapt = adapt
        self._clock = clock

    async def execute(
        self, user_id: UUID, offer_id: UUID, template_id: UUID | None
    ) -> CreationResult:
        """Rend le dossier, adapté si le modèle a répondu à temps.

        Raises:
            BaseCvMissingError: Pas de CV de base.
            OfferNotFoundError: L'offre n'est pas dans l'index.
            TemplateLockedError: Modèle Premium non possédé.
        """
        existing = await self._dossiers.find_by_offer(user_id, offer_id)
        if existing is not None:
            return CreationResult(existing, created=False)
        base = await self._base_cvs.read(user_id)
        if base is None:
            raise BaseCvMissingError()
        card = await self._offers.read(offer_id)
        if card is None:
            raise OfferNotFoundError(offer_id)
        if template_id is not None and not await self._templates.usable(user_id, template_id):
            raise TemplateLockedError(template_id)
        dossier = Dossier.open(
            user_id=user_id,
            offer=snapshot_of(card),
            base_cv=base.data,
            template_id=template_id,
            now=self._clock.now(),
        )
        await self._dossiers.insert(dossier)
        logger.info(
            "applications.dossier_opened",
            dossier_id=str(dossier.id),
            user_id=str(user_id),
            offer_id=str(offer_id),
            keywords=len(dossier.offer.keywords),
        )
        return CreationResult(await self._adapt.run_inline(dossier), created=True)


class RetryAdaptation:
    """Relance une adaptation qui a échoué (`POST /{id}/adapt`)."""

    def __init__(self, *, dossiers: DossierRepository, adapt: AdaptCv, clock: Clock) -> None:
        self._dossiers = dossiers
        self._adapt = adapt
        self._clock = clock

    async def execute(self, user_id: UUID, dossier_id: UUID) -> Dossier:
        """Raises `AdaptationNotFailedError` si elle n'a pas échoué."""
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        restarted = dossier.adaptation_started(now=self._clock.now())
        await self._dossiers.save(restarted)
        return await self._adapt.run_inline(restarted)


@dataclass(frozen=True, slots=True)
class DossierView:
    """Un dossier, et ce qu'on sait de son CV de base aujourd'hui."""

    dossier: Dossier
    base_changed: bool


class GetDossier:
    """Un dossier de la personne, avec « ton CV de base a changé depuis »."""

    def __init__(self, dossiers: DossierRepository, base_cvs: BaseCvReader) -> None:
        self._dossiers = dossiers
        self._base_cvs = base_cvs

    async def execute(self, user_id: UUID, dossier_id: UUID) -> DossierView:
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        base = await self._base_cvs.read(user_id)
        return DossierView(dossier, dossier.base_changed(base.data if base else None))


class ListDossiers:
    """Les dossiers de la personne, du plus récent au plus ancien ; les candidatures seules si demandé."""

    def __init__(self, dossiers: DossierRepository) -> None:
        self._dossiers = dossiers

    async def execute(self, user_id: UUID, *, applied_only: bool) -> list[Dossier]:
        return await self._dossiers.list_by_user(user_id, applied_only=applied_only)


class DeleteDossier:
    """Supprime un dossier. Le CV de base n'est pas concerné."""

    def __init__(self, dossiers: DossierRepository) -> None:
        self._dossiers = dossiers

    async def execute(self, user_id: UUID, dossier_id: UUID) -> None:
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        await self._dossiers.delete(dossier.id)
        logger.info("applications.dossier_deleted", dossier_id=str(dossier_id))


class DecideProposition:
    """Accepter, refuser ou revenir sur une proposition de Karria.

    La valeur écrite passe les bornes de son chemin, et le CV résultant la
    forme d'un CV (`CvValidator`) : une proposition qui ne tient pas ne
    s'applique pas (`409 proposition_invalid`).
    """

    def __init__(self, dossiers: DossierRepository, validator: CvValidator, clock: Clock) -> None:
        self._dossiers = dossiers
        self._validator = validator
        self._clock = clock

    async def execute(
        self, user_id: UUID, dossier_id: UUID, proposition_id: UUID, decision: Decision
    ) -> Dossier:
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        decided = dossier.decide(
            proposition_id, decision, now=self._clock.now(), check=self._validator.validate
        )
        await self._dossiers.save(decided)
        logger.info(
            "applications.proposition_decided",
            dossier_id=str(dossier_id),
            proposition_id=str(proposition_id),
            decision=decision.value,
        )
        return decided


class UpdateDossierCv:
    """La personne a retouché le CV du dossier à la main ; la couverture est recalculée."""

    def __init__(self, dossiers: DossierRepository, validator: CvValidator, clock: Clock) -> None:
        self._dossiers = dossiers
        self._validator = validator
        self._clock = clock

    async def execute(self, user_id: UUID, dossier_id: UUID, cv: Mapping[str, Any]) -> Dossier:
        """Raises `AdaptationInProgressError` tant que Karria travaille, `CvInvalidError` si la forme ne tient pas."""
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        dossier.ensure_not_adapting()
        updated = dossier.with_cv(self._validator.validate(cv), now=self._clock.now())
        await self._dossiers.save(updated)
        return updated


class ChangeDossierTemplate:
    """Le modèle de CV du dossier change à tout moment (`PUT /{id}/template`).

    La même porte qu'à l'ouverture : un modèle Premium non possédé est refusé
    (`TemplateLockedError`, par le port `TemplateChecker` vers `documents`).
    Rien d'autre ne bouge — ni le CV adapté, ni les propositions, ni la
    lettre : changer de modèle, c'est changer la mise en page, pas le fond.
    `None` rend la main au modèle choisi de la personne.
    """

    def __init__(
        self, dossiers: DossierRepository, templates: TemplateChecker, clock: Clock
    ) -> None:
        self._dossiers = dossiers
        self._templates = templates
        self._clock = clock

    async def execute(self, user_id: UUID, dossier_id: UUID, template_id: UUID | None) -> Dossier:
        """Raises `DossierNotFoundError`, puis `TemplateLockedError` si le modèle n'est pas utilisable."""
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        if template_id is not None and not await self._templates.usable(user_id, template_id):
            raise TemplateLockedError(template_id)
        updated = dossier.with_template(template_id, now=self._clock.now())
        if updated is dossier:
            return dossier
        await self._dossiers.save(updated)
        logger.info(
            "applications.template_changed",
            dossier_id=str(dossier_id),
            template_id=str(template_id) if template_id else None,
        )
        return updated


class DraftLetter:
    """La première ébauche de la lettre, par Karria. Remplace la lettre existante."""

    def __init__(self, dossiers: DossierRepository, writer: LetterWriter, clock: Clock) -> None:
        self._dossiers = dossiers
        self._writer = writer
        self._clock = clock

    async def execute(self, user_id: UUID, dossier_id: UUID) -> Dossier:
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        dossier.ensure_not_adapting()
        letter = await self._writer.draft(dossier.offer, dossier.cv)
        updated = await _commit_after_model(
            self._dossiers,
            dossier,
            user_id,
            lambda current: current.with_letter(letter, now=self._clock.now()),
        )
        logger.info(
            "applications.letter_drafted",
            dossier_id=str(dossier_id),
            paragraphs=len(letter.paragraphs),
        )
        return updated


class UpdateLetter:
    """L'édition manuelle de la lettre, entière (paragraphes, objet, formule)."""

    def __init__(self, dossiers: DossierRepository, clock: Clock) -> None:
        self._dossiers = dossiers
        self._clock = clock

    async def execute(self, user_id: UUID, dossier_id: UUID, letter: Mapping[str, Any]) -> Dossier:
        """Raises `LetterInvalidError` si la forme ne tient pas."""
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        updated = dossier.with_letter(Letter.validated(letter), now=self._clock.now())
        await self._dossiers.save(updated)
        return updated


class RegenerateParagraph:
    """Un seul paragraphe réécrit par Karria, avec une consigne courte facultative."""

    def __init__(self, dossiers: DossierRepository, writer: LetterWriter, clock: Clock) -> None:
        self._dossiers = dossiers
        self._writer = writer
        self._clock = clock

    async def execute(
        self, user_id: UUID, dossier_id: UUID, index: int, instruction: str | None
    ) -> Dossier:
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        if dossier.letter is None:
            raise LetterMissingError(dossier_id)
        if index < 0 or index >= len(dossier.letter.paragraphs):
            raise ParagraphNotFoundError(index)
        text = validated_paragraph(
            await self._writer.rewrite_paragraph(
                dossier.offer, dossier.cv, dossier.letter, index, instruction
            )
        )
        updated = await _commit_after_model(
            self._dossiers,
            dossier,
            user_id,
            lambda current: current.with_paragraph(index, text, now=self._clock.now()),
        )
        logger.info("applications.paragraph_regenerated", dossier_id=str(dossier_id), index=index)
        return updated


class MarkApplied:
    """« As-tu candidaté ? » — oui : le dossier rejoint l'espace Candidatures."""

    def __init__(self, dossiers: DossierRepository, events: EventBus, clock: Clock) -> None:
        self._dossiers = dossiers
        self._events = events
        self._clock = clock

    async def execute(self, user_id: UUID, dossier_id: UUID) -> Dossier:
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        if dossier.applied_at is not None:
            return dossier
        marked = dossier.mark_applied(now=self._clock.now())
        await self._dossiers.save(marked)
        await self._events.publish(
            DossierApplied(user_id=user_id, offer_id=dossier.offer.offer_id, dossier_id=dossier.id)
        )
        logger.info("applications.applied", dossier_id=str(dossier_id))
        return marked


class UnmarkApplied:
    """Retour sur la déclaration : le dossier redevient brouillon ou prêt."""

    def __init__(self, dossiers: DossierRepository, events: EventBus, clock: Clock) -> None:
        self._dossiers = dossiers
        self._events = events
        self._clock = clock

    async def execute(self, user_id: UUID, dossier_id: UUID) -> Dossier:
        dossier = await _owned(self._dossiers, dossier_id, user_id)
        if dossier.applied_at is None:
            return dossier
        unmarked = dossier.unmark_applied(now=self._clock.now())
        await self._dossiers.save(unmarked)
        await self._events.publish(
            DossierUnapplied(
                user_id=user_id, offer_id=dossier.offer.offer_id, dossier_id=dossier.id
            )
        )
        logger.info("applications.unapplied", dossier_id=str(dossier_id))
        return unmarked


class ForgetUser:
    """Efface les dossiers d'un compte supprimé — réaction à `AccountDeleted` (ADR-0015).

    Rien que des lignes : aucun fichier n'appartient à ce module (les exports
    sont ceux de `documents`). Idempotent.
    """

    def __init__(self, dossiers: DossierRepository) -> None:
        self._dossiers = dossiers

    async def execute(self, user_id: UUID) -> int:
        """Rend le nombre de dossiers effacés."""
        count = await self._dossiers.delete_by_user(user_id)
        logger.info("applications.user_forgotten", user_id=str(user_id), dossiers=count)
        return count


async def _commit_after_model(
    dossiers: DossierRepository,
    seen: Dossier,
    user_id: UUID,
    mutate: Callable[[Dossier], Dossier],
) -> Dossier:
    """Écrit le fruit d'un appel au modèle **sur l'état courant**, sous contrôle optimiste.

    Le dossier lu avant l'appel (`seen`) peut avoir été retouché pendant les
    secondes de l'appel ; on relit, on applique la mutation à l'état relu, et
    on n'écrit que si rien n'a bougé entre la relecture et l'écriture
    (`DossierConflictError` sinon : l'app réessaie, rien n'est écrasé).
    """
    current = await _owned(dossiers, seen.id, user_id)
    updated = mutate(current)
    if not await dossiers.save_if_unchanged(updated, expected_updated_at=current.updated_at):
        raise DossierConflictError(seen.id)
    return updated


async def _owned(dossiers: DossierRepository, dossier_id: UUID, user_id: UUID) -> Dossier:
    dossier = await dossiers.find(dossier_id, user_id=user_id)
    if dossier is None:
        raise DossierNotFoundError(dossier_id)
    return dossier
