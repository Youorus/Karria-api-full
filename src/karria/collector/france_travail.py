"""France Travail, la source n°1 : l'API officielle « Offres d'emploi v2 », lue en continu.

Contrat
-------
- **Authentification** OAuth2 `client_credentials` ; le jeton (≈ 25 min)
  est gardé et renouvelé une minute avant d'expirer.
- **Pagination** par `range=p-d` : 150 annonces par page au plus, index
  final 3149 au plus (vérifié le 2026-10-22 : `3000-3149` passe,
  `3000-3150` et `3150-3299` rendent 400). Une fenêtre qui dépasse 3 150
  annonces se **partitionne** par département, puis par moitiés de temps.
- **Incrémental** par `minCreationDate`/`maxCreationDate` : le passage
  courant relit depuis le dernier instant vu, avec un recouvrement — une
  annonce revue à l'identique ne coûte à l'API qu'un `last_seen_at`.
- **Fuseau** : mesuré le 2026-10-22 à 14 h 39 UTC, l'API rendait des
  annonces créées « 16:39:17Z » et une fenêtre `[maintenant - 1 h,
  maintenant]` exprimée en UTC rendait des annonces d'il y a deux heures.
  Les instants de France Travail sont l'heure de **Paris** affublée d'un
  `Z` : on les lit et on les écrit dans ce fuseau, et on remet du vrai UTC.
- **Limite de débit** mesurée : 10 appels/s par client (`X-Ratelimit-*`).
  On s'en tient à 8, par un seau à jetons ; un 429 attend `Retry-After`.
- **Retraits** : `GET /offres/{id}` rend 204 pour une annonce retirée
  (vérifié) ; le collecteur le dit à l'API par une ligne `expired: true`.
- **ROME** : code et libellé voyagent dans `raw` seulement — Karria ne
  classe plus par ROME (ADR-0016).

Le client est synchrone (bibliothèque standard) et s'exécute dans un fil ;
le reste du collecteur ne voit que des annonces au format de remise.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from karria.shared.logging import get_logger

logger = get_logger(__name__)

SOURCE: Final = "france_travail"
PAGE_SIZE: Final = 150
#: Index maximal accepté par `range` (plafond dur de l'API).
MAX_INDEX: Final = 3_149
MAX_RESULTS: Final = MAX_INDEX + 1
#: Appels par seconde que l'on s'autorise (l'API en accorde 10).
REQUESTS_PER_SECOND: Final = 8.0
#: Reprises sur 429 et 5xx, puis on abandonne la page.
ATTEMPTS: Final = 5
#: Marge avant l'expiration du jeton.
TOKEN_MARGIN_SECONDS: Final = 60
#: Une fenêtre temporelle plus courte ne se découpe plus : 3 150 annonces en
#: cinq minutes dans un seul département n'arrive pas.
MIN_WINDOW: Final = timedelta(minutes=5)
DATE_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
#: Le fuseau réel des instants de l'API, malgré leur `Z` (voir l'en-tête).
FT_TIMEZONE: Final = ZoneInfo("Europe/Paris")
#: Les départements (métropole, Corse, outre-mer) : la partition d'une
#: fenêtre trop large.
DEPARTMENTS: Final[tuple[str, ...]] = (
    *(f"{n:02d}" for n in range(1, 20)),
    "2A",
    "2B",
    *(f"{n:02d}" for n in range(21, 96)),
    "971",
    "972",
    "973",
    "974",
    "976",
)
#: Type de contrat France Travail → contrat du domaine (valeurs de
#: `ContractType`). Ce qui n'y est pas est laissé à la lecture du texte.
CONTRACT_TYPES: Final[dict[str, str]] = {
    "CDI": "cdi",
    "DIN": "cdi",  # CDI intérimaire
    "CDD": "cdd",
    "DDI": "cdd",  # CDD d'insertion
    "MIS": "interim",
    "TTI": "interim",  # travail temporaire d'insertion
    "SAI": "saisonnier",
    "LIB": "freelance",
    "FRA": "freelance",  # franchise
}
#: Natures de contrat qui disent l'alternance, quel que soit le type.
ALTERNANCE_NATURES: Final[frozenset[str]] = frozenset(
    {"contrat apprentissage", "cont. professionnalisation", "contrat de professionnalisation"}
)
SCHEDULES: Final[dict[str, str]] = {"temps plein": "temps_plein", "temps partiel": "temps_partiel"}
#: Les champs gardés dans `raw` : de quoi rejouer une normalisation, le ROME compris.
RAW_FIELDS: Final[tuple[str, ...]] = (
    "romeCode",
    "romeLibelle",
    "appellationlibelle",
    "typeContrat",
    "typeContratLibelle",
    "natureContrat",
    "experienceExige",
    "experienceLibelle",
    "dureeTravailLibelle",
    "dureeTravailLibelleConverti",
    "alternance",
    "qualificationCode",
    "qualificationLibelle",
    "secteurActivite",
    "secteurActiviteLibelle",
    "codeNAF",
    "nombrePostes",
    "accessibleTH",
    "dateCreation",
    "dateActualisation",
    "origineOffre",
)
_CONTENT_RANGE: Final = re.compile(r"(\d+)-(\d+)/(\d+)")
_DEPARTMENT_LABEL: Final = re.compile(r"^\s*(\d{2,3}|2A|2B)\s*-\s*(.+?)\s*$", re.IGNORECASE)
_DEPT_SUFFIX: Final = re.compile(r"\s*\(\s*dept\.?\s*\)\s*$", re.IGNORECASE)
_YEARS: Final = re.compile(r"(\d+)\s*an", re.IGNORECASE)
_MONTHS: Final = re.compile(r"(\d+)\s*mois", re.IGNORECASE)
_HTTP_OK: Final = 200
_HTTP_PARTIAL: Final = 206
_HTTP_NO_CONTENT: Final = 204
_HTTP_UNAUTHORIZED: Final = 401
_HTTP_TOO_MANY: Final = 429
_HTTP_SERVER_ERROR: Final = 500


class FranceTravailError(Exception):
    """L'API a refusé, ou n'a pas répondu après les reprises."""

    def __init__(self, status: int | None, detail: str) -> None:
        super().__init__(f"France Travail {status}: {detail}")
        self.status = status


@dataclass(frozen=True, slots=True)
class Credentials:
    """Ce qu'il faut pour parler à l'API."""

    client_id: str
    client_secret: str
    scope: str
    token_url: str
    search_url: str

    @property
    def offer_url(self) -> str:
        """`…/offres/{id}`, déduit de l'URL de recherche."""
        return self.search_url.rsplit("/search", 1)[0]


