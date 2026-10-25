"""Les employeurs : leur identité commune aux sources, et ce qui fait un logo acceptable.

Une même entreprise arrive sous dix graphies (« DECATHLON SAS »,
« Decathlon », « Décathlon France ») et par trois sources. On la reconnaît
par une **clé de nom** (pliée, sans forme juridique ni ponctuation) et, si
on la connaît, par son **domaine** (`decathlon.fr`). Deux homonymes aux
domaines différents restent deux entreprises ; un nom sans domaine se
rattache à l'homonyme déjà connu.

Le logo se cherche une fois par entreprise, jamais par offre, et se juge ici
sur des mesures (taille, uniformité) que l'adaptateur image fournit : la
règle reste testable sans Pillow ni réseau.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from html.parser import HTMLParser
from typing import Final
from uuid import UUID

from karria.modules.offers.domain.normalization import RawPosting, clean_title, fold

# ---------------------------------------------------------------------
# La clé de nom
# ---------------------------------------------------------------------

#: Formes juridiques retirées de la clé — jamais du nom affiché. Mots
#: entiers seulement : « SAS » s'enlève, « Sasha » reste.
LEGAL_FORMS: Final = frozenset(
    {
        "sa",
        "sas",
        "sasu",
        "sarl",
        "eurl",
        "snc",
        "sca",
        "scs",
        "sci",
        "scop",
        "scic",
        "selarl",
        "selas",
        "gie",
        "gmbh",
        "ag",
        "ltd",
        "limited",
        "llc",
        "inc",
        "plc",
        "bv",
        "nv",
        "spa",
        "srl",
        "sl",
        "sprl",
    }
)
_NON_WORD: Final = re.compile(r"[^a-z0-9]+")
#: Une clé plus longue n'aide plus à reconnaître ; la colonne fait 255.
KEY_MAX_LENGTH: Final = 200


def company_key(name: str | None) -> str | None:
    """« DECATHLON S.A.S. » → « decathlon » ; `None` si rien ne reste."""
    if not name:
        return None
    # « S.A.S. » et « S.A.R.L » se recollent avant le découpage en mots.
    folded = re.sub(
        r"\b((?:[a-z]\.){2,}[a-z]?\.?)", lambda m: m.group(1).replace(".", ""), fold(name)
    )
    words = [w for w in _NON_WORD.split(folded) if w]
    kept = [w for w in words if w not in LEGAL_FORMS] or words
    key = " ".join(kept)[:KEY_MAX_LENGTH].strip()
    return key or None


# ---------------------------------------------------------------------
# Le domaine
# ---------------------------------------------------------------------

#: Domaines qui ne disent **rien** de l'employeur : sites d'offres,
#: réseaux sociaux, logiciels de recrutement, hébergeurs de sites. Un lien
#: vers eux n'identifie pas l'entreprise, et leur icône n'est pas la sienne.
NON_COMPANY_DOMAINS: Final = frozenset(
    {
        "indeed.com",
        "indeed.fr",
        "linkedin.com",
        "licdn.com",
        "hellowork.com",
        "francetravail.fr",
        "francetravail.org",
        "pole-emploi.fr",
        "welcometothejungle.com",
        "welcomekit.co",
        "glassdoor.fr",
        "glassdoor.com",
        "monster.fr",
        "apec.fr",
        "cadremploi.fr",
        "jobteaser.com",
        "meteojob.com",
        "regionsjob.com",
        "jobijoba.com",
        "optioncarriere.com",
        "talent.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "google.com",
        "gstatic.com",
        "myworkdayjobs.com",
        "workday.com",
        "smartrecruiters.com",
        "lever.co",
        "greenhouse.io",
        "teamtailor.com",
        "recruitee.com",
        "taleo.net",
        "successfactors.com",
        "successfactors.eu",
        "icims.com",
        "jobvite.com",
        "breezy.hr",
        "workable.com",
        "flatchr.io",
        "digitalrecruiters.com",
        "talent-soft.com",
        "softy.pro",
        "jobaffinity.fr",
        "beetween.com",
        "werecruit.io",
        "join.com",
        "wixsite.com",
        "wix.com",
        "wordpress.com",
        "free.fr",
        "orange.fr",
        "gmail.com",
        "societe.com",
        "pagesjaunes.fr",
        # Logiciels et plateformes de recrutement vus en production (2026-10-25).
        "mytalentplug.com",
        "lindustrie-recrute.fr",
        "taleez.com",
        "jobposting.pro",
        "aplitrak.com",
        "free-work.com",
        "nous-recrutons.fr",
        "crechemploi.fr",
        "xtramile.io",
        "staffsante.fr",
        "aladom.fr",
    }
)
#: Suffixes publics à deux niveaux qu'on rencontre : `x.gouv.fr`,
#: `x.co.uk`. Sans la liste complète des suffixes publics, une liste courte
#: suffit — se tromper rend un domaine trop court, jamais celui d'un autre.
TWO_LEVEL_SUFFIXES: Final = frozenset(
    {
        "gouv.fr",
        "asso.fr",
        "com.fr",
        "co.uk",
        "org.uk",
        "ac.uk",
        "com.au",
        "co.jp",
        "com.br",
        "co.za",
        "com.tr",
        "com.cn",
        "co.in",
        "com.mx",
    }
)


def registrable_domain(url: str | None) -> str | None:
    """L'hôte d'une URL ramené à son domaine enregistrable : « jobs.decathlon.fr » → « decathlon.fr »."""
    if not url:
        return None
    candidate = url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        host = (urllib.parse.urlsplit(candidate).hostname or "").strip(".").lower()
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    labels = host.split(".")
    size = 3 if ".".join(labels[-2:]) in TWO_LEVEL_SUFFIXES else 2
    if len(labels) < size:
        return None
    return ".".join(labels[-size:])


def company_domain(url: str | None) -> str | None:
    """Le domaine qui identifie l'employeur, ou `None` si le lien mène ailleurs (site d'offres…)."""
    domain = registrable_domain(url)
    if domain is None or domain in NON_COMPANY_DOMAINS:
        return None
    return domain


# ---------------------------------------------------------------------
# L'entreprise et ce qu'on en voit
# ---------------------------------------------------------------------


class LogoStatus(StrEnum):
    """Où en est la recherche du logo d'une entreprise."""

    #: Un candidat attend d'être essayé (ou réessayé, après un échec passager).
    PENDING = "pending"
    #: Un logo normalisé est en stock.
    READY = "ready"
    #: Tout a été essayé, rien d'acceptable : l'app montre les initiales.
    MISSING = "missing"


