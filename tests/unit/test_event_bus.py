"""Le bus doit isoler l'émetteur des défaillances de ses abonnés."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from karria.shared.events import DomainEvent, EventBus

pytestmark = pytest.mark.unit


class ThingHappened(DomainEvent):
    label: str


class OtherThingHappened(DomainEvent):
    pass


async def test_les_abonnes_recoivent_levenement() -> None:
    bus = EventBus()
    recus: list[str] = []

    async def handler(event: ThingHappened) -> None:
        recus.append(event.label)

    bus.subscribe(ThingHappened, handler)
    await bus.publish(ThingHappened(label="ok"))

    assert recus == ["ok"]


async def test_un_abonne_en_echec_ninterrompt_pas_les_autres() -> None:
    """Garantie centrale : publier un fait ne peut pas casser l'émetteur."""
    bus = EventBus()
    appele: list[str] = []

    async def defaillant(_event: ThingHappened) -> None:
        raise RuntimeError("panne d'un abonné")

    async def sain(_event: ThingHappened) -> None:
        appele.append("sain")

    bus.subscribe(ThingHappened, defaillant)
    bus.subscribe(ThingHappened, sain)

    await bus.publish(ThingHappened(label="x"))  # ne doit pas lever

    assert appele == ["sain"]


async def test_les_evenements_sont_routes_par_type() -> None:
    bus = EventBus()
    appels: list[str] = []

    async def handler(_event: ThingHappened) -> None:
        appels.append("appelé")

    bus.subscribe(ThingHappened, handler)
    await bus.publish(OtherThingHappened())

    assert appels == []


def test_un_evenement_est_immuable() -> None:
    """Un fait passé ne se réécrit pas."""
    event = ThingHappened(label="figé")

    with pytest.raises(ValidationError):
        event.label = "modifié"
