"""Resserrer un CV pour qu'il tienne sur une page, sans rien perdre.

Contrat
-------
Un CV se lit sur **une** page : c'est la pratique attendue par les
recruteurs comme par les logiciels de tri. Quand le texte déborde malgré
l'ajustement des espacements et du corps (`fit_script`), on ne comprime pas
davantage — un CV illisible ne sert à rien — et on ne coupe pas de page en
deux : on **resserre la vue**, par paliers, en gardant toujours les
informations dans le CV de la personne (`cv.me`). Seul le rendu change, et
l'export dit exactement ce qu'il a replié (`Fit.condensed`) pour que
l'application le montre et propose d'ajuster.

Les paliers, du plus discret au plus franc :

1. trois missions au plus par expérience ; le détail des formations et des
   projets replié ; deux projets au plus ;
2. en plus : deux missions par expérience ; dix compétences, quatre langues,
   quatre centres d'intérêt au plus ; l'à-propos ramené à ses premières
   phrases ;
3. en dernier recours : une mission par expérience ; huit compétences, trois
   langues, trois centres d'intérêt ; l'à-propos à une phrase ou deux.

On ne retire **jamais** une expérience ni une formation : ce sont les faits
du parcours, pas des détails.
"""

from __future__ import annotations

import copy
from typing import Any, Final

Json = dict[str, Any]

#: Les paliers que l'export essaie, dans l'ordre.
LEVELS: Final[tuple[int, ...]] = (1, 2, 3)

MAX_MISSIONS: Final[dict[int, int]] = {1: 3, 2: 2, 3: 1}
MAX_PROJECTS: Final = 2
MAX_SKILLS: Final[dict[int, int]] = {2: 10, 3: 8}
MAX_LANGUAGES: Final[dict[int, int]] = {2: 4, 3: 3}
MAX_INTERESTS: Final[dict[int, int]] = {2: 4, 3: 3}
SUMMARY_CHARS: Final[dict[int, int]] = {2: 320, 3: 200}


def _trim_list(cv: Json, key: str, limit: int, notes: dict[str, int]) -> None:
    items = cv.get(key) or []
    if len(items) > limit:
        notes[key] = len(items) - limit
        cv[key] = items[:limit]


def _shorten_summary(text: str, limit: int) -> str:
    """Les premières phrases qui tiennent dans `limit` caractères, sans couper un mot."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in (". ", "! ", "? "):
        cut = head.rfind(sep)
        if cut > limit // 2:
            return head[: cut + 1]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip(",;:") + "…"


def condense_cv(cv: Json, level: int) -> tuple[Json, dict[str, int]]:
    """Le CV resserré au palier `level`, et ce qui a été replié, par nature.

    Les clés du compte rendu : `missions` (missions masquées, toutes
    expériences confondues), `educations_detail`, `projects`,
    `projects_detail`, `skills`, `languages`, `interests`, `summary` (1 :
    l'à-propos a été raccourci). Vide : rien à resserrer à ce palier — le CV
    est déjà aussi court que le palier le permet.
    """
    out = copy.deepcopy(cv)
    notes: dict[str, int] = {}
    max_missions = MAX_MISSIONS.get(level, MAX_MISSIONS[max(MAX_MISSIONS)])
    for exp in out.get("experiences") or []:
        missions = exp.get("missions") or []
        if len(missions) > max_missions:
            notes["missions"] = notes.get("missions", 0) + len(missions) - max_missions
            exp["missions"] = missions[:max_missions]
    for edu in out.get("educations") or []:
        if edu.get("description"):
            notes["educations_detail"] = notes.get("educations_detail", 0) + 1
            edu["description"] = None
    _trim_list(out, "projects", MAX_PROJECTS, notes)
    for project in out.get("projects") or []:
        if project.get("description"):
            notes["projects_detail"] = notes.get("projects_detail", 0) + 1
            project["description"] = None
    if level >= 2:  # noqa: PLR2004 - à partir du second palier
        tier = min(level, max(MAX_SKILLS))
        _trim_list(out, "skills", MAX_SKILLS[tier], notes)
        _trim_list(out, "languages", MAX_LANGUAGES[tier], notes)
        _trim_list(out, "interests", MAX_INTERESTS[tier], notes)
        summary = out.get("summary")
        if isinstance(summary, str) and len(summary) > SUMMARY_CHARS[tier]:
            out["summary"] = _shorten_summary(summary, SUMMARY_CHARS[tier])
            notes["summary"] = 1
    return out, notes