class LogoOrigin(StrEnum):
    """D'où vient le logo retenu — l'ordre est celui de la confiance."""

    SOURCE = "source"
    V1 = "v1"
    FAVICON = "favicon"


@dataclass(frozen=True, slots=True)
class CompanySighting:
    """Une entreprise telle qu'une annonce (ou la V1) la montre."""

    key: str
    name: str
    domain: str | None = None
    website: str | None = None
    logo_url: str | None = None
    #: L'annonce qui l'a montrée, pour l'y rattacher : (source, identifiant).
    offer: tuple[str, str] | None = None
    v1_id: int | None = None


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    """Une entreprise connue de Karria."""

    id: UUID
    key: str
    name: str
    #: Vide quand on ne le connaît pas (la colonne est non nulle : l'unicité
    #: `(key, domain)` doit tenir aussi sans domaine).
    domain: str = ""
    website: str | None = None
    #: Le dernier logo qu'une source a donné, tel quel (jamais servi à l'app).
    source_logo_url: str | None = None
    v1_id: int | None = None
    v1_logo_url: str | None = None
    logo_hash: str | None = None
    logo_origin: LogoOrigin | None = None
    logo_status: LogoStatus = LogoStatus.PENDING
    logo_attempts: int = 0
    logo_next_attempt_at: datetime | None = None
    logo_checked_at: datetime | None = None
    #: Adresses déjà jugées inutilisables : on ne les retélécharge pas.
    logo_rejected_urls: tuple[str, ...] = ()

    @property
    def has_logo(self) -> bool:
        """Un logo est en stock."""
        return self.logo_status is LogoStatus.READY and self.logo_hash is not None


