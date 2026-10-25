"""De l'annonce brute à l'offre comprise : contrat, télétravail, expérience, salaire, lieu.

Contrat
-------
Chaque règle est une fonction pure « texte en entrée, décision en sortie »,
testable en une milliseconde, et **sait dire « je ne sais pas »** (`None`)
plutôt que d'inventer. Une offre dont on ignore le contrat reste visible ;
elle n'est simplement pas retenue par un filtre « CDI ».

Ce que la V1 faisait, et ce qu'on change : la V1 lisait les champs de la
source (`typeContratLibelle`, `experienceLibelle`) et ne comprenait rien des
offres qui ne les avaient pas (Indeed, LinkedIn : 0 % de coordonnées, un
contrat déduit d'un `job_type` anglais). Ici le texte de l'annonce est la
source principale, les champs structurés de la source un indice de plus.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from karria.modules.offers.domain.offers import (
    Company,
    ContractType,
    ExperienceLevel,
    Location,
    Offer,
    RemoteMode,
    Salary,
    Schedule,
    Source,
    uuid7,
)


@dataclass(frozen=True, slots=True)
class RawPosting:
    """Une annonce telle qu'une source la livre, sans interprétation.

    C'est le pivot entre un collecteur (JobSpy aujourd'hui) et le domaine :
    un nouveau collecteur produit des `RawPosting`, et rien d'autre ne change.
    """

    source: Source
    source_id: str
    title: str
    description: str
    company_name: str | None = None
    company_logo_url: str | None = None
    company_url: str | None = None
    #: Le site de l'employeur quand la source le donne à part (Indeed :
    #: `company_url_direct`) ; `company_url` mène souvent à sa page sur la source.
    company_website: str | None = None
    location_label: str | None = None
    date_posted: date | None = None
    job_types: tuple[str, ...] = ()
    is_remote: bool | None = None
    work_from_home_type: str | None = None
    job_level: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_interval: str | None = None
    salary_currency: str | None = None
    job_url: str | None = None
    job_url_direct: str | None = None
    #: Indices **structurés** que certaines sources donnent explicitement
    #: (France Travail : type de contrat, temps de travail, expérience,
    #: salaire, coordonnées). Ils priment sur la lecture du texte ; une
    #: valeur hors des énumérations du domaine vaut « absent ».
    contract_type: str | None = None
    schedule: str | None = None
    experience: str | None = None
    experience_years: float | None = None
    salary_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    postal_code: str | None = None
    #: Parution et dernière mise à jour à la seconde, quand la source les
    #: donne (`date_posted` ne connaît que le jour).
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    #: La source a retiré l'annonce : l'offre connue se désactive.
    expired: bool = False
    #: La ligne brute, conservée pour rejouer une normalisation.
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LocationHint:
    """Ce que le libellé de lieu permet de comprendre avant géocodage."""

    query: str | None
    city: str | None = None
    postal_code: str | None = None
    department_code: str | None = None


@dataclass(frozen=True, slots=True)
class GeocodedPlace:
    """Ce que le géocodeur rend pour une requête."""

    city: str
    postal_code: str | None
    department_code: str | None
    latitude: float
    longitude: float


# ---------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------

_WS: Final = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES: Final = re.compile(r"\n{3,}")
#: Longueur au-delà de laquelle une description est tronquée en base : les
#: annonces de 30 000 caractères sont des pages de présentation d'entreprise.
DESCRIPTION_MAX_LENGTH: Final = 12_000


def fold(text: str) -> str:
    """Minuscules, sans accents, espaces normalisés — pour comparer, jamais pour afficher."""
    stripped = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    return _WS.sub(" ", ascii_only.lower()).strip()


def clean_text(text: str | None, *, max_length: int = DESCRIPTION_MAX_LENGTH) -> str:
    """Normalise les blancs d'un texte libre et le borne."""
    if not text:
        return ""
    lines = [_WS.sub(" ", line).strip() for line in text.replace("\r", "").split("\n")]
    cleaned = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rsplit(" ", 1)[0]
    return cleaned


def clean_title(title: str) -> str:
    """Un titre sur une ligne, borné, sans « H/F » en double espace."""
    return _WS.sub(" ", title.replace("\n", " ")).strip()[:255]


