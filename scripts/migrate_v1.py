"""Migration des comptes V1 et de leurs critères de recherche vers la V2.

    uv run python -m scripts.migrate_v1 export  --dsn postgresql://… --out v1_export.json
    uv run python -m scripts.migrate_v1 convert --in v1_export.json --out v1_criteria.json [--llm]
    uv run python -m scripts.migrate_v1 import  --in v1_criteria.json [--apply]

Trois étapes, trois fichiers, pour que chacune se relise avant la suivante :

- `export` lit la sauvegarde V1 (restaurée dans une base de travail, jamais
  la production) et écrit **un enregistrement par compte** : adresse en
  minuscules, drapeaux, jeton push, et la matière première des critères
  (intention en texte libre, mots-clés, villes, contrats, expérience,
  termes de recherche canoniques les plus fréquents). Ni mot de passe, ni
  nom, ni téléphone, ni identifiant OAuth : ce qui n'est pas exporté ne
  peut pas fuir.
- `convert` fait de cette matière des `SearchCriteria` V2 validés par le
  schéma. Déterministe d'abord ; `--llm` demande ensuite au même dialogue
  que l'app (`SearchDialogue`) d'affiner à partir de l'intention en texte
  libre, et garde le résultat déterministe si le modèle ne rend rien de
  cherchable (AGENTS.md §6.14 : la sortie d'un modèle est une donnée).
- `import` est une **simulation** tant que `--apply` n'est pas donné. Avec
  `--apply`, il crée les comptes manquants (adresse **vérifiée** en V1
  seulement — un compte V2 n'existe qu'avec une adresse prouvée),
  déclare l'appareil push, et applique les critères par `ApplyCriteria`,
  qui construit le fil et inscrit la demande au planificateur. Idempotent :
  un compte existant n'est pas recréé, un appareil déjà déclaré est
  simplement revu, des critères identiques (même empreinte) ne sont pas
  réappliqués.

Ce que ce script ne fait pas, et pourquoi
-----------------------------------------
- Il ne stocke pas les jetons FCM bruts : l'expéditeur V2 est Expo, qui
  ne connaît que les `ExponentPushToken[…]`. Un jeton brut serait envoyé,
  refusé, puis détaché au premier passage — une écriture pour rien. Ils
  sont comptés, pas importés (voir `docs/PLUS-TARD.md`).
- Il ne publie pas `UserRegistered` : il n'y a pas de bus hors de l'API,
  et aucun abonné n'a aujourd'hui besoin de ce fait pour un compte importé.
- Il n'écrit que des **comptes** dans les journaux et sur la sortie : jamais
  une adresse, jamais un jeton.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg.rows import dict_row

from karria.bootstrap.orm import TORTOISE_ORM
from karria.modules.devices.api.dependencies import get_clock as get_devices_clock
from karria.modules.devices.api.dependencies import get_device_repository, get_register_device
from karria.modules.devices.application import DeviceRepository, RegisterDevice
from karria.modules.devices.domain import DeviceInfo, Platform, PushPermission
from karria.modules.identity.api.dependencies import get_clock, get_user_repository
from karria.modules.identity.application import UserRepository
from karria.modules.identity.domain import Consent, EmailAddress, InvalidEmailError, User
from karria.modules.offers import build_apply_criteria
from karria.modules.offers.api.dependencies import get_criteria_repository, get_search_dialogue
from karria.modules.offers.application import ApplyCriteria, CriteriaRepository
from karria.modules.offers.domain import (
    DEFAULT_RADIUS_KM,
    ContractType,
    CriteriaIncompleteError,
    ExperienceLevel,
    LocationCriteria,
    ModelOutputInvalidError,
    ModelUnavailableError,
    SearchCriteria,
)
from karria.shared.clock import Clock
from karria.shared.config import get_settings
from karria.shared.database import TortoiseUnitOfWork, UnitOfWork, connect, disconnect
from karria.shared.llm import build_language_model
from karria.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

# =====================================================================
# Partie pure : la correspondance V1 → V2, testable sans base ni réseau
# =====================================================================

#: Version de consentement posée sur les comptes importés. Les CGU et la
#: politique de confidentialité acceptées en V1 ne portaient pas de
#: version : « v1-import » dit d'où vient l'acceptation, et l'app
#: redemandera la version courante à la première ouverture.
IMPORT_CONSENT_VERSION: Final = "v1-import"
#: Ce que l'appareil importé déclare de lui-même : la V1 ne gardait ni
#: version d'OS ni version d'app, et ces champs sont obligatoires.
IMPORT_APP_VERSION: Final = "v1-import"
IMPORT_OS_VERSION: Final = "inconnue"
#: Termes de recherche canoniques gardés par personne, les plus fréquents d'abord.
TOP_TERMS_MAX: Final = 10
#: Intitulés retenus dans les critères : les plus fréquents, pas la liste entière.
TITLES_MAX: Final = 5
#: Longueur de l'intention en texte libre reprise dans la requête sémantique.
SEMANTIC_MAX_LENGTH: Final = 1_000
QUERY_MAX_LENGTH: Final = 200
#: Secteurs V1 repris comme préférences (jamais comme filtre).
PREFERENCES_MAX: Final = 4

#: Codes de contrat V1 (`contract_types.code`) → V2. Les codes absents —
#: franchise, reprise d'entreprise, contrat de test, « non spécifié » — ne
#: désignent pas un contrat que la V2 filtre : ils tombent.
CONTRACT_MAP: Final[Mapping[str, ContractType]] = {
    "CDI": ContractType.CDI,
    "DIN": ContractType.CDI,  # CDI intérimaire : un CDI
    "CDD": ContractType.CDD,
    "DDI": ContractType.CDD,  # CDD d'insertion
    "DDT": ContractType.CDD,  # CDD tremplin
    "CCE": ContractType.CDD,  # contrat de chantier : à durée limitée
    "MIS": ContractType.INTERIM,
    "TTI": ContractType.INTERIM,  # travail temporaire d'insertion
    "SAI": ContractType.SAISONNIER,
    "LIB": ContractType.FREELANCE,
    "ALT": ContractType.ALTERNANCE,
    "STA": ContractType.STAGE,
}

#: Niveaux d'expérience V1 (`experience_levels.code`, ou le libellé libre de
#: `search_intent_keywords.experience`) → les quatre paliers V2. Expert et
#: cadre dirigeant se rangent sous « senior » : la V2 n'a pas plus haut.
EXPERIENCE_MAP: Final[Mapping[str, ExperienceLevel]] = {
    "BEGINNER": ExperienceLevel.BEGINNER,
    "DEBUTANT": ExperienceLevel.BEGINNER,
    "JUNIOR": ExperienceLevel.JUNIOR,
    "INTERMEDIATE": ExperienceLevel.CONFIRMED,
    "INTERMEDIAIRE": ExperienceLevel.CONFIRMED,
    "CONFIRME": ExperienceLevel.CONFIRMED,
    "SENIOR": ExperienceLevel.SENIOR,
    "EXPERT": ExperienceLevel.SENIOR,
    "EXECUTIVE": ExperienceLevel.SENIOR,
}

#: Termes que la V1 rangeait parmi les recherches et qui ne nomment pas un
#: métier : des contrats, des rythmes, des modes. Mesuré sur la sauvegarde
#: du 2026-09-12 : « temps plein » était le terme le plus fréquent (18
#: profils sur 23). Un intitulé de poste ne peut pas être « CDI ».
NON_TITLE_TERMS: Final[frozenset[str]] = frozenset(
    {
        "cdi",
        "cdd",
        "interim",
        "mission interimaire",
        "cdi interimaire",
        "temps plein",
        "temps partiel",
        "alternance",
        "apprentissage",
        "stage",
        "freelance",
        "teletravail",
        "vacation",
        "saisonnier",
        "contrat saisonnier",
        "independant",
        "liberal",
    }
)

_EXPO_TOKEN: Final = re.compile(r"^Expo(?:nent)?PushToken\[[A-Za-z0-9_\-]{1,200}\]$")
#: Un jeton d'enregistrement FCM : un identifiant d'instance, deux-points,
#: une charge opaque (`APA91b…`), autour de 140 à 165 caractères.
_FCM_TOKEN: Final = re.compile(r"^[A-Za-z0-9_\-]{10,}:[A-Za-z0-9_\-]{50,}$")


class TokenKind(StrEnum):
    """La forme d'un jeton push V1, qui décide s'il est utilisable par Expo."""

    EXPO = "expo"
    FCM = "fcm"
    NONE = "none"
    OTHER = "other"

    @property
    def usable(self) -> bool:
        """Expo n'achemine que ses propres jetons."""
        return self is TokenKind.EXPO


