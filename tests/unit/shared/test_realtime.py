"""Le bus de réveils : fondu des réveils, délai rendu `False`, désabonnement."""

from __future__ import annotations

import asyncio

import pytest

from karria.shared.config import DatabaseSettings
from karria.shared.realtime import InMemoryBroker, PostgresBroker, build_broker

pytestmark = pytest.mark.unit


async def test_un_reveil_arrive_a_tous_les_abonnes_du_sujet() -> None:
    broker = InMemoryBroker()
    async with (
        broker.subscribe("support:a") as un,
        broker.subscribe("support:a") as deux,
        broker.subscribe("support:b") as autre,
    ):
        await broker.publish("support:a")
        assert await un.wait(0.1) is True
        assert await deux.wait(0.1) is True
        assert await autre.wait(0.05) is False
    assert broker.subscribers("support:a") == 0
    assert broker.published == ["support:a"]


async def test_plusieurs_reveils_pendant_une_lecture_n_en_font_qu_un() -> None:
    broker = InMemoryBroker()
    async with broker.subscribe("t") as sub:
        await broker.publish("t")
        await broker.publish("t")
        assert await sub.wait(0.1) is True
        assert await sub.wait(0.05) is False


async def test_le_delai_rend_faux_sans_lever() -> None:
    broker = InMemoryBroker()
    async with broker.subscribe("t") as sub:
        assert await asyncio.wait_for(sub.wait(0.02), 1) is False


def test_la_fabrique_choisit_postgres_par_defaut_et_la_memoire_sur_demande() -> None:
    settings = DatabaseSettings()
    assert isinstance(build_broker(settings), PostgresBroker)
    assert isinstance(build_broker(settings, in_memory=True), InMemoryBroker)


async def test_postgres_s_abonne_meme_si_l_ecoute_echoue() -> None:
    """Sans base joignable, l'abonné relit au délai : dégradé, pas cassé."""
    broker = PostgresBroker("postgres://karria:karria@127.0.0.1:1/karria")
    async with broker.subscribe("t") as sub:
        assert await sub.wait(0.02) is False
    await broker.close()