def sighting_of(posting: RawPosting) -> CompanySighting | None:
    """L'employeur que montre une annonce, ou `None` si elle n'en nomme pas.

    Le site vient d'abord du champ qui le dit (`company_website`, Indeed),
    puis du lien d'entreprise s'il ne mène pas à un site d'offres ; le lien
    pour postuler ne sert qu'à deviner le domaine (`emploi.decathlon.fr`).
    """
    if posting.expired or not posting.company_name:
        return None
    name = clean_title(posting.company_name)
    key = company_key(name)
    if key is None:
        return None
    website = next(
        (url for url in (posting.company_website, posting.company_url) if company_domain(url)),
        None,
    )
    domain = company_domain(website) or company_domain(posting.job_url_direct)
    return CompanySighting(
        key=key,
        name=name[:255],
        domain=domain,
        website=website[:2048] if website else None,
        logo_url=posting.company_logo_url,
        offer=(posting.source.value, posting.source_id),
    )


def choose_company(existing: Sequence[CompanyRecord], domain: str | None) -> CompanyRecord | None:
    """L'entreprise déjà connue que montre l'annonce, ou `None` pour en créer une.

    Même clé de nom, puis : même domaine ; sinon un homonyme sans domaine
    (qui l'adopte) ; sinon, sans domaine dans l'annonce, l'homonyme le plus
    ancien. Deux domaines différents font deux entreprises.
    """
    if not existing:
        return None
    if domain is None:
        return existing[0]
    for record in existing:
        if record.domain == domain:
            return record
    for record in existing:
        if not record.domain:
            return record
    return None


#: Rang de confiance d'une adresse de logo, pour en garder une seule par
#: entreprise : un logo carré d'Indeed vaut mieux qu'un bandeau.
_LOGO_HOST_RANK: Final[tuple[str, ...]] = ("cloudfront.net", "francetravail.fr", "hellowork.com")


def _rank(url: str) -> int:
    host = registrable_domain(url) or ""
    return next((i for i, h in enumerate(_LOGO_HOST_RANK) if host == h), len(_LOGO_HOST_RANK))


def new_company(company_id: UUID, sighting: CompanySighting, *, now: datetime) -> CompanyRecord:
    """L'entreprise créée par sa première apparition.

    Sans adresse de logo ni domaine, il n'y a rien à chercher : elle naît
    « sans logo » (initiales), et une apparition plus riche la remettra en
    recherche (`merge_sighting`).
    """
    record = merge_sighting(
        CompanyRecord(
            id=company_id, key=sighting.key, name=sighting.name, logo_next_attempt_at=now
        ),
        sighting,
        now=now,
    )
    if not record.domain and not logo_candidates(record):
        return replace(record, logo_status=LogoStatus.MISSING, logo_next_attempt_at=None)
    return record