@dataclass(frozen=True, slots=True)
class Page:
    """Une page de résultats et le total annoncé par `Content-Range`."""

    offers: list[dict[str, Any]]
    total: int


class RateLimiter:
    """Un seau à jetons : au plus `rate` appels par seconde, en moyenne."""

    def __init__(
        self,
        rate: float = REQUESTS_PER_SECOND,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next = clock()

    def wait(self) -> None:
        """Attend son tour."""
        now = self._clock()
        if now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self._interval


Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(
    request: urllib.request.Request, timeout: float
) -> Any:  # pragma: no cover - le seul appel réseau réel
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - URL de configuration


class FranceTravailClient:
    """Le client : jeton, recherche paginée, existence d'une annonce."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        timeout_seconds: float = 30.0,
        limiter: RateLimiter | None = None,
        opener: Opener = _default_opener,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._credentials = credentials
        self._timeout = timeout_seconds
        self._limiter = limiter or RateLimiter(clock=clock, sleep=sleep)
        self._opener = opener
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.requests = 0

    # --- Jeton -----------------------------------------------------------

    def token(self) -> str:
        """Le jeton courant, renouvelé une minute avant son expiration."""
        if self._token is not None and self._clock() < self._token_expires_at:
            return self._token
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "scope": self._credentials.scope,
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310 - URL https de configuration
            self._credentials.token_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        status, _, payload = self._send(request)
        if status != _HTTP_OK:
            raise FranceTravailError(status, "authentification refusée")
        data = json.loads(payload)
        self._token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 1_499))
        self._token_expires_at = self._clock() + max(expires_in - TOKEN_MARGIN_SECONDS, 30)
        logger.info("france_travail.token_renewed", expires_in=expires_in)
        return self._token

    # --- Recherche -------------------------------------------------------

    def search(self, params: Mapping[str, Any]) -> Page:
        """Une page : `range` est dans `params`. 204 vaut page vide."""
        query = urllib.parse.urlencode({k: str(v) for k, v in params.items()})
        status, headers, payload = self._authorized(f"{self._credentials.search_url}?{query}")
        if status == _HTTP_NO_CONTENT or not payload:
            return Page(offers=[], total=0)
        data = json.loads(payload)
        total = _total(headers.get("Content-Range")) if headers else None
        offers = [o for o in data.get("resultats", []) if isinstance(o, dict)]
        return Page(offers=offers, total=total if total is not None else len(offers))

    def count(self, params: Mapping[str, Any]) -> int:
        """Combien d'annonces répondent à ces critères (une page d'une annonce)."""
        return self.search({**params, "range": "0-0"}).total

    def fetch_all(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Toutes les pages d'une requête, dans la limite des 3 150 premières."""
        found: list[dict[str, Any]] = []
        start = 0
        while start <= MAX_INDEX:
            end = min(start + PAGE_SIZE - 1, MAX_INDEX)
            page = self.search({**params, "range": f"{start}-{end}"})
            found.extend(page.offers)
            start = end + 1
            if not page.offers or start >= page.total:
                break
        return found

    def exists(self, offer_id: str) -> bool:
        """L'annonce est-elle encore publiée ? (204 : retirée.)"""
        safe = urllib.parse.quote(offer_id, safe="")
        status, _, _ = self._authorized(f"{self._credentials.offer_url}/{safe}")
        return status != _HTTP_NO_CONTENT

    # --- Transport -------------------------------------------------------

    def _authorized(self, url: str) -> tuple[int, Any, bytes]:
        """Un `GET` authentifié, avec reprise sur 401 (jeton), 429 et 5xx."""
        last: FranceTravailError | None = None
        for attempt in range(ATTEMPTS):
            request = urllib.request.Request(  # noqa: S310 - URL https de configuration
                url,
                headers={"Authorization": f"Bearer {self.token()}", "Accept": "application/json"},
            )
            self._limiter.wait()
            try:
                return self._send(request)
            except FranceTravailError as exc:
                last = exc
                if exc.status == _HTTP_UNAUTHORIZED:
                    self._token = None
                    continue
                if exc.status != _HTTP_TOO_MANY and (
                    exc.status is not None and exc.status < _HTTP_SERVER_ERROR
                ):
                    raise
                self._sleep(_backoff(exc, attempt))
        assert last is not None  # noqa: S101 - la boucle a tourné au moins une fois
        raise last

    def _send(self, request: urllib.request.Request) -> tuple[int, Any, bytes]:
        self.requests += 1
        try:
            with self._opener(request, self._timeout) as response:
                payload: bytes = response.read()
                return int(response.status), response.headers, payload
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            error = FranceTravailError(exc.code, detail)
            error.retry_after = _retry_after(exc.headers)  # type: ignore[attr-defined]  # porté par l'exception
            raise error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FranceTravailError(None, str(exc)) from exc