def classify_token(token: str | None) -> TokenKind:
    """Dit de quelle famille est un jeton, sans jamais le reproduire."""
    if token is None or not token.strip():
        return TokenKind.NONE
    if _EXPO_TOKEN.match(token):
        return TokenKind.EXPO
    if _FCM_TOKEN.match(token):
        return TokenKind.FCM
    return TokenKind.OTHER


def normalise_email(raw: str) -> str:
    """Minuscules, sans blancs : la même normalisation que `EmailAddress`."""
    return raw.strip().lower()


def fold_term(value: str) -> str:
    """Minuscules, sans accents, blancs réduits — pour comparer des termes."""
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )
    return " ".join(stripped.casefold().split())


def map_contract(code: str | None) -> ContractType | None:
    """Un code V1 → un contrat V2, ou `None` si la V2 ne le filtre pas."""
    if code is None:
        return None
    return CONTRACT_MAP.get(code.strip().upper())


def map_contracts(codes: Iterable[str | None]) -> list[ContractType]:
    """Les contrats V2, dédoublonnés, dans l'ordre d'apparition."""
    seen: dict[ContractType, None] = {}
    for code in codes:
        mapped = map_contract(code)
        if mapped is not None:
            seen.setdefault(mapped, None)
    return list(seen)


def map_experience(*labels: str | None) -> ExperienceLevel | None:
    """Le premier libellé reconnu, parmi un code V1 ou un libellé libre."""
    for label in labels:
        if label is None:
            continue
        key = fold_term(label).upper()
        if key in EXPERIENCE_MAP:
            return EXPERIENCE_MAP[key]
    return None


