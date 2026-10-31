"""La résolution d'un jeton nu (WebSockets) : refus par défaut, lecture de l'en-tête."""

from __future__ import annotations

import pytest

from karria.shared.auth import RefusingTokenResolver, bearer_token, get_token_resolver

pytestmark = pytest.mark.unit


async def test_par_defaut_aucun_jeton_n_est_reconnu() -> None:
    resolver = get_token_resolver()
    assert isinstance(resolver, RefusingTokenResolver)
    assert await resolver.resolve("n-importe-quoi") is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer  abc ", "abc"),
        ("Basic abc", None),
        ("Bearer", None),
        ("Bearer   ", None),
        (None, None),
    ],
)
def test_le_jeton_se_lit_dans_l_en_tete_bearer(header: str | None, expected: str | None) -> None:
    assert bearer_token(header) == expected
