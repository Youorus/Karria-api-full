"""L'atterrissage des lots remis par le collecteur : tels quels, dans le bucket `landing`.

Le lot arrive en JSONL compressé (le contrat de `domain/handoff.py`) et
s'écrit **sans être décompressé ni réécrit** : l'objet gardé est exactement
ce que le collecteur a envoyé, ce qui est la condition pour rejouer une
normalisation corrigée. Pourquoi pas Parquet : il faudrait `pyarrow`
(quarante mégaoctets) pour des fichiers que personne ne requête en
colonnes ; DuckDB lit du JSONL le jour où on veut l'analyser en masse.
"""

from __future__ import annotations

import gzip
import json
import zlib
from datetime import datetime
from typing import Any
from uuid import UUID

from karria.modules.offers.domain import BatchRejectedError
from karria.shared.logging import get_logger
from karria.shared.storage import Bucket, ObjectStorage, build_key

logger = get_logger(__name__)


def decode_batch(payload: bytes) -> list[dict[str, Any]]:
    """Un lot gzip → ses lignes JSON (objets seulement ; le reste est sauté)."""
    try:
        text = gzip.decompress(payload).decode("utf-8")
    except (OSError, EOFError, zlib.error, UnicodeDecodeError) as exc:
        raise BatchRejectedError("batch_unreadable") from exc
    records: list[dict[str, Any]] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            skipped += 1
    if skipped:
        logger.warning("offers.batch_lines_skipped", skipped=skipped)
    return records


class ObjectStorageLandingStore:
    """Implémente `LandingStore` sur le stockage objet du socle."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    async def store(self, batch_id: UUID, payload: bytes, *, at: datetime) -> str:
        """Écrit le lot tel quel et rend sa clé."""
        key = build_key("collector", batch_id.hex, extension="gz", at=at)
        await self._storage.put(Bucket.LANDING, key, payload, content_type="application/gzip")
        return key

    async def read(self, key: str) -> list[dict[str, Any]]:
        """Relit et décode."""
        return decode_batch(await self._storage.get(Bucket.LANDING, key))