def merge_sighting(
    record: CompanyRecord, sighting: CompanySighting, *, now: datetime
) -> CompanyRecord:
    """Ce qu'une nouvelle apparition apprend sur l'entreprise.

    On complète, on ne remplace pas : le domaine et le site se gardent dès
    qu'on les a. Une adresse de logo nouvelle, si l'on n'a pas encore de
    logo, remet la recherche en route — c'est un candidat de plus.
    """
    merged = record
    if not merged.domain and sighting.domain:
        merged = replace(merged, domain=sighting.domain)
        if merged.logo_status is LogoStatus.MISSING:
            # Un domaine connu, c'est une icône de site à essayer.
            merged = replace(merged, logo_status=LogoStatus.PENDING, logo_next_attempt_at=now)
    if merged.website is None and sighting.website:
        merged = replace(merged, website=sighting.website)
    if sighting.v1_id is not None and merged.v1_id is None:
        merged = replace(merged, v1_id=sighting.v1_id, v1_logo_url=sighting.logo_url)
        merged = _new_candidate(merged, sighting.logo_url, now=now)
    elif (
        sighting.v1_id is None and sighting.logo_url and sighting.logo_url != merged.source_logo_url
    ):
        better = merged.source_logo_url is None or _rank(sighting.logo_url) <= _rank(
            merged.source_logo_url
        )
        if better:
            merged = replace(merged, source_logo_url=sighting.logo_url)
            merged = _new_candidate(merged, sighting.logo_url, now=now)
    return merged


def _new_candidate(record: CompanyRecord, url: str | None, *, now: datetime) -> CompanyRecord:
    if url is None or record.has_logo or url in record.logo_rejected_urls:
        return record
    return replace(
        record, logo_status=LogoStatus.PENDING, logo_next_attempt_at=now, logo_attempts=0
    )


# ---------------------------------------------------------------------
# Les candidats et leur ordre
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LogoCandidate:
    """Une adresse à essayer, et ce qu'on en attend."""

    url: str
    origin: LogoOrigin


#: Adresses qu'on sait être des images génériques (silhouette LinkedIn,
#: « pas de logo ») : jamais téléchargées.
_PLACEHOLDER_URL: Final = re.compile(
    r"static\.licdn\.com|ghost|placeholder|default[-_]?(?:logo|company|avatar)|no[-_]?logo|nologo",
    re.IGNORECASE,
)


def is_placeholder_url(url: str) -> bool:
    """L'adresse désigne une image générique, pas un logo."""
    return bool(_PLACEHOLDER_URL.search(url))


def site_icon_urls(domain: str) -> tuple[str, ...]:
    """Les icônes d'un site quand sa page d'accueil n'en déclare pas (ou nous refuse : 403).

    L'icône conventionnelle, puis le service d'icônes de Google, qui rend
    l'`apple-touch-icon` (180 px) de bien des sites protégés contre les
    robots, et 404 pour un domaine inconnu. Téléchargée une fois, par le
    serveur : Google n'apprend qu'un domaine d'entreprise, jamais qui le
    regarde. Une icône trop petite reste refusée (`MIN_FAVICON_EDGE`).
    """
    return (
        f"https://{domain}/apple-touch-icon.png",
        f"https://www.google.com/s2/favicons?domain={domain}&sz=256",
    )


def logo_candidates(record: CompanyRecord) -> list[LogoCandidate]:
    """Les adresses à essayer, de la plus sûre à la moins sûre, sans les rejetées.

    D'abord le logo que donne la source, puis celui de la V1, et seulement
    ensuite l'icône du site (que l'adaptateur découvre sur la page d'accueil
    et complète par `site_icon_urls`). Une icône Google (`s2/favicons`) de
    la V1 est une icône de site : elle est jugée comme telle.
    """
    found: list[LogoCandidate] = []
    if record.source_logo_url:
        found.append(LogoCandidate(record.source_logo_url, LogoOrigin.SOURCE))
    if record.v1_logo_url:
        origin = LogoOrigin.FAVICON if "favicon" in record.v1_logo_url.lower() else LogoOrigin.V1
        found.append(LogoCandidate(record.v1_logo_url, origin))
    seen: set[str] = set()
    kept: list[LogoCandidate] = []
    for candidate in found:
        url = candidate.url
        if url in seen or url in record.logo_rejected_urls or is_placeholder_url(url):
            continue
        if not url.lower().startswith(("https://", "http://")):
            continue
        seen.add(url)
        kept.append(candidate)
    return kept


