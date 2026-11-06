"""Le contenu à rendre, indépendant du format : le CV (ou l'exemple du modèle) → une vue par rôle.

Contrat
-------
Une vue = `header` {champ: texte} + `sections` [{kind, title, items: [{rôle: texte | liste}]}].
Les deux moteurs (HTML → PDF et DOCX) ne lisent **que** la vue : la logique
« quel champ du CV va dans quel emplacement du modèle » n'existe qu'ici
(ADR-0020 § 2) — « Poste - Entreprise » si le modèle les réunit, dates au
format du modèle, catégories de compétences, niveau de langue.

Le poste visé (`identity.headline`) est **toujours** dans l'en-tête quand il
existe ; absent, il vaut `""` et aucun moteur ne lui réserve de place.

Les chemins (ADR-0029)
----------------------
Le CV d'une personne laisse, à côté de chaque texte, le **chemin JSON** de
la donnée dont il vient : `view["paths"]["header"]` pour l'en-tête
(`{"headline": "identity.headline", …}`) et `section["paths"]`, une liste
parallèle à `section["items"]` (`{"item_title": "experiences.1.position",
"bullet": ["experiences.1.missions.0", …]}`). Un texte composé — nom complet,
« Poste - Entreprise », dates — porte un chemin **virtuel** (`identity.full_name`,
`experiences.1`, `experiences.1.dates`). Les chemins vivent **à côté** des
éléments, jamais dedans : les moteurs et le score ATS parcourent les éléments
sans y trouver autre chose que du texte. L'exemple d'un modèle n'a pas de
chemins : rien n'y est modifiable.
"""

from __future__ import annotations

import re
from typing import Any, Final

Json = dict[str, Any]

TITLES_FR: Final[dict[str, str]] = {
    "contact": "Contact",
    "summary": "Profil",
    "experiences": "Expériences professionnelles",
    "educations": "Formation",
    "skills": "Compétences",
    "languages": "Langues",
    "interests": "Centres d'intérêt",
    "projects": "Projets",
    "references": "Références",
}
LANGUAGE_LEVELS_FR: Final[dict[str, str]] = {"Natif": "langue maternelle"}
_SCHEME: Final = re.compile(r"^https?://(www\.)?")


def year(value: str | None) -> str:
    """L'année d'une date `AAAA-MM` ou `AAAA`."""
    return (value or "")[:4]


def period(start: str | None, end: str | None, *, current: bool, sep: str = " - ") -> str:
    """« 2021 - 2023 », « 2023 - Aujourd’hui », « 2022 » (même année ou une seule borne)."""
    a = year(start)
    b = "Aujourd’hui" if current else year(end)
    if a and b and a != b:
        return f"{a}{sep}{b}"
    return a or b


def separator(samples: list[str], default: str = " - ") -> str:
    """Le séparateur qu'emploie le modèle entre deux champs d'un intitulé (« Poste - Entreprise »)."""
    for sample in samples:
        match = re.search(r"\s[-–—|]\s", sample or "")
        if match:
            return match.group(0)
    return default


def grouped(role: str, entries: list[tuple[str, str | None]]) -> list[Json]:
    """Une liste (compétences…) → un élément par catégorie, dans l'ordre d'apparition."""
    items: list[Json] = []
    for name, category in entries:
        if not items or items[-1]["subsection_title"] != category:
            items.append({"subsection_title": category, role: []})
        items[-1][role].append(name)
    return items


def grouped_paths(role: str, entries: list[tuple[str, str | None]], paths: list[str]) -> list[Json]:
    """Les chemins des éléments de `grouped`, dans le même découpage par catégorie."""
    out: list[Json] = []
    last_category: str | None = None
    for (_, category), path in zip(entries, paths, strict=True):
        if not out or last_category != category:
            out.append({role: []})
            last_category = category
        out[-1][role].append(path)
    return out


def short_url(url: str) -> str:
    """`https://www.linkedin.com/in/x/` → `linkedin.com/in/x`."""
    return _SCHEME.sub("", url).rstrip("/")


def spec_sections(spec: Json) -> dict[str, Json]:
    """Les rubriques du modèle, par genre."""
    return {s["kind"]: s for b in spec["bands"] for c in b["columns"] for s in c["sections"]}


