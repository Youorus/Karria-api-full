"""Les sites d'offres, par JobSpy : une requête → des annonces au format de remise.

JobSpy est synchrone et rend un `DataFrame` ; l'appel part dans un fil pour
ne pas retenir la boucle, chaque site séparément : la panne de l'un ne
prive pas des autres. Les lignes sont mises au format de remise ici et
nulle part ailleurs — le reste du collecteur ignore pandas.

Deux particularités de JobSpy 1.4.0, vérifiées dans son code :

- sans `fetch_description=True`, HelloWork ne rend qu'un titre et une
  ville (une requête de plus par annonce, mais une annonce lisible) ;
- JobSpy compte les rayons en **miles** ;
- HelloWork ne connaît pas « France » comme lieu (mesuré le 2026-10-22 :
  zéro résultat en 46 s) ; sans lieu, il cherche dans tout le pays.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

#: Le pays d'Indeed : JobSpy veut un nom, pas un code.
COUNTRY: Final = "france"
#: Le lieu « tout le pays » tel que l'API le donne.
NATIONWIDE: Final = "france"
KM_PER_MILE: Final = 1.609344
#: Colonnes gardées dans `raw` (pour rejouer une normalisation côté API).
RAW_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "job_type",
    "salary_source",
    "interval",
    "min_amount",
    "max_amount",
    "currency",
    "is_remote",
    "job_level",
    "job_function",
    "listing_type",
    "company_industry",
    "company_url",
    "company_logo",
    "company_url_direct",
    "skills",
    "experience_range",
    "work_from_home_type",
)


@dataclass(frozen=True, slots=True)
class Target:
    """Une requête louée par l'API."""

    key: str
    search_term: str
    location: str
    distance_km: int | None
    #: Ce que le dernier passage avait trouvé : zéro aujourd'hui alors que
    #: c'était beaucoup hier signale une page qui a changé, pas un métier vide.
    last_found: int = 0


def clean(value: Any) -> Any:  # noqa: PLR0911 - un cas par famille de valeur pandas
    """Une valeur pandas en valeur JSON : `NaN`/`NaT` → `None`, dates → ISO."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [clean(item) for item in value]
    if hasattr(value, "item"):  # scalaires numpy
        try:
            return clean(value.item())
        except (ValueError, AttributeError):
            return str(value)
    return str(value)


def _text(value: Any) -> str | None:
    cleaned = clean(value)
    if cleaned is None:
        return None
    text = str(cleaned).strip()
    return text or None


def _number(value: Any) -> float | None:
    cleaned = clean(value)
    if cleaned is None or isinstance(cleaned, bool):
        return None
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def row_to_record(row: dict[str, Any], site: str) -> dict[str, Any] | None:
    """Une ligne JobSpy → une annonce au format de remise. `None` sans identifiant ni titre."""
    source_id = _text(row.get("id"))
    title = _text(row.get("title"))
    if not source_id or not title:
        return None
    job_types = [t.strip() for t in str(_text(row.get("job_type")) or "").split(",") if t.strip()]
    is_remote = clean(row.get("is_remote"))
    posted = _text(row.get("date_posted"))
    return {
        "source": site,
        "source_id": source_id,
        "title": title,
        "description": _text(row.get("description")) or "",
        "company_name": _text(row.get("company")),
        "company_logo_url": _text(row.get("company_logo")),
        "company_url": _text(row.get("company_url")),
        "location_label": _text(row.get("location")),
        "date_posted": posted[:10] if posted else None,
        "job_types": job_types,
        "is_remote": is_remote if isinstance(is_remote, bool) else None,
        "work_from_home_type": _text(row.get("work_from_home_type")),
        "job_level": _text(row.get("job_level")),
        "salary_min": _number(row.get("min_amount")),
        "salary_max": _number(row.get("max_amount")),
        "salary_interval": _text(row.get("interval")),
        "salary_currency": _text(row.get("currency")),
        "job_url": _text(row.get("job_url")),
        "job_url_direct": _text(row.get("job_url_direct")),
        "raw": {column: clean(row.get(column)) for column in RAW_COLUMNS if column in row},
    }


def patch_hellowork(hellowork: Any) -> None:
    """Remet d'aplomb la lecture des cartes HelloWork par JobSpy 1.4.0.

    Constaté le 2026-10-22 vers 9 h 30 : HelloWork a remplacé, dans le lien
    de chaque carte, les `<p>` du titre et de l'employeur par des `<span>`.
    JobSpy lit `h3 p` et écartait **toutes** les cartes (« not enough values
    to unpack ») : zéro annonce, sans erreur. Le correctif renomme les
    `<span>` en `<p>` avant la lecture d'origine ; il est idempotent et
    inoffensif si JobSpy est corrigé en amont. C'est exactement le genre de
    panne qui doit rester de ce côté-ci du contrat.
    """
    original = hellowork._process_job
    if getattr(original, "karria_patched", False):
        return

    def process(self: Any, card: Any) -> Any:
        link = card.select_one("a[data-cy=offerTitle]")
        if link is not None:
            for span in link.select("h3 span"):
                span.name = "p"
        return original(self, card)

    process.karria_patched = True  # type: ignore[attr-defined]  # marqueur d'idempotence
    hellowork._process_job = process


def scrape(
    site: str,
    target: Target,
    *,
    results_wanted: int,
    hours_old: int,
    proxies: list[str],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Une requête à un site ; lève si le site échoue (l'appelant décide)."""
    from jobspy import scrape_jobs  # noqa: PLC0415 - import lourd (pandas), différé au premier usage
    from jobspy.hellowork import HelloWork  # noqa: PLC0415

    patch_hellowork(HelloWork)

    _ = timeout_seconds  # JobSpy n'expose pas de délai ; gardé pour le jour où il le fera
    nationwide = target.location.strip().lower() == NATIONWIDE
    frame = scrape_jobs(
        site_name=[site],
        search_term=target.search_term,
        location=None if nationwide and site == "hellowork" else target.location,
        distance=round(target.distance_km / KM_PER_MILE) if target.distance_km else None,
        results_wanted=results_wanted,
        hours_old=hours_old,
        country_indeed=COUNTRY,
        proxies=proxies or None,
        description_format="markdown",
        fetch_description=True,
        verbose=0,
    )
    rows: list[dict[str, Any]] = frame.to_dict(orient="records")
    return [record for record in (row_to_record(row, site) for row in rows) if record is not None]
