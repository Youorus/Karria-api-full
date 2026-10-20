"""Le client des appels sortants : reprise, erreurs, décodage."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest

from karria.shared.http.outbound import (
    RETRYABLE_STATUSES,
    JsonClient,
    OutboundError,
)


class _Reponse:
    """Ce que `urlopen` rend, réduit à ce dont le client se sert."""

    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self._corps = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._corps

    def __enter__(self) -> _Reponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _http_error(code: int, corps: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://exemple.test", code=code, msg="", hdrs=None, fp=io.BytesIO(corps.encode())
    )


class TestLesAppelsQuiAboutissent:
    @pytest.mark.asyncio
    async def test_rend_le_corps_decode(self) -> None:
        client = JsonClient(base_url="https://exemple.test")

        with patch("urllib.request.urlopen", return_value=_Reponse({"ok": True})):
            reponse = await client.post_json("/envoi", {"a": 1})

        assert reponse.mapping == {"ok": True}

    @pytest.mark.asyncio
    async def test_une_reponse_qui_n_est_pas_un_objet_leve(self) -> None:
        """Un fournisseur qui rend une liste là où l'on attend un objet est
        une panne, pas un cas à traiter : la lever ici évite un
        `AttributeError` trois couches plus loin."""
        client = JsonClient(base_url="https://exemple.test")

        with patch("urllib.request.urlopen", return_value=_Reponse([1, 2])):
            reponse = await client.post_json("/envoi", {})
            with pytest.raises(OutboundError):
                _ = reponse.mapping


class TestLaReprise:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", sorted(RETRYABLE_STATUSES))
    async def test_retente_ce_qui_a_une_chance(self, code: int) -> None:
        """429 dit « trop vite », les 5xx disent « pas maintenant »."""
        client = JsonClient(base_url="https://exemple.test", attempts=2)
        appels: list[int] = []

        def _urlopen(*_: object, **__: object) -> _Reponse:
            appels.append(1)
            if len(appels) == 1:
                raise _http_error(code)
            return _Reponse({"ok": True})

        with (
            patch("urllib.request.urlopen", side_effect=_urlopen),
            patch("asyncio.sleep"),
        ):
            reponse = await client.post_json("/envoi", {})

        assert len(appels) == 2
        assert reponse.mapping == {"ok": True}

    @pytest.mark.asyncio
    async def test_ne_retente_pas_ce_qui_echouera_pareil(self) -> None:
        """LE test de la règle de reprise.

        Retenter un 400 ne fait que répéter la même erreur en la
        retardant — et sur un envoi non idempotent, cela livrerait deux
        fois.
        """
        client = JsonClient(base_url="https://exemple.test", attempts=3)
        appels: list[int] = []

        def _urlopen(*_: object, **__: object) -> _Reponse:
            appels.append(1)
            raise _http_error(400, "jeton invalide")

        with (
            patch("urllib.request.urlopen", side_effect=_urlopen),
            pytest.raises(OutboundError) as capture,
        ):
            await client.post_json("/envoi", {})

        assert len(appels) == 1
        assert capture.value.status == 400
        # Le corps de l'erreur porte souvent le motif exact : le jeter
        # laisserait « HTTP 400 » pour seule explication.
        assert "jeton invalide" in capture.value.detail

    @pytest.mark.asyncio
    async def test_une_panne_reseau_est_retentee(self) -> None:
        """Le cas qui se rattrape le mieux : la requête n'a pas abouti."""
        client = JsonClient(base_url="https://exemple.test", attempts=2)
        appels: list[int] = []

        def _urlopen(*_: object, **__: object) -> _Reponse:
            appels.append(1)
            if len(appels) == 1:
                raise urllib.error.URLError("coupure")
            return _Reponse({"ok": True})

        with (
            patch("urllib.request.urlopen", side_effect=_urlopen),
            patch("asyncio.sleep"),
        ):
            await client.post_json("/envoi", {})

        assert len(appels) == 2

    @pytest.mark.asyncio
    async def test_l_attente_double_a_chaque_tentative(self) -> None:
        """Un délai fixe fait repartir tous les clients ensemble après une
        panne, et achève le service qui vient de se relever."""
        client = JsonClient(base_url="https://exemple.test", attempts=3)
        attentes: list[float] = []

        async def _sleep(duree: float) -> None:
            attentes.append(duree)

        with (
            patch("urllib.request.urlopen", side_effect=_http_error(503)),
            patch("asyncio.sleep", side_effect=_sleep),
            pytest.raises(OutboundError),
        ):
            await client.post_json("/envoi", {})

        assert attentes == [0.5, 1.0]

    @pytest.mark.asyncio
    async def test_une_seule_tentative_desactive_la_reprise(self) -> None:
        """Le bon réglage pour un appel non idempotent."""
        client = JsonClient(base_url="https://exemple.test", attempts=1)
        appels: list[int] = []

        def _urlopen(*_: object, **__: object) -> _Reponse:
            appels.append(1)
            raise _http_error(503)

        with patch("urllib.request.urlopen", side_effect=_urlopen), pytest.raises(OutboundError):
            await client.post_json("/envoi", {})

        assert len(appels) == 1


class TestLaComposition:
    @pytest.mark.asyncio
    async def test_l_url_se_compose_sans_double_barre(self) -> None:
        vues: list[str] = []

        def _urlopen(requete: Any, **__: object) -> _Reponse:
            vues.append(requete.full_url)
            return _Reponse({})

        client = JsonClient(base_url="https://exemple.test/")
        with patch("urllib.request.urlopen", side_effect=_urlopen):
            await client.post_json("/envoi", {})

        assert vues == ["https://exemple.test/envoi"]

    @pytest.mark.asyncio
    async def test_les_parametres_sont_encodes(self) -> None:
        vues: list[str] = []

        def _urlopen(requete: Any, **__: object) -> _Reponse:
            vues.append(requete.full_url)
            return _Reponse({})

        client = JsonClient(base_url="https://exemple.test")
        with patch("urllib.request.urlopen", side_effect=_urlopen):
            await client.get_json("/recherche", {"q": "Paris & Douala"})

        assert "q=Paris+%26+Douala" in vues[0]

    @pytest.mark.asyncio
    async def test_un_corps_illisible_leve(self) -> None:
        class _Illisible(_Reponse):
            def read(self) -> bytes:
                return b"<html>erreur</html>"

        client = JsonClient(base_url="https://exemple.test")
        with (
            patch("urllib.request.urlopen", return_value=_Illisible({})),
            pytest.raises(OutboundError, match="illisible"),
        ):
            await client.post_json("/envoi", {})