def from_cv(spec: Json, cv: Json) -> Json:
    """Le CV (forme de `GET /cv/me`) vers la vue, selon les emplacements du modèle."""
    ident = cv.get("identity") or {}
    contact = cv.get("contact") or {}
    links = contact.get("links") or []
    website = short_url(links[0]["url"]) if links and links[0].get("url") else ""
    first, last = (ident.get("first_name") or "").strip(), (ident.get("last_name") or "").strip()
    header = {
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}".strip(),
        "headline": (ident.get("headline") or "").strip(),
        "phone": contact.get("phone") or "",
        "email": contact.get("email") or "",
        "address": contact.get("city") or "",
        "website": website,
    }
    header_paths = {
        "first_name": "identity.first_name",
        "last_name": "identity.last_name",
        "full_name": "identity.full_name",
        "headline": "identity.headline",
        "phone": "contact.phone",
        "email": "contact.email",
        "address": "contact.city",
        "website": "contact.links.0.url",
    }
    kinds = spec_sections(spec)
    out: list[Json] = []
    contact_item = {
        role: value
        for role, value in (
            ("phone", contact.get("phone")),
            ("email", contact.get("email")),
            ("address", contact.get("city")),
            ("website", website),
        )
        if value
    }
    if contact_item:
        out.append(
            {
                "kind": "contact",
                "items": [contact_item],
                "paths": [{role: header_paths[role] for role in contact_item}],
            }
        )
    if cv.get("summary"):
        out.append(
            {
                "kind": "summary",
                "items": [{"summary": cv["summary"]}],
                "paths": [{"summary": "summary"}],
            }
        )
    exp_spec = kinds.get("experiences", {})
    # L'entreprise a son emplacement dans le contenu ou dans la colonne latérale d'une frise.
    exp_slots = list(exp_spec.get("slots", [])) + list(
        (exp_spec.get("side") or {}).get("slots", [])
    )
    has_subtitle = any("item_subtitle" in slot["roles"] for slot in exp_slots)
    sep = separator([exp_spec.get("title_sep", "")])
    items = []
    paths = []
    for i, e in enumerate(cv.get("experiences") or []):
        missions = list(e.get("missions") or [])
        items.append(
            {
                "item_title": e["position"]
                if has_subtitle
                else f"{e['position']}{sep}{e['company']}",
                "item_subtitle": e["company"] if has_subtitle else None,
                "item_dates": period(
                    e.get("start_date"), e.get("end_date"), current=bool(e.get("is_current"))
                ),
                "item_location": e.get("location"),
                "bullet": missions,
                "item_text": e.get("description") if not missions else None,
            }
        )
        paths.append(
            {
                "item_title": f"experiences.{i}.position" if has_subtitle else f"experiences.{i}",
                "item_subtitle": f"experiences.{i}.company",
                "item_dates": f"experiences.{i}.dates",
                "item_location": f"experiences.{i}.location",
                "bullet": [f"experiences.{i}.missions.{j}" for j in range(len(missions))],
                "item_text": f"experiences.{i}.description",
            }
        )
    if items:
        out.append({"kind": "experiences", "items": items, "paths": paths})
    items = []
    paths = []
    for i, e in enumerate(cv.get("educations") or []):
        items.append(
            {
                "item_title": e["degree"],
                "item_subtitle": e["school"],
                "item_dates": period(
                    e.get("start_date"), e.get("end_date"), current=bool(e.get("is_in_progress"))
                ),
                "item_location": e.get("location"),
                "bullet": [],
                "item_text": e.get("description"),
            }
        )
        paths.append(
            {
                "item_title": f"educations.{i}.degree",
                "item_subtitle": f"educations.{i}.school",
                "item_dates": f"educations.{i}.dates",
                "item_location": f"educations.{i}.location",
                "bullet": [],
                "item_text": f"educations.{i}.description",
            }
        )
    if items:
        out.append({"kind": "educations", "items": items, "paths": paths})
    skills = cv.get("skills") or []
    if skills:
        entries = [(s["name"], s.get("category")) for s in skills]
        out.append(
            {
                "kind": "skills",
                "items": grouped("skill", entries),
                "paths": grouped_paths(
                    "skill", entries, [f"skills.{i}" for i in range(len(skills))]
                ),
            }
        )
    languages = cv.get("languages") or []
    if languages:
        names = [
            f"{lg['language']} ({LANGUAGE_LEVELS_FR.get(lg.get('level') or '', lg.get('level'))})"
            if lg.get("level")
            else lg["language"]
            for lg in languages
        ]
        entries = [(n, None) for n in names]
        out.append(
            {
                "kind": "languages",
                "items": grouped("language", entries),
                "paths": grouped_paths(
                    "language", entries, [f"languages.{i}" for i in range(len(languages))]
                ),
            }
        )
    interests = cv.get("interests") or []
    if interests:
        entries = [(i["name"], None) for i in interests]
        out.append(
            {
                "kind": "interests",
                "items": grouped("interest", entries),
                "paths": grouped_paths(
                    "interest", entries, [f"interests.{i}" for i in range(len(interests))]
                ),
            }
        )
    projects = cv.get("projects") or []
    if projects:
        out.append(
            {
                "kind": "projects",
                "items": [
                    {
                        "item_title": p["title"],
                        "item_subtitle": ", ".join(p.get("technologies") or []) or None,
                        "item_dates": None,
                        "bullet": [],
                        "item_text": p.get("description"),
                    }
                    for p in projects
                ],
                "paths": [
                    {
                        "item_title": f"projects.{i}.title",
                        "item_subtitle": f"projects.{i}.technologies",
                        "bullet": [],
                        "item_text": f"projects.{i}.description",
                    }
                    for i in range(len(projects))
                ],
            }
        )
    for section in out:
        section["title"] = TITLES_FR[section["kind"]]
    return {"header": header, "sections": out, "paths": {"header": header_paths}}