class _IconLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        rel = values.get("rel", "").lower()
        if "icon" in rel and values.get("href"):
            self.links.append((rel, values["href"], values.get("sizes", "").lower()))


def _declared_size(sizes: str) -> int:
    best = 0
    for token in sizes.split():
        width, _, _ = token.partition("x")
        if width.isdigit():
            best = max(best, int(width))
    return best


def icon_links(html: str, base_url: str) -> list[str]:
    """Les icônes qu'une page d'accueil déclare, de la plus grande à la plus petite.

    `apple-touch-icon` (180 px en général) passe avant une icône de même
    taille déclarée ; le SVG est écarté (rien ne le rastérise ici).
    """
    parser = _IconLinks()
    try:
        parser.feed(html[:512_000])
    except (ValueError, AssertionError):  # pragma: no cover - HTMLParser tolère presque tout
        return []
    ranked: list[tuple[int, int, str]] = []
    for rel, href, sizes in parser.links:
        url = urllib.parse.urljoin(base_url, href.strip())
        if not url.startswith(("https://", "http://")) or url.lower().split("?")[0].endswith(
            ".svg"
        ):
            continue
        apple = 1 if "apple-touch-icon" in rel else 0
        size = _declared_size(sizes) or (180 if apple else 0)
        ranked.append((size, apple, url))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return list(dict.fromkeys(url for _, _, url in ranked))


# ---------------------------------------------------------------------
# Le jugement d'une image
# ---------------------------------------------------------------------

#: Plus grand côté minimal d'un logo donné par une source ou la V1, en
#: pixels. HelloWork sert des bandeaux de 160 x 60, Indeed des carrés de
#: 256 : 48 garde les deux et écarte les vignettes inutilisables.
MIN_SOURCE_EDGE: Final = 48
#: Plus grand côté minimal d'une icône de site. Une icône de 16, 32 ou 48
#: pixels agrandie sur une tuile de 54 points à 3x est floue : les
#: initiales sont plus belles. `apple-touch-icon` (180) passe.
MIN_FAVICON_EDGE: Final = 96
#: Rapport largeur / hauteur maximal (un bandeau plus allongé, une fois mis
#: au carré, n'est plus qu'un trait).
MAX_ASPECT_RATIO: Final = 5.0
#: Sous cet écart-type de luminance (0-255) et sans transparence qui
#: dessine une forme, l'image est un aplat : une image vide ou générique.
MIN_CONTRAST: Final = 6.0


@dataclass(frozen=True, slots=True)
class ImageTraits:
    """Ce que l'adaptateur image a mesuré, après rognage des bords unis."""

    width: int
    height: int
    #: Écart-type de la luminance des pixels visibles.
    contrast: float
    #: Part des pixels visibles (non transparents), entre 0 et 1.
    coverage: float = 1.0


class LogoRejection(StrEnum):
    """Pourquoi une image n'est pas un logo qu'on affiche."""

    TOO_SMALL = "too_small"
    TOO_WIDE = "too_wide"
    BLANK = "blank"
    PLACEHOLDER = "placeholder"
    UNREADABLE = "unreadable"


def judge_logo(traits: ImageTraits, origin: LogoOrigin) -> LogoRejection | None:
    """`None` si l'image fait un logo présentable ; sinon la raison du refus."""
    long_edge = max(traits.width, traits.height)
    short_edge = min(traits.width, traits.height)
    floor = MIN_FAVICON_EDGE if origin is LogoOrigin.FAVICON else MIN_SOURCE_EDGE
    if short_edge <= 1 or long_edge < floor:
        return LogoRejection.TOO_SMALL
    if long_edge / short_edge > MAX_ASPECT_RATIO:
        return LogoRejection.TOO_WIDE
    if traits.coverage < 0.01 or (traits.contrast < MIN_CONTRAST and traits.coverage > 0.98):  # noqa: PLR2004 - presque rien de visible
        return LogoRejection.BLANK
    return None


