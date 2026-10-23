"""`UpdateKarriaMemory`, `GetKarriaMemory`, `ResetKarriaMemory` : ce que Karria retient, et l'oubli."""

from __future__ import annotations

from uuid import uuid4

import pytest

from karria.modules.offers.application import (
    GetKarriaMemory,
    ResetKarriaMemory,
    UpdateKarriaMemory,
)
from karria.modules.offers.domain import KarriaMemory, MemoryFacts
from offers.fakes import NOW, FakeMemoryUpdater, FixedClock, InMemoryMemoryRepository

pytestmark = pytest.mark.unit

USER = uuid4()


def update(repository: InMemoryMemoryRepository, updater: FakeMemoryUpdater) -> UpdateKarriaMemory:
    return UpdateKarriaMemory(
        memory=repository, updater=updater, clock=FixedClock(), constitution_version="1.0"
    )


async def test_un_premier_tour_cree_la_memoire_sous_la_constitution_courante() -> None:
    repository = InMemoryMemoryRepository()
    updater = FakeMemoryUpdater(MemoryFacts(target_jobs=["serveur"], summary="Cherche en salle."))

    memory = await update(repository, updater).execute(
        USER, user_message="je cherche serveur à Lyon", reply="C'est noté."
    )

    assert memory is not None
    assert memory.facts.target_jobs == ["serveur"]
    assert (memory.constitution_version, memory.updated_at) == ("1.0", NOW)
    assert repository.memories[USER] == memory
    assert updater.calls[0] == (MemoryFacts(), "je cherche serveur à Lyon", "C'est noté.")


async def test_un_tour_suivant_fusionne_avec_le_connu() -> None:
    repository = InMemoryMemoryRepository()
    repository.memories[USER] = KarriaMemory(facts=MemoryFacts(target_jobs=["serveur"]))
    updater = FakeMemoryUpdater(MemoryFacts(constraints=["temps plein"]))

    memory = await update(repository, updater).execute(
        USER, user_message="pas de temps partiel", reply="Ok."
    )

    assert memory is not None
    assert memory.facts.target_jobs == ["serveur"]
    assert memory.facts.constraints == ["temps plein"]
    assert updater.calls[0][0].target_jobs == ["serveur"]


async def test_rien_de_nouveau_ne_touche_pas_la_memoire() -> None:
    repository = InMemoryMemoryRepository()
    for proposed in (None, MemoryFacts()):
        assert (
            await update(repository, FakeMemoryUpdater(proposed)).execute(
                USER, user_message="merci", reply="Avec plaisir."
            )
            is None
        )
    assert repository.memories == {}


async def test_lecture_et_effacement() -> None:
    repository = InMemoryMemoryRepository()
    assert await GetKarriaMemory(memory=repository).execute(USER) is None
    repository.memories[USER] = KarriaMemory(facts=MemoryFacts(summary="x"))

    assert await GetKarriaMemory(memory=repository).execute(USER) is not None
    assert await ResetKarriaMemory(memory=repository).execute(USER) is True
    assert await ResetKarriaMemory(memory=repository).execute(USER) is False
    assert await GetKarriaMemory(memory=repository).execute(USER) is None