# ---------------------------------------------------------------------
# Contrat
# ---------------------------------------------------------------------

#: Dans l'ordre : le premier motif qui apparaît l'emporte. L'alternance passe
#: avant le CDD — « alternance en CDD de 12 mois » est une alternance.
_CONTRACT_PATTERNS: Final[tuple[tuple[ContractType, re.Pattern[str]], ...]] = (
    (
        ContractType.ALTERNANCE,
        re.compile(r"\b(alternan\w*|apprenti\w*|apprentissage|contrat (de )?pro\w*)\b"),
    ),
    (ContractType.STAGE, re.compile(r"\b(stage|stagiaire|internship|intern)\b")),
    (ContractType.INTERIM, re.compile(r"\b(interim\w*|interimaire)\b")),
    (ContractType.SAISONNIER, re.compile(r"\bsaisonni\w+\b")),
    (
        ContractType.FREELANCE,
        re.compile(r"\b(freelance|free-lance|independant|auto-?entrepreneur|portage salarial)\b"),
    ),
    (ContractType.CDD, re.compile(r"\bcdd\b")),
    (ContractType.CDI, re.compile(r"\bcdi\b")),
)

_JOB_TYPE_CONTRACT: Final[dict[str, ContractType]] = {
    "internship": ContractType.STAGE,
    "temporary": ContractType.CDD,
    "contract": ContractType.CDD,
}


def _first_contract(text: str) -> ContractType | None:
    hits = [(m.start(), kind) for kind, p in _CONTRACT_PATTERNS if (m := p.search(text))]
    if not hits:
        return None
    return min(hits)[1]


#: Les types de JobSpy qui disent un contrat (les autres disent un temps de travail).
_CONTRACT_JOB_TYPES: Final = frozenset({"temporary", "contract", "internship"})


def source_default_contract(source: Source, job_types: Sequence[str]) -> ContractType | None:
    """Ce que la source dit en creux, quand elle le dit.

    HelloWork affiche toujours un contrat sur sa carte, et JobSpy en traduit
    tous les libellés en type — sauf « CDI », qu'il laisse tomber (vérifié
    dans `jobspy/hellowork/constant.py`, v1.4.0). Une carte HelloWork sans
    type de contrat est donc un CDI. Indeed n'offre pas cette garantie :
    l'attribut « CDI » y est simplement perdu, et l'absence ne prouve rien.
    """
    if source is Source.HELLOWORK and not _CONTRACT_JOB_TYPES & {t.lower() for t in job_types}:
        return ContractType.CDI
    return None


def hinted[E: StrEnum](kind: type[E], value: str | None) -> E | None:
    """Une valeur structurée de la source, si elle est une valeur du domaine."""
    if not value:
        return None
    try:
        return kind(value.strip().lower())
    except ValueError:
        return None


def detect_contract(
    title: str,
    description: str,
    job_types: Sequence[str],
    *,
    default: ContractType | None = None,
    hint: ContractType | None = None,
) -> ContractType | None:
    """Le contrat : l'indice structuré de la source, sinon le titre, la description, la source.

    L'indice structuré (France Travail : `typeContrat`, `alternance`)
    prime sur le texte : une annonce de CDI qui mentionne « un CDD a
    précédé ce poste » reste un CDI.
    """
    if hint is not None:
        return hint
    found = _first_contract(fold(title))
    if found is not None:
        return found
    found = _first_contract(fold(description[:1_500]))
    if found is not None:
        return found
    for job_type in job_types:
        if (kind := _JOB_TYPE_CONTRACT.get(job_type.lower())) is not None:
            return kind
    if default is not None:
        return default
    # Le contrat se dit parfois seulement en bas de l'annonce.
    return _first_contract(fold(description[1_500:]))


# ---------------------------------------------------------------------
# Télétravail et temps de travail
# ---------------------------------------------------------------------

