"""L'oubli d'un compte supprimé va jusqu'au bout (audit de sécurité du 2026-11-20)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from doubles import FakeClock, InMemoryPendingForgetRepository
from karria.modules.identity.application import ReplayForgets

NOW = datetime(2026, 11, 20, 21, 0, tzinfo=UTC)


class TestRejeuDesOublis:
    async def test_un_oubli_en_panne_reste_inscrit_puis_aboutit(self) -> None:
        forgets = InMemoryPendingForgetRepository()
        gone, stuck = uuid4(), uuid4()
        await forgets.add(gone, NOW - timedelta(minutes=10))
        await forgets.add(stuck, NOW - timedelta(minutes=10))
        down = {stuck}
        seen: list[UUID] = []

        async def forget_everywhere(user_id: UUID) -> None:
            seen.append(user_id)
            if user_id in down:
                raise RuntimeError("payments: StripeError")

        replay = ReplayForgets(forgets, forget_everywhere, FakeClock(NOW))

        assert await replay.execute() == (1, 1)
        assert list(forgets.rows) == [stuck]  # rayé seulement si tout a réussi
        assert forgets.errors[stuck] == ["RuntimeError: payments: StripeError"]

        down.clear()  # Stripe répond de nouveau
        assert await replay.execute() == (1, 0)
        assert forgets.rows == {}
        assert seen == [gone, stuck, stuck]

    async def test_un_compte_tout_juste_supprime_laisse_passer_la_reaction_immediate(self) -> None:
        forgets = InMemoryPendingForgetRepository()
        await forgets.add(uuid4(), NOW - timedelta(seconds=30))

        async def forget_everywhere(user_id: UUID) -> None:
            raise AssertionError("trop tôt")

        assert await ReplayForgets(forgets, forget_everywhere, FakeClock(NOW)).execute() == (0, 0)
