"""L'offre d'emploi telle que Karria la comprend — un seul modèle, normalisé.

Contrat
-------
Une offre arrive d'une source (Indeed, HelloWork, LinkedIn…) sous une forme
propre à cette source ; elle n'entre dans le domaine qu'après
normalisation (`domain/normalization.py`). Ici ne vivent que des champs
**compris** : un contrat parmi huit, un mode de télétravail, un niveau
d'expérience, un salaire mensuel en euros. La V1 gardait le `raw_data` de
la source et le relisait partout (sept clés différentes pour la ville) ; on
le garde aussi, mais seulement pour rejouer une normalisation, jamais pour
présenter.

Les identifiants sont des UUID v7 : ordonnés dans le temps, ils gardent
l'index primaire compact sur une table qui reçoit des dizaines de milliers
de lignes par jour (voir `shared/database/base.py`).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

#: Une offre publiée depuis plus longtemps est considérée pourvue ou
#: retirée : elle quitte le fil et l'index. Trente jours — la durée de vie
#: médiane d'une annonce ; la V1 appliquait la même et cela tenait.
OFFER_ACTIVE_TTL: Final = timedelta(days=30)


class Source(StrEnum):
    """D'où vient une offre : l'API officielle de France Travail, ou un site lu par JobSpy."""

    #: La source n°1 : officielle, incrémentale, nationale (ADR-0016 bis).
    FRANCE_TRAVAIL = "france_travail"
    INDEED = "indeed"
    HELLOWORK = "hellowork"
    LINKEDIN = "linkedin"
    GLASSDOOR = "glassdoor"
    GOOGLE = "google"
    #: Offres posées à la main ou par un test : jamais collectées.
    MANUAL = "manual"


class ContractType(StrEnum):
    """Les contrats que l'on sait reconnaître et sur lesquels on filtre."""

    CDI = "cdi"
    CDD = "cdd"
    INTERIM = "interim"
    ALTERNANCE = "alternance"
    STAGE = "stage"
    FREELANCE = "freelance"
    SAISONNIER = "saisonnier"
    AUTRE = "autre"

    @property
    def label(self) -> str:
        """Le libellé affiché."""
        return CONTRACT_LABELS[self]


CONTRACT_LABELS: Final[dict[ContractType, str]] = {
    ContractType.CDI: "CDI",
    ContractType.CDD: "CDD",
    ContractType.INTERIM: "Intérim",
    ContractType.ALTERNANCE: "Alternance",
    ContractType.STAGE: "Stage",
    ContractType.FREELANCE: "Freelance",
    ContractType.SAISONNIER: "Saisonnier",
    ContractType.AUTRE: "Autre",
}


class RemoteMode(StrEnum):
    """Télétravail, tel que l'offre l'annonce."""

    ON_SITE = "sur_site"
    HYBRID = "hybride"
    REMOTE = "teletravail"


class Schedule(StrEnum):
    """Temps de travail."""

    FULL_TIME = "temps_plein"
    PART_TIME = "temps_partiel"


class ExperienceLevel(StrEnum):
    """Expérience demandée, en quatre paliers ordonnés."""

    BEGINNER = "debutant"
    JUNIOR = "junior"
    CONFIRMED = "confirme"
    SENIOR = "senior"

    @property
    def rank(self) -> int:
        """Position dans l'échelle, pour comparer deux niveaux."""
        return _EXPERIENCE_RANK[self]


_EXPERIENCE_RANK: Final[dict[ExperienceLevel, int]] = {
    ExperienceLevel.BEGINNER: 0,
    ExperienceLevel.JUNIOR: 1,
    ExperienceLevel.CONFIRMED: 2,
    ExperienceLevel.SENIOR: 3,
}


