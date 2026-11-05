"""Le dépôt des dossiers sur PostgreSQL : aller-retour complet, unicité par offre, oubli."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from doubles import sample_adaptation_output, sample_base_cv, sample_letter_json, sample_offer_card
from karria.modules.applications.application import snapshot_of
from karria.modules.applications.domain import (
    Dossier,
    DossierNotFoundError,
    Letter,
    propositions_from,
)
from karria.modules.applications.infrastructure import TortoiseDossierRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def build(user_id: object | None = None) -> Dossier:
    dossier = Dossier.open(
        user_id=user_id or uuid4(),  # type: ignore[arg-type]
        offer=snapshot_of(sample_offer_card()),
        base_cv=sample_base_cv(),
        template_id=uuid4(),
        now=NOW,
    )
    conversion = propositions_from(sample_adaptation_output(), dossier.cv)
    return dossier.adapted(conversion.propositions, now=NOW).with_letter(
        Letter.validated(sample_letter_json()), now=NOW
    )


@pytest.mark.usefixtures("clean_database")
async def test_aller_retour_complet_et_horodatages_du_domaine() -> None:
    repository = TortoiseDossierRepository()
    dossier = build()

    await repository.insert(dossier)
    found = await repository.find(dossier.id, user_id=dossier.user_id)

    assert found == dossier
    assert await repository.find(dossier.id, user_id=uuid4()) is None
    assert await repository.find_any(dossier.id) == dossier
    assert await repository.find_by_offer(dossier.user_id, dossier.offer.offer_id) == dossier


@pytest.mark.usefixtures("clean_database")
async def test_sauver_remplace_et_liste_trie_par_modification() -> None:
    repository = TortoiseDossierRepository()
    user = uuid4()
    first = build(user)
    second = build(user)
    await repository.insert(first)
    await repository.insert(second)
    applied = first.mark_applied(now=datetime(2026, 11, 6, tzinfo=UTC))
    await repository.save(applied)

    everything = await repository.list_by_user(user, applied_only=False)
    only_applied = await repository.list_by_user(user, applied_only=True)

    assert [d.id for d in everything] == [first.id, second.id]
    assert [d.id for d in only_applied] == [first.id]
    assert everything[0].applied_at == applied.applied_at


@pytest.mark.usefixtures("clean_database")
async def test_supprimer_et_oublier() -> None:
    repository = TortoiseDossierRepository()
    user = uuid4()
    one, two = build(user), build(user)
    await repository.insert(one)
    await repository.insert(two)

    await repository.delete(one.id)
    await repository.delete(one.id)
    assert await repository.find_any(one.id) is None
    assert await repository.delete_by_user(user) == 1
    assert await repository.list_by_user(user, applied_only=False) == []


@pytest.mark.usefixtures("clean_database")
async def test_un_dossier_supprime_ne_renait_pas_et_le_controle_optimiste_tient() -> None:
    repository = TortoiseDossierRepository()
    dossier = build()
    await repository.insert(dossier)

    later = datetime(2026, 11, 5, 11, 0, tzinfo=UTC)
    touched = dossier.with_cv({**dossier.cv, "summary": "Retouché"}, now=later)
    assert await repository.save_if_unchanged(touched, expected_updated_at=dossier.updated_at)
    # L'écriture concurrente, calculée sur l'état d'avant, est refusée.
    stale = dossier.mark_applied(now=later)
    assert not await repository.save_if_unchanged(stale, expected_updated_at=dossier.updated_at)
    found = await repository.find_any(dossier.id)
    assert found is not None
    assert found.cv["summary"] == "Retouché"
    assert found.applied_at is None

    await repository.delete(dossier.id)
    with pytest.raises(DossierNotFoundError):
        await repository.save(touched)
    assert not await repository.save_if_unchanged(touched, expected_updated_at=later)
    assert await repository.find_any(dossier.id) is None
