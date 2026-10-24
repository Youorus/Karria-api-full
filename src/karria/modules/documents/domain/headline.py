"""Le poste visé sous le nom, toujours — même quand le modèle n'en a pas.

Contrat
-------
Sur un CV professionnel, l'intitulé recherché se lit juste sous le nom : c'est
la deuxième chose qu'un recruteur regarde, et le premier mot-clé qu'un ATS
cherche. Quand le modèle a un emplacement pour lui, on prend le style mesuré
du modèle. Quand il n'en a pas, on le **synthétise dans la grammaire du
modèle** (ADR-0020 § 3.1), à la manière des CV réels :

- **même famille** que le nom (la paire nom / poste forme un bloc) ;
- **graisse moyenne** : 500 sous un nom gras, 400 sous un nom maigre — la
  hiérarchie se lit sans concurrencer le nom ;
- **corps** : ~72 % des titres de rubrique, borné entre 1,15 × le texte courant
  et 55 % du nom ;
- **couleur d'accent** si les titres de rubrique en portent une autre que le
  nom ; sinon la couleur du nom ;
- **capitales espacées** si les titres de rubrique le sont ; l'interlettrage
  est alors plafonné à **0,08 em** : pdfminer (que lisent beaucoup d'ATS)
  insère une espace dès qu'un écart entre glyphes dépasse 0,1 em et lit
  « D A T A » (mesuré le 2026-10-24 ; la preuve de concept, lue par un autre
  extracteur, avait retenu 0,12 em) : un champ que Karria ajoute doit être
  lisible par un ATS par construction.

Un CV sans poste visé n'affiche rien : l'emplacement est omis, sans blanc.
"""

from __future__ import annotations

from typing import Any, Final

#: Plafond d'interlettrage d'un intitulé lisible par les ATS, en em : sous le
#: seuil de pdfminer (0,1 em, `LAParams.word_margin`), avec une marge.
ATS_SAFE_TRACKING_EM: Final = 0.08
MM_PER_PT: Final = 25.4 / 72
#: Rapport boîte / corps d'une ligne quand la police n'est pas encore connue.
DEFAULT_BOX_RATIO: Final = 1.17


def synthesize_headline_style(
    name: dict[str, Any],
    section_title: dict[str, Any],
    *,
    body_size_pt: float | None = None,
    box_ratio: float = DEFAULT_BOX_RATIO,
) -> dict[str, Any]:
    """Le style d'un poste visé que le modèle n'a pas prévu.

    Args:
        name: Style de la (dernière) ligne du nom.
        section_title: Style des titres de rubrique.
        body_size_pt: Corps du texte courant, s'il est connu.
        box_ratio: Hauteur de boîte / corps de la police (métriques verticales).
    """
    size = 0.72 * float(section_title["size_pt"])
    if body_size_pt:
        size = max(size, 1.15 * body_size_pt)
    size = min(size, 0.55 * float(name["size_pt"]))
    size = round(size, 1)
    accent = section_title["color"] if section_title["color"] != name["color"] else name["color"]
    tracked = float(section_title.get("letter_spacing_pt") or 0.0) > 0.0
    uppercase = bool(section_title.get("uppercase"))
    spacing = 0.0
    if tracked:
        title_em = float(section_title["letter_spacing_pt"]) / float(section_title["size_pt"])
        spacing = round(min(max(title_em, 0.06) * 0.9, ATS_SAFE_TRACKING_EM) * size, 2)
    return {
        "family": name["family"],
        "weight": 500 if int(name["weight"]) >= 600 else 400,  # noqa: PLR2004
        "italic": False,
        "size_pt": size,
        "color": accent,
        "letter_spacing_pt": spacing,
        "uppercase": uppercase,
        "box_h_mm": round(size * box_ratio * MM_PER_PT, 3),
        "pitch_mm": None,
        "synthesized": True,
    }


def ats_safe_spacing(style: dict[str, Any]) -> float:
    """L'interlettrage d'un style plafonné à 0,08 em (variante « optimisée ATS »)."""
    return round(
        min(float(style["letter_spacing_pt"]), ATS_SAFE_TRACKING_EM * float(style["size_pt"])), 2
    )


def ats_safe_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """La variante « optimisée ATS » d'une spécification : chaque interlettrage plafonné à 0,08 em.

    Seul l'espacement change ; polices, couleurs et géométrie restent celles du
    modèle (ADR-0020 § 6). La spécification d'origine n'est pas modifiée.
    """
    styles = {
        key: {**style, "letter_spacing_pt": ats_safe_spacing(style)}
        for key, style in spec["styles"].items()
    }
    return {**spec, "styles": styles}
