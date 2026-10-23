"""Les élargissements : ce que Karria propose quand la recherche rapporte peu.

Karria connaît toute la base : quand une demande rapporte peu, ou qu'un
voisinage rapporterait beaucoup plus, elle le dit **avec des chiffres
comptés** — jamais par le modèle de langage — et demande la permission.
Ce module est pur : il énumère les relâchements candidats, les classe une
fois comptés, et reconnaît l'acceptation d'une proposition. Le comptage
(une recherche par candidat, dans notre index) est l'affaire de l'application.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from karria.modules.offers.domain.criteria import SearchCriteria
from karria.modules.offers.domain.departments import department_label, neighbours_of
from karria.modules.offers.domain.normalization import fold
from karria.modules.offers.domain.offers import ContractType, Offer, RemoteMode, Schedule
from karria.modules.offers.domain.titles import title_tokens

#: En dessous de ce nombre de résultats, tout gain notable mérite d'être dit.
WIDEN_WHEN_BELOW: Final = 30
#: Un élargissement qui rapporte moins n'est pas proposé.
WIDEN_MIN_GAIN: Final = 10
#: Propositions au plus par tour.
WIDENINGS_MAX: Final = 3
#: Les rayons qu'on propose, dans l'ordre.
RADIUS_STEPS: Final = (30, 50, 100)
#: Rayons proposés au plus par tour.
RADIUS_STEPS_MAX: Final = 2
#: Départements voisins nommés dans la phrase, au plus.
NAMED_NEIGHBOURS: Final = 3
#: Contrats qu'on propose d'ajouter, dans l'ordre.
CONTRACT_STEPS: Final = (ContractType.CDI, ContractType.CDD, ContractType.INTERIM)
_CONTRACT_PHRASE: Final[dict[ContractType, str]] = {
    ContractType.CDI: "le CDI",
    ContractType.CDD: "le CDD",
    ContractType.INTERIM: "l'intérim",
}
#: Un intitulé voisin n'est proposé que s'il porte au moins autant d'offres écartées.
TITLE_MIN_COUNT: Final = 5
TITLE_CANDIDATES_MAX: Final = 2
_GENDER: Final = re.compile(r"\(h/f/x\)|\bh/f/x\b|\(?\b[hf]\s*/\s*[hf]\b\)?")
_PARENS: Final = re.compile(r"\([^)]*\)")
_PUNCT: Final = re.compile(r"[^\w\s'-]")


class WideningKind(StrEnum):
    """Ce qu'on relâche."""

    RADIUS = "radius"
    DEPARTMENTS = "departments"
    WHOLE_FRANCE = "whole_france"
    CONTRACT_TYPES = "contract_types"
    SCHEDULE = "schedule"
    EXPERIENCE = "experience"
    REMOTE = "remote"
    JOB_TITLES = "job_titles"


@dataclass(frozen=True, slots=True)
class Relaxation:
    """Un candidat : des critères relâchés, avant comptage."""

    id: str
    kind: WideningKind
    criteria: SearchCriteria
    #: Pour la phrase : « élargir à 50 km autour de Lyon ».
    label: str
    #: Pour la réponse rapide : « Élargir à 50 km ».
    short: str
    #: Un gain déjà connu (intitulés voisins comptés dans la recherche courante).
    known_gain: int | None = None


@dataclass(frozen=True, slots=True)
class Widening:
    """Une proposition chiffrée, telle que l'app la reçoit et que la conversation la garde."""

    id: str
    kind: WideningKind
    label: str
    gain: int
    message: str
    criteria: SearchCriteria

    def to_dict(self) -> dict[str, Any]:
        """Pour la ranger avec la conversation."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "gain": self.gain,
            "message": self.message,
            "criteria": self.criteria.model_dump(mode="json"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Widening:
        """L'inverse, validé."""
        return cls(
            id=str(data["id"]),
            kind=WideningKind(str(data["kind"])),
            label=str(data["label"]),
            gain=int(data["gain"]),
            message=str(data["message"]),
            criteria=SearchCriteria.model_validate(data["criteria"]),
        )


