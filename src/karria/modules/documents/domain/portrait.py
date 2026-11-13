"""Le portrait d'exemple d'un modèle : une personne fictive, en harmonie avec lui.

Contrat
-------
La photo d'exemple d'un modèle vient du PDF du fournisseur : une photo de
banque d'images, la même d'un modèle à l'autre, et qui n'est pas à Karria.
Chaque modèle reçoit donc **son** portrait, généré : une personne fictive
qui porte le nom de l'exemple, cadrée en buste, sur un fond uni accordé à
la couleur du modèle. La variété vient de la graine (l'âge, la tenue, la
lumière changent d'un modèle à l'autre) ; l'harmonie vient de la couleur
et de l'ambiance du modèle (ses étiquettes). Rien ici n'appelle un
fournisseur : ce module décide **quoi** demander ; l'infrastructure demande.
"""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass
from typing import Any, Final

from karria.modules.documents.domain.palette import primary_color_of

Json = dict[str, Any]

AGES: Final = ("d'environ 25 ans", "d'environ 30 ans", "d'environ 38 ans", "d'environ 45 ans")
OUTFITS: Final = (
    "chemise claire sans cravate",
    "veste sombre sur un haut uni",
    "pull fin à col rond",
    "blazer clair sur un haut sombre",
    "chemise bleu pâle, manches retroussées",
)
LIGHTS: Final = (
    "lumière de studio douce et enveloppante",
    "lumière naturelle de fenêtre, légèrement latérale",
    "lumière diffuse et uniforme",
)
MOODS: Final = ("sourire discret", "expression sereine et ouverte", "regard franc, visage détendu")


@dataclass(frozen=True, slots=True)
class Persona:
    """Qui montrer, et dans quel accord avec le modèle."""

    first_name: str
    last_name: str
    headline: str | None
    primary: str | None
    tags: tuple[str, ...]
    seed: int

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or "la personne de l'exemple"


def _lighten(color: str, lightness: float = 0.88, saturation: float = 0.18) -> str | None:
    """Une teinte très claire et peu saturée de la couleur du modèle, pour le fond."""
    try:
        r, g, b = (int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return None
    h, _l, _s = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb(h, lightness, saturation)
    return f"#{round(r2 * 255):02X}{round(g2 * 255):02X}{round(b2 * 255):02X}"


def seed_of(template_name: str, version: int) -> int:
    """Une graine stable par modèle et version : le même portrait à chaque relance."""
    digest = hashlib.sha256(f"{template_name}:{version}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def persona_of(spec: Json, sample: Json, *, template_name: str, version: int) -> Persona:
    """La personne de l'exemple, telle que la spécification et l'exemple la décrivent."""
    identity = sample.get("identity") or {}
    meta = spec.get("meta") or {}
    primary = primary_color_of(spec)
    return Persona(
        first_name=str(identity.get("first_name") or "").strip(),
        last_name=str(identity.get("last_name") or "").strip(),
        headline=(str(identity.get("headline") or "").strip() or None),
        primary=str(primary) if primary else None,
        tags=tuple(str(t) for t in (meta.get("tags") or ())),
        seed=seed_of(template_name, version),
    )


def portrait_prompt(persona: Persona) -> str:
    """La consigne du générateur d'image : réaliste, sobre, accordée au modèle, sans texte."""
    s = persona.seed
    age = AGES[s % len(AGES)]
    outfit = OUTFITS[(s // 7) % len(OUTFITS)]
    light = LIGHTS[(s // 31) % len(LIGHTS)]
    mood = MOODS[(s // 101) % len(MOODS)]
    fond = _lighten(persona.primary) if persona.primary else None
    harmony = (
        f"Fond uni, couleur {fond}, accordé à la couleur du modèle de CV {persona.primary}."
        if fond
        else "Fond uni gris très clair, neutre."
    )
    role = f", {persona.headline.lower()}" if persona.headline else ""
    ambiance = f" Ambiance : {', '.join(persona.tags[:4])}." if persona.tags else ""
    return (
        f"Photo de portrait professionnelle, réaliste, d'une personne fictive prénommée "
        f"{persona.full_name}{role}, {age}. Cadrage en buste, de face, regard vers l'objectif, "
        f"{mood}. {outfit.capitalize()}. {light.capitalize()}. {harmony}{ambiance} "
        "Photo nette, couleurs naturelles, pas de retouche excessive. "
        "Aucun texte, aucun logo, aucune signature, aucun objet dans les mains. Format portrait 3:4."
    )
