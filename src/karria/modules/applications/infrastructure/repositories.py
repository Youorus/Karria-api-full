"""Dépôt Tortoise du module `applications`. Ne rend jamais un modèle."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from karria.modules.applications.domain import (
    AdaptationState,
    Coverage,
    Dossier,
    DossierNotFoundError,
    Letter,
    OfferSnapshot,
    Proposition,
)
from karria.modules.applications.infrastructure.models import ApplicationDossierModel


def _to_dossier(row: ApplicationDossierModel) -> Dossier:
    return Dossier(
        id=row.id,
        user_id=row.user_id,
        offer=OfferSnapshot.from_json(row.offer_snapshot),
        template_id=row.template_id,
        cv=dict(row.cv),
        cv_base_hash=row.cv_base_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
        propositions=tuple(Proposition.from_json(p) for p in row.propositions or []),
        letter=Letter.from_json(row.letter) if row.letter else None,
        coverage=Coverage.from_json(row.coverage),
        adaptation=AdaptationState(row.adaptation),
        adaptation_error=row.adaptation_error,
        applied_at=row.applied_at,
    )


def _columns(dossier: Dossier) -> dict[str, Any]:
    return {
        "user_id": dossier.user_id,
        "offer_id": dossier.offer.offer_id,
        "offer_snapshot": dossier.offer.as_json(),
        "template_id": dossier.template_id,
        "cv": dossier.cv,
        "cv_base_hash": dossier.cv_base_hash,
        "propositions": [p.as_json() for p in dossier.propositions],
        "letter": dossier.letter.as_json() if dossier.letter else None,
        "coverage": dossier.coverage.as_json(),
        "status": dossier.status.value,
        "adaptation": dossier.adaptation.value,
        "adaptation_error": dossier.adaptation_error,
        "applied_at": dossier.applied_at,
        "updated_at": dossier.updated_at,
    }


class TortoiseDossierRepository:
    """Implémente `DossierRepository` sur PostgreSQL."""

    async def find(self, dossier_id: UUID, *, user_id: UUID) -> Dossier | None:
        row = await ApplicationDossierModel.get_or_none(id=dossier_id, user_id=user_id)
        return None if row is None else _to_dossier(row)

    async def find_any(self, dossier_id: UUID) -> Dossier | None:
        row = await ApplicationDossierModel.get_or_none(id=dossier_id)
        return None if row is None else _to_dossier(row)

    async def find_by_offer(self, user_id: UUID, offer_id: UUID) -> Dossier | None:
        row = await ApplicationDossierModel.get_or_none(user_id=user_id, offer_id=offer_id)
        return None if row is None else _to_dossier(row)

    async def list_by_user(self, user_id: UUID, *, applied_only: bool) -> list[Dossier]:
        query = ApplicationDossierModel.filter(user_id=user_id)
        if applied_only:
            query = query.filter(applied_at__isnull=False)
        rows = await query.order_by("-updated_at", "-id")
        return [_to_dossier(row) for row in rows]

    async def insert(self, dossier: Dossier) -> None:
        """Crée ; `created_at` et `updated_at` sont ceux du domaine, pas ceux de l'ORM."""
        await ApplicationDossierModel.create(id=dossier.id, **_columns(dossier))
        # À la création, `auto_now_add` / `auto_now` posent l'instant de la base ;
        # on remet ceux du domaine pour que les deux horodatages soient les siens.
        await ApplicationDossierModel.filter(id=dossier.id).update(
            created_at=dossier.created_at, updated_at=dossier.updated_at
        )

    async def save(self, dossier: Dossier) -> None:
        """Remplace, par `update()` (jamais `save()` : `auto_now` écraserait `updated_at`).

        Zéro ligne : le dossier a été supprimé pendant qu'on y travaillait ; on
        ne le recrée pas — l'effacement l'emporte (`DossierNotFoundError`).
        """
        updated = await ApplicationDossierModel.filter(id=dossier.id).update(**_columns(dossier))
        if updated == 0:
            raise DossierNotFoundError(dossier.id)

    async def save_if_unchanged(self, dossier: Dossier, *, expected_updated_at: datetime) -> bool:
        """Le contrôle optimiste : n'écrit que si personne n'a écrit depuis `expected_updated_at`."""
        updated = await ApplicationDossierModel.filter(
            id=dossier.id, updated_at=expected_updated_at
        ).update(**_columns(dossier))
        return updated == 1

    async def delete(self, dossier_id: UUID) -> None:
        await ApplicationDossierModel.filter(id=dossier_id).delete()

    async def delete_by_user(self, user_id: UUID) -> int:
        return await ApplicationDossierModel.filter(user_id=user_id).delete()