def relaxations(criteria: SearchCriteria) -> list[Relaxation]:
    """Les relâchements qui ont un sens pour ces critères, à compter."""
    found: list[Relaxation] = []
    location = criteria.location
    anywhere = criteria.whole_france or criteria.wants_remote_only
    if location is not None and location.is_geocoded and not anywhere:
        steps = [s for s in RADIUS_STEPS if s > location.radius_km][:RADIUS_STEPS_MAX]
        found.extend(
            Relaxation(
                id=f"radius-{step}",
                kind=WideningKind.RADIUS,
                criteria=criteria.model_copy(
                    update={"location": location.model_copy(update={"radius_km": step})}
                ),
                label=f"élargir à {step} km autour de {location.label}",
                short=f"Élargir à {step} km",
            )
            for step in steps
        )
    if (
        location is not None
        and not location.is_geocoded
        and location.department_codes
        and not anywhere
    ):
        extra = neighbours_of(location.department_codes)
        if extra:
            names = ", ".join(department_label(c) for c in extra[:NAMED_NEIGHBOURS])
            more = "…" if len(extra) > NAMED_NEIGHBOURS else ""
            found.append(
                Relaxation(
                    id="departments",
                    kind=WideningKind.DEPARTMENTS,
                    criteria=criteria.model_copy(
                        update={
                            "location": location.model_copy(
                                update={"department_codes": [*location.department_codes, *extra]}
                            )
                        }
                    ),
                    label=f"ajouter les départements voisins ({names}{more})",
                    short="Départements voisins",
                )
            )
    if location is not None and not anywhere:
        found.append(
            Relaxation(
                id="whole-france",
                kind=WideningKind.WHOLE_FRANCE,
                criteria=criteria.model_copy(update={"whole_france": True}),
                label="chercher partout en France",
                short="Partout en France",
            )
        )
    if criteria.contract_types and not criteria.alternance_only:
        for kind in CONTRACT_STEPS:
            if kind in criteria.contract_types:
                continue
            found.append(
                Relaxation(
                    id=f"contract-{kind.value}",
                    kind=WideningKind.CONTRACT_TYPES,
                    criteria=criteria.model_copy(
                        update={"contract_types": [*criteria.contract_types, kind]}
                    ),
                    label=f"accepter aussi {_CONTRACT_PHRASE[kind]}",
                    short=f"Ajouter {_CONTRACT_PHRASE[kind]}",
                )
            )
    if criteria.schedule is Schedule.PART_TIME:
        found.append(
            Relaxation(
                id="schedule-any",
                kind=WideningKind.SCHEDULE,
                criteria=criteria.model_copy(update={"schedule": None}),
                label="accepter aussi les offres qui ne précisent pas le temps de travail",
                short="Temps non précisé aussi",
            )
        )
    elif criteria.schedule is Schedule.FULL_TIME:
        found.append(
            Relaxation(
                id="schedule-any",
                kind=WideningKind.SCHEDULE,
                criteria=criteria.model_copy(update={"schedule": None}),
                label="accepter aussi le temps partiel",
                short="Aussi à temps partiel",
            )
        )
    if criteria.max_experience is not None:
        found.append(
            Relaxation(
                id="experience-any",
                kind=WideningKind.EXPERIENCE,
                criteria=criteria.model_copy(update={"max_experience": None}),
                label="ouvrir à tous les niveaux d'expérience",
                short="Tous niveaux d'expérience",
            )
        )
    if (
        criteria.remote
        and not criteria.wants_remote_only
        and RemoteMode.ON_SITE not in criteria.remote
    ):
        found.append(
            Relaxation(
                id="remote-any",
                kind=WideningKind.REMOTE,
                criteria=criteria.model_copy(update={"remote": []}),
                label="accepter aussi les postes sur site",
                short="Aussi sur site",
            )
        )
    return found


def title_key(title: str) -> str:
    """« Commis de cuisine (H/F) » → « commis de cuisine » : la clé d'un intitulé réel."""
    text = _PARENS.sub(" ", fold(title))
    text = _GENDER.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


