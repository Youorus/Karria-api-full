"""La lettre de motivation : une donnée structurée, rendue dans la grammaire du modèle de CV (ADR-0030).

Contrat
-------
- Une lettre = destinataire (`company`, `logo_url?`, `city?`), objet,
  paragraphes typés (`interet_entreprise`, `interet_poste`, `competences`,
  `pourquoi_moi`, `libre`), formule de politesse, expéditeur (l'identité du
  CV). Jamais de texte libre hors de ces champs.
- `from_letter` produit la **vue** commune aux deux moteurs (`kind: "letter"`),
  avec les chemins `data-chemin` à côté des textes (`view["paths"]`), comme
  pour le CV (`domain/view.py`).
- Les bonnes pratiques RH françaises sont des règles ici, pas dans l'app :
  une page, trois à quatre paragraphes, objet explicite (« Candidature au
  poste de … »), lieu et date, politesse sobre, signature. `letter_warnings`
  dit ce qui s'en écarte ; rien n'est bloqué, la personne décide.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

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

    La spécification du modèle n'est lue que par les moteurs (styles, marges) ;
    ici elle ne sert qu'à rester symétrique de `from_cv`.
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
        "full_name": full_name,
        "headline": (sender.get("headline") or "").strip(),
        "phone": (sender.get("phone") or "").strip(),
        "email": (sender.get("email") or "").strip(),
        "address": (sender.get("city") or "").strip(),
    }
    return {
        "kind": "letter",
        "header": header,
        "recipient": {
            "company": (recipient.get("company") or "").strip(),
            "city": (recipient.get("city") or "").strip(),
        },
        "date_line": place_and_date(sender.get("city"), today),
        "subject": explicit_subject(letter.get("subject")),
        "greeting": (letter.get("greeting") or DEFAULT_GREETING).strip(),
        "paragraphs": paragraphs,
        "closing": (letter.get("closing") or DEFAULT_CLOSING).strip(),
        "signature": full_name,
        "sections": [],
        "paths": {
            "header": {
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