@dataclass(frozen=True, slots=True)
class Location:
    """Où se trouve le poste, après géocodage.

    `label` est ce qu'on affiche (« Lyon 3e », « Nanterre (92) ») ; les
    coordonnées servent au filtre par distance. Une offre sans coordonnées
    n'est pas rejetée : elle n'apparaît simplement pas dans une recherche
    bornée par un rayon.
    """

    label: str
    city: str | None = None
    postal_code: str | None = None
    department_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @property
    def is_geocoded(self) -> bool:
        """A-t-on des coordonnées exploitables ?"""
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True, slots=True)
class Salary:
    """Un salaire ramené au **brut mensuel en euros**, et le texte d'origine.

    Les sources mélangent annuel, mensuel, horaire, fourchettes et « selon
    profil » ; un filtre « au moins 2 000 € » n'a de sens que sur une
    unité commune.
    """

    min_monthly: int | None
    max_monthly: int | None
    raw: str | None

    @property
    def is_known(self) -> bool:
        """Au moins une borne est connue."""
        return self.min_monthly is not None or self.max_monthly is not None


@dataclass(frozen=True, slots=True)
class Company:
    """L'employeur, tel que la source le nomme."""

    name: str
    logo_url: str | None = None
    website: str | None = None


@dataclass(frozen=True, slots=True)
class Offer:
    """Une offre normalisée. Immuable : une mise à jour produit une nouvelle valeur."""

    id: UUID
    source: Source
    source_id: str
    title: str
    company: Company | None
    description: str
    location: Location | None
    contract_type: ContractType | None
    remote: RemoteMode | None
    schedule: Schedule | None
    experience: ExperienceLevel | None
    salary: Salary
    published_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    apply_url: str | None
    origin_url: str | None
    is_active: bool = True
    #: Condensat du contenu normalisé : deux offres au même condensat sont
    #: la même annonce (voir `domain/dedup.py`).
    content_hash: str = ""
    #: Empreinte SimHash du contenu, pour les quasi-doublons.
    simhash: int = 0
    #: L'offre canonique dont celle-ci est un doublon, le cas échéant.
    duplicate_of: UUID | None = None
    #: Version du plongement présent dans l'index (`None` : pas indexée).
    indexed_version: str | None = None
    #: Condensat du contenu dont l'index porte le plongement : s'il diffère
    #: de `content_hash`, l'annonce a changé et se re-plonge.
    indexed_hash: str | None = None
    indexed_at: datetime | None = None

    @property
    def is_alternance(self) -> bool:
        """Raccourci métier : l'alternance est un filtre à part entière dans l'app."""
        return self.contract_type is ContractType.ALTERNANCE

    @property
    def is_canonical(self) -> bool:
        """Une offre qui n'est le doublon d'aucune autre."""
        return self.duplicate_of is None

    @property
    def indexable(self) -> bool:
        """Entre dans l'index : active et canonique."""
        return self.is_active and self.is_canonical

    @property
    def is_indexed(self) -> bool:
        """L'index porte-t-il un point pour cette offre ?"""
        return self.indexed_version is not None

    def embedding_current(self, version: str) -> bool:
        """Le plongement indexé vaut-il encore ? Même version, même contenu.

        C'est la garantie qui manquait à la V1 : on ne re-plonge **jamais**
        un contenu déjà plongé par le même modèle.
        """
        return self.indexed_version == version and self.indexed_hash == self.content_hash


def uuid7(*, now_ms: int | None = None) -> UUID:
    """Un UUID v7 (RFC 9562) : 48 bits d'horodatage, le reste aléatoire.

    La bibliothèque standard ne le fournit qu'à partir de Python 3.14 ;
    l'implémentation tient en six lignes et se teste sur la monotonie de
    l'horodatage.
    """
    millis = int(time.time() * 1000) if now_ms is None else now_ms
    rand = int.from_bytes(os.urandom(10), "big")
    value = (millis & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= (rand >> 68) << 64  # 12 bits aléatoires (rand_a)
    value |= 0b10 << 62  # variante RFC
    value |= rand & ((1 << 62) - 1)  # 62 bits aléatoires (rand_b)
    return UUID(int=value)