def _total(content_range: str | None) -> int | None:
    if not content_range or (match := _CONTENT_RANGE.search(content_range)) is None:
        return None
    return int(match.group(3))


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers else None
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _backoff(exc: FranceTravailError, attempt: int) -> float:
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, float):
        return min(retry_after, 30.0)
    return float(2**attempt)


# ---------------------------------------------------------------------
# Fenêtres de collecte
# ---------------------------------------------------------------------


def window_params(start: datetime, end: datetime) -> dict[str, str]:
    """Les paramètres d'une fenêtre de création, dans le fuseau de l'API, des plus récentes aux plus anciennes."""
    return {
        "minCreationDate": start.astimezone(FT_TIMEZONE).strftime(DATE_FORMAT),
        "maxCreationDate": end.astimezone(FT_TIMEZONE).strftime(DATE_FORMAT),
        "sort": "1",
    }


def collect_window(
    client: FranceTravailClient,
    start: datetime,
    end: datetime,
    *,
    department: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Toutes les annonces créées dans la fenêtre, sans en perdre au plafond.

    Si la fenêtre dépasse 3 150 annonces : par département ; si un
    département dépasse encore : par moitiés de temps, jusqu'à `MIN_WINDOW`.
    Au-delà, on prend ce que l'API donne et on le journalise — c'est une
    perte visible, pas silencieuse.
    """
    params = window_params(start, end)
    if department is not None:
        params["departement"] = department
    total = client.count(params)
    if total == 0:
        return
    if total <= MAX_RESULTS:
        yield from client.fetch_all(params)
        return
    if department is None:
        for code in DEPARTMENTS:
            yield from collect_window(client, start, end, department=code)
        return
    if end - start > MIN_WINDOW:
        middle = start + (end - start) / 2
        yield from collect_window(client, start, middle, department=department)
        yield from collect_window(client, middle, end, department=department)
        return
    logger.warning(
        "france_travail.window_truncated", department=department, total=total, kept=MAX_RESULTS
    )
    yield from client.fetch_all(params)


# ---------------------------------------------------------------------
# Mise au format de remise
# ---------------------------------------------------------------------


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _mapping(container: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Un sous-objet JSON, ou un dictionnaire vide s'il manque ou n'en est pas un."""
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _moment(value: Any) -> str | None:
    """Un instant de l'API (heure de Paris marquée `Z`, millisecondes) → ISO UTC à la seconde."""
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.removesuffix("Z"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=FT_TIMEZONE)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat()


def location_label(lieu: Mapping[str, Any]) -> str | None:
    """« 86 - FLEURE » → « Fleure (86) » ; « 75 - Paris (Dept.) » → « Paris (75) »."""
    label = _text(lieu.get("libelle"))
    if label is None:
        return None
    match = _DEPARTMENT_LABEL.match(label)
    if match is None:
        return label
    department = match.group(1).upper()
    name = _DEPT_SUFFIX.sub("", match.group(2)).strip()
    if not name:
        return label
    pretty = name.title() if name.isupper() else name
    return f"{pretty} ({department})"


def contract_of(offer: Mapping[str, Any]) -> str | None:
    """Le contrat du domaine : l'alternance d'abord, puis le type France Travail."""
    nature = (_text(offer.get("natureContrat")) or "").lower()
    if offer.get("alternance") is True or nature in ALTERNANCE_NATURES:
        return "alternance"
    return CONTRACT_TYPES.get((_text(offer.get("typeContrat")) or "").upper())


def experience_of(offer: Mapping[str, Any]) -> tuple[str | None, float | None]:
    """(`debutant` si la source l'accepte, années demandées si elle les chiffre)."""
    label = _text(offer.get("experienceLibelle")) or ""
    if offer.get("experienceExige") == "D" or "débutant" in label.lower():
        return "debutant", None
    if (years := _YEARS.search(label)) is not None:
        return None, float(years.group(1))
    if (months := _MONTHS.search(label)) is not None:
        return None, round(int(months.group(1)) / 12, 2)
    return None, None


def apply_urls(offer: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(la page de l'annonce, le lien direct pour postuler s'il existe)."""
    origin = _mapping(offer, "origineOffre")
    contact = _mapping(offer, "contact")
    page = _text(origin.get("urlOrigine"))
    direct = _text(contact.get("urlPostulation"))
    if direct is None:
        raw_partners = origin.get("partenaires")
        partners: list[Any] = raw_partners if isinstance(raw_partners, list) else []
        direct = next(
            (_text(p.get("url")) for p in partners if isinstance(p, dict) and _text(p.get("url"))),
            None,
        )
    return page, direct


def offer_to_record(offer: Mapping[str, Any]) -> dict[str, Any] | None:
    """Une annonce France Travail → une ligne du format de remise. `None` sans id ni titre."""
    source_id = _text(offer.get("id"))
    title = _text(offer.get("intitule"))
    if source_id is None or title is None:
        return None
    lieu = _mapping(offer, "lieuTravail")
    company = _mapping(offer, "entreprise")
    salary = _mapping(offer, "salaire")
    converted = (_text(offer.get("dureeTravailLibelleConverti")) or "").lower()
    experience, years = experience_of(offer)
    page, direct = apply_urls(offer)
    posted_at = _moment(offer.get("dateCreation"))
    latitude, longitude = lieu.get("latitude"), lieu.get("longitude")
    return {
        "source": SOURCE,
        "source_id": source_id,
        "title": title,
        "description": _text(offer.get("description")) or "",
        "company_name": _text(company.get("nom")),
        "company_logo_url": _text(company.get("logo")),
        "company_url": _text(company.get("url")),
        "location_label": location_label(lieu),
        "date_posted": posted_at[:10] if posted_at else None,
        "job_types": [],
        "is_remote": None,
        "work_from_home_type": None,
        "job_level": None,
        "salary_min": None,
        "salary_max": None,
        "salary_interval": None,
        "salary_currency": None,
        "job_url": page,
        "job_url_direct": direct,
        "contract_type": contract_of(offer),
        "schedule": SCHEDULES.get(converted),
        "experience": experience,
        "experience_years": years,
        "salary_text": _text(salary.get("libelle")),
        "latitude": float(latitude) if isinstance(latitude, int | float) else None,
        "longitude": float(longitude) if isinstance(longitude, int | float) else None,
        "postal_code": _text(lieu.get("codePostal")),
        "posted_at": posted_at,
        "updated_at": _moment(offer.get("dateActualisation")),
        "expired": False,
        "raw": {
            **{field: offer.get(field) for field in RAW_FIELDS if field in offer},
            "commune": lieu.get("commune"),
            "lieuTravailLibelle": lieu.get("libelle"),
        },
    }


def expired_record(source_id: str) -> dict[str, Any]:
    """La ligne qui dit « la source a retiré cette annonce »."""
    return {"source": SOURCE, "source_id": source_id, "expired": True}
