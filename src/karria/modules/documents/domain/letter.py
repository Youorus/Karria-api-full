"""La lettre de motivation : une donnée structurée, rendue depuis une spécification de lettre (ADR-0030, ADR-0036).

Contrat
-------
- Une lettre = destinataire (`company`, `logo_url?`, `city?`), objet,
  paragraphes typés (`interet_entreprise`, `interet_poste`, `competences`,
  `pourquoi_moi`, `libre`), formule de politesse, expéditeur (l'identité du
  CV). Jamais de texte libre hors de ces champs.
- `from_letter` produit la **vue** commune aux deux moteurs (`kind: "letter"`),
  avec les chemins `data-chemin` à côté des textes (`view["paths"]`), comme
  pour le CV (`domain/view.py`). La mise en page vient d'une **spécification
  de lettre** — celle appariée au modèle, ou la lettre sobre
  (`domain/plain_letter.py`) — plus de la grammaire du CV (ADR-0036).
- Les bonnes pratiques RH françaises sont des règles ici, pas dans l'app :
  une page, trois à quatre paragraphes, objet explicite (« Candidature au
  poste de … »), lieu et date, politesse sobre, signature. `letter_warnings`
  dit ce qui s'en écarte ; rien n'est bloqué, la personne décide.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

from karria.modules.documents.domain.palette import derived_color, parse_hex_color

Json = dict[str, Any]

PARAGRAPH_KINDS: Final = (
    "interet_entreprise",
    "interet_poste",
    "competences",
    "pourquoi_moi",
    "libre",
)
#: En dessous, la lettre paraît expédiée ; au-dessus, elle ne tient plus sur une page.
MIN_PARAGRAPHS: Final = 2
MAX_PARAGRAPHS: Final = 5
#: Au-delà, une page ne suffit plus (mesuré sur Meridian à 10 pt : ~3 200 signes).
MAX_BODY_CHARS: Final = 3_200
#: La formule de politesse proposée quand la lettre n'en a pas.
DEFAULT_CLOSING: Final = (
    "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées."
)
DEFAULT_GREETING: Final = "Madame, Monsieur,"

MONTHS_FR: Final = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def recolor_letter_spec(spec: Json, primary: str | None) -> Json:
    """La lettre appariée, à la couleur choisie pour le CV (ADR-0034 § 2026-11-06, ADR-0036).

    `spec["accent"]` est la couleur du **décor du CV apparié**, mesurée à
    l'ingestion. Suivent la couleur choisie : les décorations de la lettre qui
    portaient cette couleur ou l'une de ses teintes (`derived_color`), et les
    **textes** marqués `follows_decor` — ceux qui, dans le PDF, avaient la
    couleur du décor (l'objet, souvent). Tout autre texte reste du texte. Sans
    couleur, sans accent, ou avec la couleur d'origine : le même objet. Une
    lettre sobre n'a pas d'accent : elle reste neutre.
    """
    origin = spec.get("accent")
    if primary is None or not isinstance(origin, str) or not origin:
        return spec
    chosen = parse_hex_color(primary)
    origin = origin.upper()
    if chosen == origin:
        return spec

    def shape(d: Json) -> Json:
        out = dict(d)
        for key in ("fill", "stroke"):
            value = d.get(key)
            if isinstance(value, str) and value:
                out[key] = derived_color(value.upper(), origin, chosen) or value
        return out

    header = spec.get("header") or {}
    return {
        **spec,
        "styles": {
            key: {**st, "color": chosen} if st.get("follows_decor") else st
            for key, st in (spec.get("styles") or {}).items()
        },
        "header": {**header, "decorations": [shape(d) for d in header.get("decorations") or []]},
        "page_decorations": [shape(d) for d in spec.get("page_decorations") or []],
    }


def format_date_fr(day: date) -> str:
    """« 5 novembre 2026 », « 1er mars 2027 »."""
    number = "1er" if day.day == 1 else str(day.day)
    return f"{number} {MONTHS_FR[day.month - 1]} {day.year}"


def place_and_date(city: str | None, day: date) -> str:
    """« Montreuil, le 5 novembre 2026 » — sans ville : « Le 5 novembre 2026 »."""
    when = format_date_fr(day)
    return f"{city.strip()}, le {when}" if city and city.strip() else f"Le {when}"


def letter_file_name(company: str | None, extension: str) -> str:
    """« Lettre de motivation - Entreprise.pdf » : le nom proposé au téléchargement."""
    name = (company or "").strip()
    return (
        f"Lettre de motivation - {name}.{extension}"
        if name
        else f"Lettre de motivation.{extension}"
    )


def explicit_subject(subject: str | None, position: str | None = None) -> str:
    """L'objet tel qu'il se lit : « Candidature au poste de … » quand il manque."""
    text = (subject or "").strip()
    if text:
        return text
    return f"Candidature au poste de {position.strip()}" if position and position.strip() else ""


def letter_warnings(letter: Json) -> list[str]:
    """Ce qui s'écarte des usages : des avertissements, jamais un refus."""
    paragraphs = [p for p in letter.get("paragraphs") or [] if (p.get("text") or "").strip()]
    warnings: list[str] = []
    if len(paragraphs) < MIN_PARAGRAPHS:
        warnings.append("too_few_paragraphs")
    if len(paragraphs) > MAX_PARAGRAPHS:
        warnings.append("too_many_paragraphs")
    if sum(len(p["text"]) for p in paragraphs) > MAX_BODY_CHARS:
        warnings.append("body_too_long")
    subject = (letter.get("subject") or "").strip()
    if not subject.casefold().startswith("candidature"):
        warnings.append("subject_not_explicit")
    if not (letter.get("closing") or "").strip():
        warnings.append("closing_missing")
    if not ((letter.get("recipient") or {}).get("company") or "").strip():
        warnings.append("company_missing")
    return warnings


def from_letter(spec: Json, letter: Json, *, today: date) -> Json:
    """La lettre vers la vue commune aux moteurs, avec ses chemins.

    La spécification de lettre n'est lue que par les moteurs (styles,
    positions) ; ici elle ne sert qu'à rester symétrique de `from_cv`. Le nom
    est donné entier **et** en deux champs : un modèle de lettre l'écrit sur
    une ou deux lignes (ADR-0036). `date_line`, s'il est posé dans la lettre,
    remplace lieu et date calculés : c'est le **contenu d'exemple** d'un modèle
    (la date imprimée dans son PDF), jamais une donnée de l'app, dont le schéma
    ne le connaît pas.
    """
    del spec
    sender = letter.get("sender") or {}
    recipient = letter.get("recipient") or {}
    first = (sender.get("first_name") or "").strip()
    last = (sender.get("last_name") or "").strip()
    full_name = f"{first} {last}".strip()
    paragraphs = [
        {"kind": p.get("kind") or "libre", "text": (p.get("text") or "").strip()}
        for p in letter.get("paragraphs") or []
        if (p.get("text") or "").strip()
    ]
    header = {
        "first_name": first,
        "last_name": last,
        "full_name": full_name,
        "headline": (sender.get("headline") or "").strip(),
        "phone": (sender.get("phone") or "").strip(),
        "email": (sender.get("email") or "").strip(),
        "address": (sender.get("city") or "").strip(),
    }
    fixed_date = (letter.get("date_line") or "").strip()
    return {
        "kind": "letter",
        "header": header,
        "recipient": {
            "company": (recipient.get("company") or "").strip(),
            "city": (recipient.get("city") or "").strip(),
        },
        "date_line": fixed_date or place_and_date(sender.get("city"), today),
        "subject": explicit_subject(letter.get("subject")),
        "greeting": (letter.get("greeting") or DEFAULT_GREETING).strip(),
        "paragraphs": paragraphs,
        "closing": (letter.get("closing") or DEFAULT_CLOSING).strip(),
        "signature": full_name,
        "sections": [],
        "paths": {
            "header": {
                "first_name": "sender.first_name",
                "last_name": "sender.last_name",
                "full_name": "sender.full_name",
                "headline": "sender.headline",
                "phone": "sender.phone",
                "email": "sender.email",
                "address": "sender.city",
            },
            "recipient": {"company": "recipient.company", "city": "recipient.city"},
            "subject": "subject",
            "greeting": "greeting",
            "paragraphs": [
                f"paragraphs.{i}.text"
                for i, p in enumerate(letter.get("paragraphs") or [])
                if (p.get("text") or "").strip()
            ],
            "closing": "closing",
            "signature": "sender.full_name",
        },
    }