_FULL_REMOTE: Final = re.compile(
    r"(100 ?% (de )?(teletravail|remote|a distance)|full remote|teletravail (complet|total|integral)"
    r"|entierement (a distance|en teletravail)|remote (first|only))"
)
_HYBRID: Final = re.compile(
    r"(hybride|hybrid|teletravail partiel|\d ?(a ?\d ?)?jours? (de )?teletravail"
    r"|teletravail (possible|envisageable|ponctuel|occasionnel)|jours? en presentiel)"
)
_ANY_REMOTE: Final = re.compile(r"\b(teletravail|remote|a distance)\b")
_PART_TIME: Final = re.compile(
    r"(temps partiel|mi-temps|part[- ]time|parttime|\b(2[0-9]|3[0-4])h\b)"
)
_FULL_TIME: Final = re.compile(r"(temps (plein|complet)|full[- ]time|fulltime|\b3[5-9]h\b)")


def detect_remote(
    is_remote: bool | None, work_from_home_type: str | None, title: str, description: str
) -> RemoteMode | None:
    """Le mode de télétravail annoncé, sans extrapoler."""
    text = fold(f"{title}\n{description}")
    wfh = fold(work_from_home_type or "")
    if "remote" in wfh or _FULL_REMOTE.search(text):
        return RemoteMode.REMOTE
    if "hybrid" in wfh or _HYBRID.search(text):
        return RemoteMode.HYBRID
    if is_remote:
        return RemoteMode.REMOTE
    if _ANY_REMOTE.search(text):
        # Mentionné sans précision : une part de télétravail, pas tout.
        return RemoteMode.HYBRID
    if "office" in wfh or "on site" in wfh or "sur site" in wfh:
        return RemoteMode.ON_SITE
    return None


def detect_schedule(
    job_types: Sequence[str], title: str, description: str, *, hint: Schedule | None = None
) -> Schedule | None:
    """Temps plein ou partiel. L'indice structuré, sinon le titre, le corps, la source."""
    if hint is not None:
        return hint
    for text in (fold(title), fold(description)):
        if _PART_TIME.search(text):
            return Schedule.PART_TIME
        if _FULL_TIME.search(text):
            return Schedule.FULL_TIME
    lowered = {t.lower() for t in job_types}
    if "parttime" in lowered:
        return Schedule.PART_TIME
    if "fulltime" in lowered:
        return Schedule.FULL_TIME
    return None


# ---------------------------------------------------------------------
# Expérience
# ---------------------------------------------------------------------

_YEARS: Final = re.compile(
    r"(?:(?:minimum|min\.?|au moins|plus de|\+ ?de)\s*)?(\d{1,2})\s*(?:a|à|-|/)?\s*(\d{1,2})?\s*"
    r"(?:ans?|annees?)\s*(?:d'|d |de |minimum)?\s*(?:experience|exp\b)"
)
_YEARS_AFTER: Final = re.compile(
    r"experience\s*(?:de|d'|minimum de|d'au moins|:)?\s*(\d{1,2})\s*(?:ans?|annees?)"
)
_BEGINNER: Final = re.compile(
    r"(debutant\w* accept\w*|sans experience|aucune experience|premiere experience|premier emploi"
    r"|jeune diplome|ouvert aux debutants|debutant\w* bienvenu\w*|accessible sans)"
)
_SENIOR_TITLE: Final = re.compile(
    r"\b(senior|lead|principal|expert|chef de|directeur|directrice|head of)\b"
)
_JUNIOR_TITLE: Final = re.compile(r"\b(junior|debutant\w*|stagiaire|alternan\w*|apprenti\w*)\b")
_CONFIRMED: Final = re.compile(r"\b(confirme\w*|experimente\w*)\b")

_JOB_LEVEL: Final[dict[str, ExperienceLevel]] = {
    "internship": ExperienceLevel.BEGINNER,
    "entry level": ExperienceLevel.BEGINNER,
    "associate": ExperienceLevel.JUNIOR,
    "mid-senior level": ExperienceLevel.CONFIRMED,
    "director": ExperienceLevel.SENIOR,
    "executive": ExperienceLevel.SENIOR,
}


def level_for_years(years: int) -> ExperienceLevel:
    """Des années demandées au palier."""
    if years < 1:
        return ExperienceLevel.BEGINNER
    if years < 3:  # noqa: PLR2004
        return ExperienceLevel.JUNIOR
    if years < 7:  # noqa: PLR2004
        return ExperienceLevel.CONFIRMED
    return ExperienceLevel.SENIOR