def item_path(section: Json, index: int, role: str, position: int = 0) -> str | None:
    """Le chemin d'un texte d'une rubrique (`None` pour l'exemple d'un modèle)."""
    paths = section.get("paths")
    if not paths or index >= len(paths):
        return None
    found = paths[index].get(role)
    if isinstance(found, list):
        return str(found[position]) if position < len(found) else None
    return str(found) if found else None


def header_path(view: Json, field: str) -> str | None:
    """Le chemin d'un champ de l'en-tête (`None` pour l'exemple d'un modèle)."""
    found = (view.get("paths") or {}).get("header", {}).get(field)
    return str(found) if found else None


def from_sample(spec: Json, sample: Json) -> Json:
    """Le contenu d'exemple du modèle, avec ses titres d'origine : pour mesurer la fidélité."""
    ident = sample.get("identity") or {}
    contact = sample.get("contact") or {}
    first, last = ident.get("first_name", ""), ident.get("last_name", "")
    header = {
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}".strip(),
        "headline": ident.get("headline", ""),
        "phone": contact.get("phone", ""),
        "email": contact.get("email", ""),
        "address": contact.get("city", ""),
        "website": contact.get("website", ""),
    }
    titles = {kind: s["title_sample"] for kind, s in spec_sections(spec).items()}
    out: list[Json] = []
    item = {
        role: contact[field]
        for role, field in (
            ("phone", "phone"),
            ("email", "email"),
            ("address", "city"),
            ("website", "website"),
        )
        if contact.get(field)
    }
    if "contact" in titles:
        out.append({"kind": "contact", "items": [item]})
    if sample.get("summary"):
        out.append({"kind": "summary", "items": [{"summary": sample["summary"]}]})
    for kind in ("experiences", "educations", "projects", "references"):
        entries = [
            {
                "item_title": e["title_raw"],
                "item_subtitle": e["subtitle_raw"] or None,
                "item_dates": e["dates_raw"] or None,
                "item_location": e["location"],
                "bullet": e["bullets"],
                "item_text": e["description"],
            }
            for e in sample.get(kind) or []
        ]
        if entries:
            out.append({"kind": kind, "items": entries})
    for kind, role in (("skills", "skill"), ("languages", "language"), ("interests", "interest")):
        entries = grouped(role, [(s["name"], s.get("category")) for s in sample.get(kind) or []])
        if entries:
            out.append({"kind": kind, "items": entries})
    # Les rubriques libres du modèle (« Réalisations », « Certifications »…),
    # avec leur titre et leurs éléments tels quels (ADR-0037).
    for kind, other in (sample.get("others") or {}).items():
        if kind not in titles:
            continue
        entries = [
            {
                "item_title": e["title_raw"] or None,
                "item_subtitle": e["subtitle_raw"] or None,
                "item_dates": e["dates_raw"] or None,
                "item_location": e["location"],
                "bullet": e["bullets"],
                "item_text": e["description"],
            }
            for e in other.get("items") or []
        ]
        if entries:
            out.append({"kind": kind, "items": entries})
    for section in out:
        section["title"] = titles.get(section["kind"], TITLES_FR.get(section["kind"], ""))
    return {"header": header, "sections": out, "sample": True}