#: Un domaine porté par plus d'entreprises que cela n'est le site d'aucune :
#: la colonne `website` de la V1 en est pleine (`microsoft.com` pour 632
#: entreprises, `zhihu.com`, `dict.cc`…, mesuré le 2026-10-25). Son icône
#: n'est alors jamais cherchée.
MAX_COMPANIES_PER_DOMAIN: Final = 3
#: Un logo de la V1 ou d'icône de site porté par plus d'entreprises (clés
#: différentes) que cela est une image générique ou une erreur de la V1 :
#: il est retiré à toutes (`purge_shared_logos`). Les logos des sources
#: (franchises : « McDonald's Paris 15 ») ne sont pas concernés.
MAX_SHARED_FALLBACK_LOGO: Final = 5
#: Durée maximale de la recherche pour une entreprise : au-delà, l'essai
#: compte comme un échec passager (un site qui égrène ses octets ne bloque
#: plus tout un passage — constaté au premier rattrapage).
COMPANY_DEADLINE_SECONDS: Final = 60.0

#: Condensats d'images génériques constatées dans le stock (une même image
#: servie pour des centaines d'entreprises sans rapport). Mesurés, jamais
#: devinés : voir `python -m scripts.companies shared-logos`.
PLACEHOLDER_HASHES: Final[frozenset[str]] = frozenset()


# ---------------------------------------------------------------------
# Les reprises
# ---------------------------------------------------------------------

#: Délais entre deux essais après un échec **passager** (délai dépassé,
#: 5xx, 429) : une heure, six heures, un jour, trois jours, puis on renonce
#: jusqu'à ce qu'un nouveau candidat apparaisse.
RETRY_DELAYS: Final[tuple[timedelta, ...]] = (
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(days=1),
    timedelta(days=3),
)
#: Le bail d'une entreprise prise en charge : passé ce délai sans résultat
#: (worker tué), elle redevient disponible.
CLAIM_LEASE: Final = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class LogoAttempt:
    """Ce qu'un essai a donné, que la règle transforme en état de l'entreprise."""

    logo_hash: str | None = None
    origin: LogoOrigin | None = None
    rejected_urls: tuple[str, ...] = ()
    transient_failure: bool = False


#: Adresses rejetées gardées au plus par entreprise.
MAX_REJECTED_URLS: Final = 12


def settle_logo(record: CompanyRecord, attempt: LogoAttempt, *, now: datetime) -> CompanyRecord:
    """L'état de l'entreprise après un essai : prêt, à réessayer plus tard, ou sans logo."""
    rejected = tuple(dict.fromkeys((*record.logo_rejected_urls, *attempt.rejected_urls)))[
        -MAX_REJECTED_URLS:
    ]
    base = replace(record, logo_checked_at=now, logo_rejected_urls=rejected)
    if attempt.logo_hash is not None:
        return replace(
            base,
            logo_hash=attempt.logo_hash,
            logo_origin=attempt.origin,
            logo_status=LogoStatus.READY,
            logo_attempts=0,
            logo_next_attempt_at=None,
        )
    if attempt.transient_failure and record.logo_attempts < len(RETRY_DELAYS):
        return replace(
            base,
            logo_status=LogoStatus.PENDING,
            logo_attempts=record.logo_attempts + 1,
            logo_next_attempt_at=now + RETRY_DELAYS[record.logo_attempts],
        )
    return replace(
        base,
        logo_status=LogoStatus.READY if record.has_logo else LogoStatus.MISSING,
        logo_next_attempt_at=None,
    )


#: Les deux tailles servies. 128 pour les cartes (une tuile de 54 points en
#: 2x), 256 pour le détail et les écrans 3x.
LOGO_SIZES: Final[tuple[int, ...]] = (128, 256)


def logo_object_key(logo_hash: str, size: int) -> str:
    """La clé de stockage d'une taille : le condensat versionne, l'objet ne change jamais."""
    return f"{logo_hash}/{size}.webp"