def title_relaxations(criteria: SearchCriteria, rejected: Sequence[Offer]) -> list[Relaxation]:
    """Les intitulés voisins les plus fréquents parmi les offres que la porte d'intitulé a écartées.

    Leur gain est déjà connu : ce sont des offres de la recherche courante,
    retenues par le filtre dur, refusées sur le titre seul.
    """
    wanted = {title_tokens(t) for t in criteria.job_titles} | {
        title_tokens(t) for t in criteria.excluded_titles
    }
    counts: Counter[str] = Counter()
    for offer in rejected:
        key = title_key(offer.title)
        if key and title_tokens(key) not in wanted:
            counts[key] += 1
    found: list[Relaxation] = []
    for key, count in counts.most_common():
        if count < TITLE_MIN_COUNT or len(found) >= TITLE_CANDIDATES_MAX:
            break
        found.append(
            Relaxation(
                id=f"title-{key.replace(' ', '-')}",
                kind=WideningKind.JOB_TITLES,
                criteria=criteria.model_copy(
                    update={
                        "job_titles": [*criteria.job_titles, key],
                        "excluded_titles": [
                            t
                            for t in criteria.excluded_titles
                            if title_tokens(t) != title_tokens(key)
                        ],
                    }
                ),
                label=f"inclure « {key} », très proche de ce que tu cherches",
                short=f"Ajouter « {key} »",
                known_gain=count,
            )
        )
    return found


_FAMILY: Final[dict[WideningKind, str]] = {
    WideningKind.RADIUS: "geo",
    WideningKind.DEPARTMENTS: "geo",
    WideningKind.WHOLE_FRANCE: "geo",
}


def choose(current: int, counted: Sequence[tuple[Relaxation, int]]) -> list[Widening]:
    """Classe par gain ; une proposition par famille (le lieu n'en est qu'une) ; au plus `WIDENINGS_MAX`.

    `counted` donne, pour chaque candidat, le nombre d'offres que ses critères
    rapporteraient (le gain d'un intitulé voisin est déjà connu). On ne propose
    que si la recherche est maigre, ou si l'élargissement double au moins
    le fil.
    """
    chosen: dict[str, Widening] = {}
    for relaxation, total in counted:
        gain = relaxation.known_gain if relaxation.known_gain is not None else total - current
        if gain < WIDEN_MIN_GAIN or (current >= WIDEN_WHEN_BELOW and gain < current):
            continue
        family = _FAMILY.get(relaxation.kind, relaxation.kind.value)
        # Dans une même famille, le premier venu est le moins disruptif (rayon
        # 50 avant 100, avant toute la France) : il ne cède qu'à un gain plus grand.
        if family in chosen and chosen[family].gain >= gain:
            continue
        chosen[family] = Widening(
            id=relaxation.id,
            kind=relaxation.kind,
            label=relaxation.label,
            gain=gain,
            message=f"{relaxation.short} : +{gain}",
            criteria=relaxation.criteria,
        )
    ranked = sorted(chosen.values(), key=lambda w: w.gain, reverse=True)
    return ranked[:WIDENINGS_MAX]


def phrase_widenings(current: int, widenings: Sequence[Widening]) -> str:
    """La phrase qui propose, chiffres à l'appui, et demande la permission."""
    if not widenings:
        return ""
    parts = "; ".join(f"{w.label} (+{w.gain})" for w in widenings)
    if current == 0:
        return f"Pour en trouver, je peux {parts}. Je le fais ?"
    return f"Si tu veux, je peux élargir : {parts}. Je le fais ?"


def accepted_widening(content: str, pending: Sequence[Widening]) -> Widening | None:
    """La proposition que ce message accepte telle quelle (réponse rapide), ou `None`."""
    text = fold(content)
    for widening in pending:
        if text in (fold(widening.message), fold(widening.label)):
            return widening
    return None


def render_pending(pending: Sequence[Widening]) -> str | None:
    """Les propositions en attente, en données pour le modèle (il ne les chiffre jamais lui-même)."""
    if not pending:
        return None
    lines = "\n".join(f"- {w.id} : {w.label} (+{w.gain} offres)" for w in pending)
    return f"Élargissements proposés au tour précédent (chiffres comptés, à ne pas modifier) :\n{lines}"
