"""Dépôt Tortoise du module `applications`. Ne rend jamais un modèle."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from karria.modules.applications.domain import (
    AdaptationState,
    Coverage,
    Dossier,
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

    async def save(self, dossier: Dossier) -> None:
        """Crée ou remplace ; `updated_at` est celui du domaine, pas celui de l'ORM."""
        columns = _columns(dossier)
        updated = await ApplicationDossierModel.filter(id=dossier.id).update(**columns)
        if updated == 0:
            # `update()` et non `save()` : `auto_now` écraserait `updated_at`, dont le
            # cas d'usage se sert pour savoir si la personne a touché le dossier
            # pendant l'adaptation.
            await ApplicationDossierModel.create(id=dossier.id, **columns)
            # À la création, `auto_now_add` / `auto_now` posent l'instant de la base ;
            # on remet ceux du domaine pour que les deux horodatages soient les siens.
            await ApplicationDossierModel.filter(id=dossier.id).update(
                created_at=dossier.created_at, updated_at=dossier.updated_at
            )

    async def delete(self, dossier_id: UUID) -> None:
        await ApplicationDossierModel.filter(id=dossier_id).delete()

    async def delete_by_user(self, user_id: UUID) -> int:
        return await ApplicationDossierModel.filter(user_id=user_id).delete()
