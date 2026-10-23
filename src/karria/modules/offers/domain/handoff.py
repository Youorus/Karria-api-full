"""Le contrat de remise entre le collecteur et l'API : une annonce brute, en JSON.

Le collecteur (dépôt à part : JobSpy, mandataires, cadence, analyse propre
à chaque site) ne connaît de l'API que deux choses : la liste des cibles à
collecter, et la forme d'une annonce à remettre. Cette forme est ici, et
la copie conforme côté collecteur est `karria/collector/contract.py`.
Le document qui fait foi pour les deux : `docs/collecteur.md`.

Une annonce remise est un objet JSON par ligne (JSONL, compressé gzip),
dont les clés sont celles de `RawPosting`. Tout ce qui n'est pas reconnu
est ignoré ; une ligne inexploitable est écartée, jamais devinée — le
collecteur peut évoluer (ajouter un champ, un site) sans casser l'API.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, Final

from karria.modules.offers.domain.normalization import RawPosting
from karria.modules.offers.domain.offers import Source

#: Version du contrat. Une version que l'API ne connaît pas est refusée en
#: bloc : mieux vaut un lot rejeté et visible qu'un lot mal compris.
HANDOFF_VERSION: Final = 1
#: Annonces au plus par lot remis : une page de collecte, avec de la marge.
BATCH_MAX_POSTINGS: Final = 2_000
#: Taille maximale d'un lot compressé.
BATCH_MAX_BYTES: Final = 16 * 1024 * 1024
_TEXT_MAX: Final = 60_000


def _text(record: Mapping[str, Any], key: str, limit: int = 2_048) -> str | None:
    value = record.get(key)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] or None


def _number(record: Mapping[str, Any], key: str) -> float | None:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _day(record: Mapping[str, Any]) -> date | None:
    value = record.get("date_posted")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _moment(record: Mapping[str, Any], key: str) -> datetime | None:
    """Un instant ISO 8601 (`Z` accepté), ramené en UTC ; `None` s'il est illisible ou naïf."""
    value = record.get(key)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def posting_from_record(record: Mapping[str, Any]) -> RawPosting | None:
    """Une ligne du lot → une annonce brute, ou `None` si elle est inexploitable.

    Il faut au moins une source connue, un identifiant et un titre ; le
    reste est facultatif et lu défensivement (un type inattendu vaut absent).
    Une ligne `expired: true` n'a besoin que de la source et de l'identifiant :
    elle dit que la source a retiré l'annonce.
    """
    try:
        source = Source(str(record.get("source")))
    except ValueError:
        return None
    source_id = _text(record, "source_id", 255)
    expired = record.get("expired") is True
    # Une annonce retirée n'a pas besoin de titre : son identifiant suffit.
    title = _text(record, "title", 1_000) or ("" if expired else None)
    if source_id is None or title is None or source is Source.MANUAL:
        return None
    job_types = record.get("job_types")
    is_remote = record.get("is_remote")
    raw = record.get("raw")
    posted_at = _moment(record, "posted_at")
    return RawPosting(
        source=source,
        source_id=source_id,
        title=title,
        description=_text(record, "description", _TEXT_MAX) or "",
        company_name=_text(record, "company_name", 255),
        company_logo_url=_text(record, "company_logo_url"),
        company_url=_text(record, "company_url"),
        location_label=_text(record, "location_label", 255),
        date_posted=_day(record),
        job_types=tuple(t for t in job_types if isinstance(t, str))
        if isinstance(job_types, list)
        else (),
        is_remote=is_remote if isinstance(is_remote, bool) else None,
        work_from_home_type=_text(record, "work_from_home_type", 64),
        job_level=_text(record, "job_level", 64),
        salary_min=_number(record, "salary_min"),
        salary_max=_number(record, "salary_max"),
        salary_interval=_text(record, "salary_interval", 16),
        salary_currency=_text(record, "salary_currency", 8),
        job_url=_text(record, "job_url"),
        job_url_direct=_text(record, "job_url_direct"),
        contract_type=_text(record, "contract_type", 16),
        schedule=_text(record, "schedule", 16),
        experience=_text(record, "experience", 16),
        experience_years=_number(record, "experience_years"),
        salary_text=_text(record, "salary_text", 255),
        latitude=_number(record, "latitude"),
        longitude=_number(record, "longitude"),
        postal_code=_text(record, "postal_code", 10),
        posted_at=posted_at,
        updated_at=_moment(record, "updated_at"),
        expired=expired,
        raw=dict(raw) if isinstance(raw, dict) else {},
    )