def detect_experience(  # noqa: PLR0911 - une règle par indice, dans l'ordre de confiance
    job_level: str | None,
    title: str,
    description: str,
    *,
    hint: ExperienceLevel | None = None,
    years: float | None = None,
) -> ExperienceLevel | None:
    """L'expérience demandée : l'indice de la source, les années, les mots du titre, le niveau."""
    if hint is not None:
        return hint
    if years is not None:
        return level_for_years(int(years))
    body = fold(description)
    if _BEGINNER.search(body):
        return ExperienceLevel.BEGINNER
    explicit = _YEARS.search(body) or _YEARS_AFTER.search(body)
    if explicit is not None:
        return level_for_years(int(explicit.group(1)))
    head = fold(title)
    if _JUNIOR_TITLE.search(head):
        return ExperienceLevel.JUNIOR if "junior" in head else ExperienceLevel.BEGINNER
    if _SENIOR_TITLE.search(head):
        return ExperienceLevel.SENIOR
    if _CONFIRMED.search(head) or _CONFIRMED.search(body):
        return ExperienceLevel.CONFIRMED
    if job_level and (level := _JOB_LEVEL.get(job_level.strip().lower())) is not None:
        return level
    return None


# ---------------------------------------------------------------------
# Salaire
# ---------------------------------------------------------------------

#: Heures et jours travaillés par mois, pour ramener un taux au mensuel.
HOURS_PER_MONTH: Final = 151.67
DAYS_PER_MONTH: Final = 21.67
WEEKS_PER_MONTH: Final = 4.33
#: Bornes de vraisemblance d'un brut mensuel en euros. Hors de là, c'est
#: une erreur de lecture (un numéro de téléphone, un code postal).
MONTHLY_MIN: Final = 700
MONTHLY_MAX: Final = 40_000

_INTERVAL_FACTOR: Final[dict[str, float]] = {
    "yearly": 1 / 12,
    "monthly": 1.0,
    "weekly": WEEKS_PER_MONTH,
    "daily": DAYS_PER_MONTH,
    "hourly": HOURS_PER_MONTH,
}

_AMOUNT: Final = r"(\d{1,3}(?:[ \u00a0.]\d{3})+|\d+(?:[.,]\d+)?)\s*(k)?\s*(?:€|euros?|eur)"
_RANGE: Final = re.compile(
    rf"(?:de|entre|from)?\s*{_AMOUNT}?\s*(?:a|à|et|-|\u2013|/|to)\s*{_AMOUNT}|{_AMOUNT}",
    re.IGNORECASE,
)
_PERIOD_ANNUAL: Final = re.compile(r"(annuel|par an|/ ?an|brut/an|\ban\b|k€|ke)")
_PERIOD_HOURLY: Final = re.compile(r"(horaire|par heure|/ ?h\b|de l'heure|\bh\b)")
_PERIOD_DAILY: Final = re.compile(r"(tjm|par jour|/ ?jour|journalier)")


def _to_number(raw: str, kilo: str | None) -> float:
    """Un montant déjà reconnu par `_AMOUNT` : chiffres, séparateurs, décimale."""
    value = raw.replace("\u00a0", "").replace(" ", "")
    if "." in value and "," not in value and len(value.split(".")[-1]) == 3:  # noqa: PLR2004
        value = value.replace(".", "")
    number = float(value.replace(",", "."))
    return number * 1000 if kilo else number


def _monthly(amount: float, factor: float) -> int | None:
    monthly = round(amount * factor)
    return monthly if MONTHLY_MIN <= monthly <= MONTHLY_MAX else None


def _factor_for(context: str, amount: float) -> float:
    if _PERIOD_HOURLY.search(context) and amount < 500:  # noqa: PLR2004
        return HOURS_PER_MONTH
    if _PERIOD_DAILY.search(context) and amount < 3_000:  # noqa: PLR2004
        return DAYS_PER_MONTH
    if _PERIOD_ANNUAL.search(context) or amount >= 10_000:  # noqa: PLR2004
        return 1 / 12
    if amount < 200:  # noqa: PLR2004
        return HOURS_PER_MONTH
    return 1.0


