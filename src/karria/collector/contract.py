"""Le format de remise, côté collecteur — miroir de `offers/domain/handoff.py`.

Les deux fichiers décrivent le même contrat depuis chaque rive ; un test
(`tests/unit/test_collector_contract.py`) vérifie qu'une annonce mise au
format ici est lue à l'identique là-bas. Le document qui fait foi :
`docs/collecteur.md`.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Mapping
from typing import Any, Final

HANDOFF_VERSION: Final = 1

#: Les clés d'une annonce remise. `source`, `source_id` et `title` sont
#: obligatoires ; les autres facultatives.
FIELDS: Final[tuple[str, ...]] = (
    "source",
    "source_id",
    "title",
    "description",
    "company_name",
    "company_logo_url",
    "company_url",
    "company_website",
    "location_label",
    "date_posted",
    "job_types",
    "is_remote",
    "work_from_home_type",
    "job_level",
    "salary_min",
    "salary_max",
    "salary_interval",
    "salary_currency",
    "job_url",
    "job_url_direct",
    "contract_type",
    "schedule",
    "experience",
    "experience_years",
    "salary_text",
    "latitude",
    "longitude",
    "postal_code",
    "posted_at",
    "updated_at",
    "expired",
    "raw",
)
#: Annonces au plus par lot remis — la même borne que l'API (`BATCH_MAX_POSTINGS`).
BATCH_MAX_POSTINGS: Final = 2_000


def encode_batch(records: Iterable[Mapping[str, Any]]) -> bytes:
    """Une ligne JSON par annonce, compressée gzip."""
    lines = [
        json.dumps({k: record[k] for k in FIELDS if k in record}, ensure_ascii=False, default=str)
        for record in records
    ]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8") if lines else b"")