def is_title_term(term: str) -> bool:
    """Le terme nomme-t-il un métier, et non un contrat ou un rythme ?"""
    folded = fold_term(term)
    return bool(folded) and folded not in NON_TITLE_TERMS


def rank_terms(rows: Iterable[tuple[int, str]]) -> dict[int, list[dict[str, Any]]]:
    """Par profil, les termes les plus demandés (`{"term", "count"}`), au plus `TOP_TERMS_MAX`.

    Les termes sont comparés repliés (« Serveur » et « serveur » ne font
    qu'un) ; la première graphie rencontrée est celle qu'on garde.
    """
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    spellings: dict[str, str] = {}
    for profile_id, term in rows:
        folded = fold_term(term)
        if not folded:
            continue
        spellings.setdefault(folded, " ".join(term.split()))
        counts[profile_id][folded] += 1
    return {
        profile_id: [
            {"term": spellings[folded], "count": count}
            for folded, count in counter.most_common(TOP_TERMS_MAX)
        ]
        for profile_id, counter in counts.items()
    }


def pick_titles(top_terms: Sequence[Mapping[str, Any]], keywords: Sequence[str]) -> list[str]:
    """Les intitulés visés : les termes de recherche d'abord, les mots-clés V1 ensuite.

    Les termes canoniques disent ce que la V1 a réellement cherché pour la
    personne ; les mots-clés de l'intention complètent quand ils sont trop
    peu nombreux. Les contrats et rythmes sont écartés.
    """
    chosen: dict[str, str] = {}
    candidates = [str(item["term"]) for item in top_terms] + [str(k) for k in keywords]
    for candidate in candidates:
        cleaned = " ".join(candidate.split())
        if not is_title_term(cleaned):
            continue
        chosen.setdefault(fold_term(cleaned), cleaned)
        if len(chosen) >= TITLES_MAX:
            break
    return list(chosen.values())


def location_from(source: Mapping[str, Any]) -> tuple[LocationCriteria | None, bool]:
    """Le lieu V2 : la première ville choisie, sinon la position courante ; ou toute la France.

    Les villes de `profiles_cities` sont celles que la personne a cochées ;
    `profile_search.city_ids` en est l'expansion par zone (jusqu'à 1 447
    villes dans la sauvegarde) et ne dit pas où elle habite.
    """
    if source.get("whole_france"):
        return None, True
    cities: Sequence[Mapping[str, Any]] = source.get("cities") or []
    if cities:
        city = cities[0]
        return _city_location(city), False
    current: Mapping[str, Any] | None = source.get("current_city")
    if current and current.get("lat") is not None and current.get("lng") is not None:
        return _city_location(current), False
    return None, False


