"""L'atterrissage des lots : écrit tel quel, relu ligne à ligne."""

from __future__ import annotations

import gzip
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from karria.modules.offers.domain import BatchRejectedError
from karria.modules.offers.infrastructure import ObjectStorageLandingStore
from karria.modules.offers.infrastructure.landing import decode_batch
from karria.shared.config import Environment, StorageSettings
from karria.shared.storage import Bucket
from karria.shared.storage.memory import InMemoryObjectStorage

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def gz(text: str) -> bytes:
    return gzip.compress(text.encode())


class TestDecodage:
    def test_les_lignes_json_objets_sont_rendues_les_autres_sautees(self) -> None:
        records = decode_batch(gz('{"a": 1}\n\n[1, 2]\npas du json\n{"b": 2}\n'))

        assert records == [{"a": 1}, {"b": 2}]

    def test_un_lot_vide_ne_contient_rien(self) -> None:
        assert decode_batch(gz("")) == []

    @pytest.mark.parametrize("payload", [b"pas du gzip", gzip.compress(b"\xff\xfe")])
    def test_un_lot_illisible_est_refuse(self, payload: bytes) -> None:
        with pytest.raises(BatchRejectedError) as error:
            decode_batch(payload)

        assert error.value.details["reason"] == "batch_unreadable"


class TestStockage:
    async def test_le_lot_s_ecrit_tel_quel_et_se_relit(self) -> None:
        storage = InMemoryObjectStorage(StorageSettings(), Environment.TEST.value)
        landing = ObjectStorageLandingStore(storage)
        payload = gz('{"source": "indeed"}\n')

        key = await landing.store(uuid4(), payload, at=NOW)

        assert key.startswith("collector/")
        assert key.endswith(".gz")
        assert await storage.get(Bucket.LANDING, key) == payload
        assert await landing.read(key) == [{"source": "indeed"}]
