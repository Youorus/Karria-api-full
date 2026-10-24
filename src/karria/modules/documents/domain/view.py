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
    website = short_url(links[0]["url"]) if links else ""
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
        out.append({"kind": "contact", "items": [contact_item]})
    if cv.get("summary"):
        out.append({"kind": "summary", "items": [{"summary": cv["summary"]}]})
    exp_spec = kinds.get("experiences", {})
    has_subtitle = any("item_subtitle" in slot["roles"] for slot in exp_spec.get("slots", []))
    sep = separator([exp_spec.get("title_sep", "")])
    items = [
        {
            "item_title": e["position"] if has_subtitle else f"{e['position']}{sep}{e['company']}",
            "item_subtitle": e["company"] if has_subtitle else None,
            "item_dates": period(
                e.get("start_date"), e.get("end_date"), current=bool(e.get("is_current"))
            ),
            "item_location": e.get("location"),
            "bullet": list(e.get("missions") or []),
            "item_text": e.get("description") if not e.get("missions") else None,
        }
        for e in cv.get("experiences") or []
    ]
    if items:
        out.append({"kind": "experiences", "items": items})
    items = [
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
        for e in cv.get("educations") or []
    ]
    if items:
        out.append({"kind": "educations", "items": items})
    skills = cv.get("skills") or []
    if skills:
        out.append(
            {
                "kind": "skills",
                "items": grouped("skill", [(s["name"], s.get("category")) for s in skills]),
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
        out.append({"kind": "languages", "items": grouped("language", [(n, None) for n in names])})
    interests = cv.get("interests") or []
    if interests:
        out.append(
            {
                "kind": "interests",
                "items": grouped("interest", [(i["name"], None) for i in interests]),
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
            }
        )
    for section in out:
        section["title"] = TITLES_FR[section["kind"]]
    return {"header": header, "sections": out}


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
    for section in out:
        section["title"] = titles.get(section["kind"], TITLES_FR[section["kind"]])
    return {"header": header, "sections": out, "sample": True}