def _city_location(city: Mapping[str, Any]) -> LocationCriteria:
    name = str(city.get("name") or "").strip()
    department = city.get("department_code")
    label = f"{name} ({department})" if name and department else name or "lieu V1"
    return LocationCriteria(
        label=label,
        latitude=city.get("lat"),
        longitude=city.get("lng"),
        radius_km=DEFAULT_RADIUS_KM,
        department_code=str(department) if department else None,
        city=name or None,
    )


def deterministic_criteria(source: Mapping[str, Any] | None) -> SearchCriteria | None:
    """Les critères V2 déduits sans modèle de langage, ou `None` sans profil V1.

    Peut rendre des critères **incomplets** (sans métier ou sans lieu) :
    c'est `import` qui tranche, en les comptant plutôt qu'en les appliquant.
    """
    if source is None:
        return None
    keywords: Mapping[str, Any] = source.get("keywords") or {}
    titles = pick_titles(source.get("top_search_terms") or [], keywords.get("keywords") or [])
    location, whole_france = location_from(source)
    contracts = map_contracts(source.get("contract_codes") or []) or map_contracts(
        keywords.get("contract_codes") or []
    )
    intent = str(source.get("search_intent_raw") or "").strip()
    summary = str(keywords.get("summary") or "").strip()
    query = summary or intent[:QUERY_MAX_LENGTH] or (titles[0] if titles else "Recherche V1")
    sectors = [str(s) for s in (keywords.get("sectors") or [])][:PREFERENCES_MAX]
    return SearchCriteria.model_validate(
        {
            "query": query,
            "job_titles": titles,
            "search_terms": titles,
            "semantic_query": (intent or summary)[:SEMANTIC_MAX_LENGTH],
            "preferences": sectors,
            "location": location.model_dump() if location else None,
            "whole_france": whole_france,
            "contract_types": [c.value for c in contracts],
            "max_experience": map_experience(
                source.get("experience_code"), keywords.get("experience")
            ),
        }
    )


def searchable_once_located(criteria: SearchCriteria) -> bool:
    """Cherchable **après** géocodage : un métier, et un lieu nommé, toute la France ou le télétravail seul.

    `SearchCriteria.is_searchable` exige un point ou un département ; le
    modèle, lui, nomme le lieu sans le situer, et c'est `ApplyCriteria.locate`
    qui le géocode à l'import. Juger une proposition sur la règle stricte
    reviendrait à ne jamais la retenir.
    """
    if not criteria.job_titles:
        return False
    if criteria.whole_france or criteria.wants_remote_only:
        return True
    return criteria.location is not None and bool(criteria.location.label.strip())


def refine_with_llm(
    base: SearchCriteria | None, proposed: SearchCriteria | None
) -> tuple[SearchCriteria | None, str]:
    """Combine la lecture du modèle et les faits V1 ; rend `(critères, provenance)`.

    Le modèle comprend mieux le **métier** qu'une liste de termes ; la V1
    connaît mieux le **lieu** (coordonnées exactes), les **contrats** et
    l'**expérience** (choisis dans des listes). On prend donc la proposition
    du modèle quand elle est cherchable, et on la complète par ce que la V1
    sait de source sûre quand le modèle n'en dit rien.
    """
    if proposed is None or not searchable_once_located(proposed):
        return base, "deterministic" if base is not None else "none"
    if base is None:
        return proposed, "llm"
    updates: dict[str, Any] = {}
    if base.location is not None and (
        proposed.location is None or not proposed.location.is_geocoded
    ):
        updates["location"] = base.location
        updates["whole_france"] = False
    if not proposed.contract_types and base.contract_types:
        updates["contract_types"] = list(base.contract_types)
    if proposed.max_experience is None and base.max_experience is not None:
        updates["max_experience"] = base.max_experience
    merged = proposed.model_copy(update=updates) if updates else proposed
    return SearchCriteria.model_validate(merged.model_dump()), "llm+v1"


def installation_id_for(token: str) -> str:
    """Un identifiant d'installation stable pour un jeton V1 : rejouer l'import revoit le même appareil."""
    return str(uuid5(NAMESPACE_URL, f"karria-v1-push:{token}"))


