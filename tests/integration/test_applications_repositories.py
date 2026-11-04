"""Le dépôt des dossiers sur PostgreSQL : aller-retour complet, unicité par offre, oubli."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from doubles import sample_adaptation_output, sample_base_cv, sample_letter_json, sample_offer_card
from karria.modules.applications.application import snapshot_of
from karria.modules.applications.domain import Dossier, Letter, propositions_from
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

    await repository.save(dossier)
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
    await repository.save(first)
    await repository.save(second)
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
    await repository.save(one)
    await repository.save(two)

    await repository.delete(one.id)
    await repository.delete(one.id)
    assert await repository.find_any(one.id) is None
    assert await repository.delete_by_user(user) == 1
    assert await repository.list_by_user(user, applied_only=False) == []