def parse_salary_text(text: str) -> tuple[int | None, int | None]:
    """Lit un salaire dans du texte libre, ramené au brut mensuel.

    Lit la première somme en euros et, s'il y en a une, sa borne haute.
    L'unité se devine au contexte (« par an », « /h », « k€ ») puis à
    l'ordre de grandeur. Rend `(None, None)` plutôt qu'un chiffre douteux.
    """
    folded = fold(text)
    match = _RANGE.search(folded)
    if match is None:
        return None, None
    groups = match.groups()
    low = _to_number(groups[0], groups[1]) if groups[0] else None
    high = _to_number(groups[2], groups[3]) if groups[2] else None
    if low is None and high is None and groups[4]:
        low = _to_number(groups[4], groups[5])
    context = folded[max(0, match.start() - 40) : match.end() + 40]
    # Une borne au moins est lue : l'expression exige un montant.
    reference = low if low is not None else high
    assert reference is not None  # noqa: S101 - garanti par `_RANGE`
    factor = _factor_for(context, reference)
    monthly_low = _monthly(low, factor) if low is not None else None
    monthly_high = _monthly(high, factor) if high is not None else None
    if monthly_low is not None and monthly_high is not None and monthly_high < monthly_low:
        monthly_low, monthly_high = monthly_high, monthly_low
    return monthly_low, monthly_high


def normalize_salary(raw: RawPosting) -> Salary:
    """Le libellé de salaire de la source, puis ses bornes structurées en euros, puis le texte."""
    salary_text = raw.salary_text or (
        raw.raw.get("salary_text") if isinstance(raw.raw.get("salary_text"), str) else None
    )
    if raw.salary_text:
        low, high = parse_salary_text(raw.salary_text)
        if low is not None or high is not None:
            return Salary(min_monthly=low, max_monthly=high, raw=salary_text)
    currency = (raw.salary_currency or "").upper()
    interval = (raw.salary_interval or "").lower()
    if currency in {"EUR", ""} and interval in _INTERVAL_FACTOR:
        factor = _INTERVAL_FACTOR[interval]
        low = _monthly(raw.salary_min, factor) if raw.salary_min else None
        high = _monthly(raw.salary_max, factor) if raw.salary_max else None
        if low is not None or high is not None:
            return Salary(min_monthly=low, max_monthly=high, raw=salary_text or _describe(raw))
    low, high = parse_salary_text(f"{raw.title}\n{raw.description}")
    return Salary(min_monthly=low, max_monthly=high, raw=salary_text)


def _describe(raw: RawPosting) -> str:
    """Appelé quand une borne au moins est connue."""
    parts = [str(int(v)) for v in (raw.salary_min, raw.salary_max) if v]
    return f"{' - '.join(parts)} {raw.salary_currency or 'EUR'} ({raw.salary_interval})"


# ---------------------------------------------------------------------
# Lieu
# ---------------------------------------------------------------------

#: Libellés qui ne désignent aucun lieu précis.
GENERIC_PLACES: Final[frozenset[str]] = frozenset(
    {"france", "fr", "teletravail", "remote", "a distance", "ile-de-france", "idf", "national"}
)
_POSTAL: Final = re.compile(r"\b(\d{5})\b")
_DEPARTMENT_SUFFIX: Final = re.compile(r"\(\s*(\d{2,3}|2a|2b)\s*\)", re.IGNORECASE)


def parse_location_label(label: str | None) -> LocationHint:
    """Comprend « Lyon (69) », « 75011 Paris », « Nanterre, Île-de-France, France »."""
    if not label:
        return LocationHint(query=None)
    text = _WS.sub(" ", label.replace("\n", " ")).strip()
    folded = fold(text)
    if folded in GENERIC_PLACES:
        return LocationHint(query=None)
    postal = _POSTAL.search(text)
    department = _DEPARTMENT_SUFFIX.search(text)
    head = text.split(",")[0]
    head = _DEPARTMENT_SUFFIX.sub("", head)
    head = _POSTAL.sub("", head).strip(" -\u2013")
    city = head or None
    if city and fold(city) in GENERIC_PLACES:
        city = None
    department_code = department.group(1).upper() if department else None
    if postal and department_code is None:
        department_code = department_from_postal(postal.group(1))
    query = " ".join(part for part in (postal.group(1) if postal else None, city) if part) or None
    return LocationHint(
        query=query,
        city=city,
        postal_code=postal.group(1) if postal else None,
        department_code=department_code,
    )