def parse_moment(value: str | None) -> datetime | None:
    """Relit une date ISO ; une date sans fuseau est réputée UTC (la V1 écrivait en UTC)."""
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def build_export_record(
    *,
    user: Mapping[str, Any],
    profile: Mapping[str, Any] | None,
    cities: Sequence[Mapping[str, Any]],
    contract_codes: Sequence[str],
    experience_code: str | None,
    top_terms: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble un enregistrement d'export, sans rien reprendre d'identifiant au-delà de l'adresse."""
    token = user.get("fcm_token")
    push = None
    if token:
        push = {
            "token": token,
            "kind": classify_token(token).value,
            "provider": user.get("push_provider"),
            "platform": user.get("push_platform"),
            "updated_at": _iso(user.get("push_token_updated_at")),
        }
    criteria = None
    if profile is not None:
        current = None
        if profile.get("current_latitude") is not None:
            current = {
                "name": profile.get("current_city_name"),
                "postal_code": profile.get("current_postal_code"),
                "lat": profile.get("current_latitude"),
                "lng": profile.get("current_longitude"),
            }
        criteria = {
            "search_intent_raw": profile.get("search_intent_raw") or "",
            "keywords": _public_keywords(profile.get("search_intent_keywords") or {}),
            "whole_france": bool(profile.get("whole_france")),
            "cities": [
                {
                    "name": c.get("name"),
                    "postal_code": c.get("postal_code"),
                    "lat": c.get("latitude"),
                    "lng": c.get("longitude"),
                    "department_code": c.get("department_code"),
                }
                for c in cities
            ],
            "current_city": current,
            "contract_codes": list(contract_codes),
            "experience_code": experience_code,
            "notifications_enabled": bool(profile.get("notifications_enabled")),
            "top_search_terms": list(top_terms),
        }
    return {
        "v1_user_id": user["id"],
        "email": normalise_email(str(user["email"])),
        "email_verified": bool(user.get("is_email_verified")),
        "is_active": bool(user.get("is_active")),
        "oauth_provider": user.get("oauth_provider"),
        "created_at": _iso(user.get("created_at")),
        "last_login": _iso(user.get("last_login")),
        "push": push,
        "criteria": criteria,
    }


#: Les clés de `search_intent_keywords` qui décrivent la recherche. Le reste
#: (`_intent_model`, salaires) n'a pas d'équivalent V2 ou ne la pilote pas.
_KEYWORD_KEYS: Final = ("keywords", "job_types", "competences", "sectors", "contract_codes")


def _public_keywords(raw: Mapping[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {
        key: [str(v) for v in raw.get(key) or []] for key in _KEYWORD_KEYS if raw.get(key)
    }
    for key in ("experience", "summary"):
        if raw.get(key):
            kept[key] = str(raw[key])
    return kept


def convert_record(
    record: Mapping[str, Any], *, proposed: SearchCriteria | None = None
) -> dict[str, Any]:
    """Un enregistrement d'export → ses critères V2 et une note de provenance."""
    source = record.get("criteria")
    base = deterministic_criteria(source)
    criteria, origin = (
        refine_with_llm(base, proposed)
        if proposed is not None
        else (
            base,
            "deterministic" if base is not None else "none",
        )
    )
    notes: list[str] = []
    if criteria is not None and not searchable_once_located(criteria):
        notes.append("incomplete:" + ",".join(criteria.missing()))
    push = record.get("push")
    if push is not None and not TokenKind(push["kind"]).usable:
        notes.append(f"push_unusable:{push['kind']}")
    return {
        "v1_user_id": record["v1_user_id"],
        "email": record["email"],
        "email_verified": record["email_verified"],
        "created_at": record.get("created_at"),
        "last_login": record.get("last_login"),
        "notifications_enabled": bool(source and source.get("notifications_enabled")),
        "push": push,
        "criteria": criteria.model_dump(mode="json") if criteria is not None else None,
        "source": origin,
        "notes": notes,
    }


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """Ce que `import` ferait d'un enregistrement — décidé sans base, exécuté ensuite."""

    email: str
    skip_reason: str | None = None
    created_at: datetime | None = None
    last_login: datetime | None = None
    push_token: str | None = None
    push_platform: str | None = None
    installation_id: str | None = None
    criteria: SearchCriteria | None = None
    notes: tuple[str, ...] = field(default=())


def plan_import(record: Mapping[str, Any]) -> ImportPlan:
    """Décide, pour un enregistrement converti, ce qui s'importe et ce qui se compte."""
    email = normalise_email(str(record["email"]))
    if not record.get("email_verified"):
        return ImportPlan(email=email, skip_reason="unverified")
    notes: list[str] = []
    push = record.get("push")
    token = platform = installation = None
    if push is not None:
        if TokenKind(push["kind"]).usable and push.get("platform") in {"ios", "android"}:
            token, platform = str(push["token"]), str(push["platform"])
            installation = installation_id_for(token)
        else:
            notes.append(f"push_unusable:{push['kind']}")
    criteria = None
    raw = record.get("criteria")
    if raw is not None:
        candidate = SearchCriteria.model_validate(raw)
        if searchable_once_located(candidate):
            criteria = candidate
        else:
            notes.append("criteria_incomplete")
    return ImportPlan(
        email=email,
        created_at=parse_moment(record.get("created_at")),
        last_login=parse_moment(record.get("last_login")),
        push_token=token,
        push_platform=platform,
        installation_id=installation,
        criteria=criteria,
        notes=tuple(notes),
    )


def summarise_export(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Des comptes, rien d'autre : ce que l'export imprime."""
    tokens = Counter(
        (r["push"]["provider"] or "-", r["push"]["platform"] or "-", r["push"]["kind"])
        for r in records
        if r.get("push")
    )
    return {
        "users": len(records),
        "verified": sum(1 for r in records if r["email_verified"]),
        "with_profile": sum(1 for r in records if r.get("criteria")),
        "with_push_token": sum(1 for r in records if r.get("push")),
        "push_tokens": {"/".join(key): count for key, count in sorted(tokens.items())},
        "notifications_enabled": sum(
            1 for r in records if r.get("criteria") and r["criteria"]["notifications_enabled"]
        ),
    }


def summarise_convert(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Ce que la conversion imprime : des comptes et la provenance des critères."""
    searchable = [
        r
        for r in records
        if r["criteria"] and not any(n.startswith("incomplete") for n in r["notes"])
    ]
    return {
        "users": len(records),
        "with_criteria": sum(1 for r in records if r["criteria"]),
        "searchable": len(searchable),
        "incomplete": sum(1 for r in records if r["criteria"]) - len(searchable),
        "by_source": dict(Counter(r["source"] for r in records)),
        "push_unusable": sum(1 for r in records if any(n.startswith("push_") for n in r["notes"])),
    }


def anonymous_samples(records: Sequence[Mapping[str, Any]], *, limit: int = 3) -> list[str]:
    """Quelques critères en une ligne (`SearchCriteria.summary`), sans adresse."""
    lines: list[str] = []
    for record in records:
        if record["criteria"] is None:
            continue
        criteria = SearchCriteria.model_validate(record["criteria"])
        lines.append(f"[{record['source']}] {criteria.summary()}")
        if len(lines) >= limit:
            break
    return lines


# =====================================================================
# Partie I/O : la base V1, le dialogue, la V2
# =====================================================================

_USERS_SQL: Final = """
SELECT id, email, oauth_provider, is_active, is_email_verified, created_at, last_login,
       fcm_token, push_provider, push_platform, push_token_updated_at
FROM users ORDER BY id
"""
_PROFILES_SQL: Final = """
SELECT p.id, p.user_id, p.search_intent_raw, p.search_intent_keywords, p.whole_france,
       p.notifications_enabled, p.current_latitude, p.current_longitude, p.current_city_name,
       p.current_postal_code, e.code AS experience_code
FROM profiles p LEFT JOIN experience_levels e ON e.id = p.experience_level_id
ORDER BY p.id
"""
_CITIES_SQL: Final = """
SELECT pc.profiles_id, c.name, c.postal_code, c.latitude, c.longitude, c.department_code
FROM profiles_cities pc JOIN cities c ON c.id = pc.city_id
ORDER BY pc.profiles_id, c.id
"""
_CONTRACTS_SQL: Final = """
SELECT pct.profiles_id, ct.code
FROM profiles_contract_types pct JOIN contract_types ct ON ct.id = pct.contracttype_id
ORDER BY pct.profiles_id, ct.id
"""
_TERMS_SQL: Final = """
SELECT q.profile_id, c.search_term
FROM profile_search_queries q JOIN canonical_search_queries c ON c.id = q.query_id
"""


def _read_records(path: Path) -> list[dict[str, Any]]:  # pragma: no cover - fichier
    """Relit un fichier produit par l'étape précédente."""
    records: list[dict[str, Any]] = json.loads(path.read_text("utf-8"))
    return records


def _write_json(path: Path, payload: object) -> None:  # pragma: no cover - fichier
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), "utf-8")


def _export(dsn: str, out: Path) -> dict[str, Any]:  # pragma: no cover - lecture de la base V1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        users = conn.execute(_USERS_SQL).fetchall()
        profiles = conn.execute(_PROFILES_SQL).fetchall()
        city_rows = conn.execute(_CITIES_SQL).fetchall()
        contract_rows = conn.execute(_CONTRACTS_SQL).fetchall()
        term_rows = conn.execute(_TERMS_SQL).fetchall()

    profile_by_user = {p["user_id"]: p for p in profiles}
    cities: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in city_rows:
        cities[row["profiles_id"]].append(row)
    contracts: dict[int, list[str]] = defaultdict(list)
    for row in contract_rows:
        contracts[row["profiles_id"]].append(row["code"])
    terms = rank_terms((row["profile_id"], row["search_term"]) for row in term_rows)

    records = []
    for user in users:
        profile = profile_by_user.get(user["id"])
        pid = profile["id"] if profile else -1
        records.append(
            build_export_record(
                user=user,
                profile=profile,
                cities=cities.get(pid, []),
                contract_codes=contracts.get(pid, []),
                experience_code=profile["experience_code"] if profile else None,
                top_terms=terms.get(pid, []),
            )
        )
    _write_json(out, records)
    return summarise_export(records)


async def _propose(
    intent: str, base: SearchCriteria | None
) -> SearchCriteria | None:  # pragma: no cover - appel réseau
    """Le dialogue de l'app relit l'intention V1 ; un modèle muet ou hors cadre rend `None`."""
    dialogue = get_search_dialogue(build_language_model(get_settings()))
    try:
        proposal = await dialogue.propose(history=[], current=base, message=intent, memory=None)
    except (ModelUnavailableError, ModelOutputInvalidError):
        logger.warning("migrate_v1.llm_failed")
        return None
    return proposal.criteria


async def _convert(
    source: Path, out: Path, *, llm: bool
) -> dict[str, Any]:  # pragma: no cover - orchestration
    records = _read_records(source)
    converted = []
    for record in records:
        proposed = None
        criteria = record.get("criteria") or {}
        intent = str(criteria.get("search_intent_raw") or "").strip()
        if llm and intent:
            proposed = await _propose(intent, deterministic_criteria(criteria))
        converted.append(convert_record(record, proposed=proposed))
    _write_json(out, converted)
    return {**summarise_convert(converted), "samples": anonymous_samples(converted)}


@dataclass(frozen=True, slots=True)
class _Importer:  # pragma: no cover - écrit en base V2
    """Les adaptateurs réellement déployés, assemblés une fois pour tous les comptes."""

    users: UserRepository
    devices: DeviceRepository
    register: RegisterDevice
    criteria: CriteriaRepository
    applier: ApplyCriteria
    clock: Clock
    uow: UnitOfWork
    apply: bool
    counts: Counter[str]

    async def ensure_user(self, plan: ImportPlan) -> User | None:
        """Retrouve le compte par son adresse, ou le crée (`--apply`). `None` en simulation."""
        address = EmailAddress.parse(plan.email)
        user = await self.users.find_by_email(address)
        if user is not None:
            self.counts["users_existing"] += 1
            return user
        self.counts["users_to_create"] += 1
        if not self.apply:
            return None
        now = self.clock.now()
        born = plan.created_at or now
        user = User(
            id=uuid4(),
            email=address,
            email_verified_at=born,
            consent=Consent(
                terms_version=IMPORT_CONSENT_VERSION,
                privacy_policy_version=IMPORT_CONSENT_VERSION,
                accepted_at=born,
            ),
            created_at=born,
            updated_at=now,
            last_login_at=plan.last_login,
        )
        async with self.uow.atomic():
            await self.users.add(user)
        self.counts["users_created"] += 1
        return user

    async def ensure_device(self, plan: ImportPlan, user: User | None) -> None:
        """Déclare l'appareil V1 — seulement un jeton qu'Expo sait acheminer."""
        if plan.push_token is None or plan.installation_id is None:
            return
        existing = await self.devices.find(user.id, plan.installation_id) if user else None
        self.counts["devices_existing" if existing else "devices_to_create"] += 1
        if not self.apply or user is None:
            return
        platform = plan.push_platform or Platform.ANDROID.value
        await self.register.execute(
            user_id=user.id,
            installation_id=plan.installation_id,
            info=DeviceInfo(
                platform=Platform(platform),
                push_token=plan.push_token,
                push_permission=PushPermission.GRANTED,
                os_name=platform,
                os_version=IMPORT_OS_VERSION,
                is_physical_device=True,
                app_version=IMPORT_APP_VERSION,
            ),
            country=None,
        )
        self.counts["devices_registered"] += 1

    async def ensure_criteria(self, plan: ImportPlan, user: User | None) -> None:
        """Applique les critères par le même chemin que l'app ; une empreinte identique ne refait rien."""
        if plan.criteria is None:
            return
        current = await self.criteria.find(user.id) if user else None
        if current is not None and current.fingerprint == plan.criteria.fingerprint():
            self.counts["criteria_unchanged"] += 1
            return
        self.counts["criteria_to_apply"] += 1
        if not self.apply or user is None:
            return
        try:
            applied = await self.applier.execute(user.id, plan.criteria)
        except CriteriaIncompleteError:
            self.counts["criteria_incomplete_after_locate"] += 1
            return
        self.counts["criteria_applied"] += 1
        self.counts["feed_entries"] += applied.results

    async def run(self, record: Mapping[str, Any]) -> None:
        """Un compte : décider, puis compte, appareil, critères."""
        plan = plan_import(record)
        for note in plan.notes:
            self.counts[note] += 1
        if plan.skip_reason is not None:
            self.counts[f"skipped_{plan.skip_reason}"] += 1
            return
        try:
            user = await self.ensure_user(plan)
        except InvalidEmailError:
            self.counts["skipped_invalid_email"] += 1
            return
        await self.ensure_device(plan, user)
        await self.ensure_criteria(plan, user)


async def _import(
    source: Path, *, apply: bool
) -> dict[str, Any]:  # pragma: no cover - écrit en base V2
    settings = get_settings()
    configure_logging(settings.observability)
    records = _read_records(source)
    await connect(TORTOISE_ORM)
    try:
        uow = TortoiseUnitOfWork()
        devices = get_device_repository()
        importer = _Importer(
            users=get_user_repository(),
            devices=devices,
            register=get_register_device(devices, get_devices_clock(), uow),
            criteria=get_criteria_repository(),
            applier=build_apply_criteria(settings),
            clock=get_clock(),
            uow=uow,
            apply=apply,
            counts=Counter(),
        )
        for record in records:
            await importer.run(record)
        logger.info("migrate_v1.import_done", apply=apply, **dict(importer.counts))
    finally:
        await disconnect()
    return {"apply": apply, **dict(sorted(importer.counts.items()))}


async def main(argv: list[str]) -> int:  # pragma: no cover - point d'entrée
    """Analyse la ligne de commande et lance l'étape demandée."""
    parser = argparse.ArgumentParser(prog="scripts.migrate_v1", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="lit la base V1 et écrit un JSON par compte")
    export.add_argument("--dsn", required=True, help="DSN PostgreSQL de la base V1 de travail")
    export.add_argument("--out", required=True, type=Path)
    convert = commands.add_parser("convert", help="produit des SearchCriteria V2")
    convert.add_argument("--in", dest="source", required=True, type=Path)
    convert.add_argument("--out", required=True, type=Path)
    convert.add_argument("--llm", action="store_true", help="affine par le dialogue de l'app")
    importer = commands.add_parser("import", help="simulation ; --apply pour écrire")
    importer.add_argument("--in", dest="source", required=True, type=Path)
    importer.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    match args.command:
        case "export":
            report = _export(args.dsn, args.out)
        case "convert":
            report = await _convert(args.source, args.out, llm=args.llm)
        case "import":
            report = await _import(args.source, apply=args.apply)
        case _:
            parser.error("commande inconnue")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    sys.exit(asyncio.run(main(sys.argv[1:])))