def department_from_postal(postal_code: str) -> str:
    """Le département d'un code postal, Corse et outre-mer compris."""
    if postal_code.startswith("97") or postal_code.startswith("98"):
        return postal_code[:3]
    if postal_code.startswith("20"):
        return "2A" if postal_code < "20200" else "2B"
    return postal_code[:2]


def source_place(raw: RawPosting) -> GeocodedPlace | None:
    """Le lieu que la source situe elle-même : aucun géocodage à faire.

    France Travail donne latitude, longitude et code postal de la commune ;
    la ville se lit dans le libellé.
    """
    if raw.latitude is None or raw.longitude is None:
        return None
    hint = parse_location_label(raw.location_label)
    postal = raw.postal_code or hint.postal_code
    return GeocodedPlace(
        city=hint.city or (raw.location_label or "").strip(),
        postal_code=postal,
        department_code=hint.department_code
        or (department_from_postal(postal) if postal else None),
        latitude=raw.latitude,
        longitude=raw.longitude,
    )


def build_location(
    hint: LocationHint, place: GeocodedPlace | None, label: str | None
) -> Location | None:
    """Assemble le lieu : les coordonnées du géocodeur, le libellé de la source."""
    if place is not None:
        return Location(
            label=label.strip() if label else place.city,
            city=place.city,
            postal_code=place.postal_code or hint.postal_code,
            department_code=place.department_code or hint.department_code,
            latitude=place.latitude,
            longitude=place.longitude,
        )
    if hint.query is None and not hint.department_code:
        return None
    return Location(
        label=label.strip() if label else (hint.city or ""),
        city=hint.city,
        postal_code=hint.postal_code,
        department_code=hint.department_code,
    )


# ---------------------------------------------------------------------
# L'offre
# ---------------------------------------------------------------------


def published_at_of(raw: RawPosting, *, now: datetime) -> datetime:
    """La parution annoncée par la source — à la seconde si elle la donne, au jour sinon.

    Jamais dans le futur ; `now` à défaut.
    """
    if raw.posted_at is not None:
        return min(raw.posted_at, now)
    if raw.date_posted is None:
        return now
    return min(datetime.combine(raw.date_posted, datetime.min.time(), tzinfo=UTC), now)


def normalize_posting(
    raw: RawPosting,
    *,
    now: datetime,
    place: GeocodedPlace | None,
    offer_id: UUID | None = None,
) -> Offer:
    """Produit l'offre normalisée. Le géocodage a eu lieu avant (adaptateur)."""
    title = clean_title(raw.title)
    description = clean_text(raw.description)
    hint = parse_location_label(raw.location_label)
    company = (
        Company(
            name=clean_title(raw.company_name),
            logo_url=raw.company_logo_url,
            website=raw.company_url,
        )
        if raw.company_name and raw.company_name.strip()
        else None
    )
    return Offer(
        id=offer_id or uuid7(),
        source=raw.source,
        source_id=raw.source_id,
        title=title,
        company=company,
        description=description,
        location=build_location(hint, place, raw.location_label),
        contract_type=detect_contract(
            title,
            description,
            raw.job_types,
            default=source_default_contract(raw.source, raw.job_types),
            hint=hinted(ContractType, raw.contract_type),
        ),
        remote=detect_remote(raw.is_remote, raw.work_from_home_type, title, description),
        schedule=detect_schedule(
            raw.job_types, title, description, hint=hinted(Schedule, raw.schedule)
        ),
        experience=detect_experience(
            raw.job_level,
            title,
            description,
            hint=hinted(ExperienceLevel, raw.experience),
            years=raw.experience_years,
        ),
        salary=normalize_salary(raw),
        published_at=published_at_of(raw, now=now),
        first_seen_at=now,
        last_seen_at=now,
        apply_url=raw.job_url_direct or raw.job_url,
        origin_url=raw.job_url,
    )
