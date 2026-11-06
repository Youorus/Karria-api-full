"""La lettre sobre : une spécification de lettre intégrée, et le choix entre elle et la lettre du modèle (ADR-0036).

Contrat
-------
- **Une lettre se rend toujours depuis une spécification de lettre**, de même
  forme que la spécification d'un modèle de CV : `page`, `fonts`, `styles` par
  rôle, un en-tête à blocs positionnés (`header.blocks`, ancrés à gauche ou à
  droite, lignes empilées au pas mesuré), une colonne de texte (`body`) dont
  les blocs se suivent avec leurs blancs mesurés, des décorations. Deux
  sources : la **lettre appariée** d'un modèle, mesurée dans son PDF
  (`infrastructure/letter_spec_builder.py`), ou **cette lettre sobre**.
- La lettre sobre est la lettre d'affaires française sur page blanche :
  expéditeur en haut à gauche (nom, intitulé, coordonnées), destinataire à
  droite (entreprise, ville), lieu et date à droite, « Objet : … » en gras,
  civilité, paragraphes justifiés, formule de politesse, signature par le nom.
  A4, marges classiques (25 mm), **Carlito 11 pt** : la jumelle métrique de
  Calibri — la police par défaut de Word depuis 2007, donc celle de la plupart
  des lettres de candidature lues par les recruteurs ; ses formes humanistes
  et sa grande hauteur d'x se lisent mieux en paragraphes justifiés que la
  linéale d'Arial (Arimo), plus froide et plus large ; et un DOCX ouvert dans
  Word avec Calibri garde ses retours à la ligne. Interlignes mesurés sur la
  police servie (boîte 1,22 × le corps, pas de 5,4 mm pour 11 pt).
- Elle ne porte **aucune couleur de modèle** : `recolor_spec` n'y trouve pas
  de couleur principale et la rend telle quelle. Le neutre reste neutre.
- `letter_style_served` résout le choix de la personne : `template` sans
  lettre appariée → `plain` ; par défaut `template` si le modèle en a une,
  sinon `plain`. La réponse dit toujours le style réellement servi.
"""

from __future__ import annotations

from typing import Any, Final, Literal

Json = dict[str, Any]

LetterStyle = Literal["template", "plain"]
LETTER_STYLES: Final[tuple[str, ...]] = ("template", "plain")
LETTER_STYLE_TEMPLATE: Final = "template"
LETTER_STYLE_PLAIN: Final = "plain"

#: Version du dessin de la lettre sobre : à incrémenter quand sa géométrie ou
#: ses styles changent, pour que les aperçus de la galerie se régénèrent
#: (`python -m scripts.templates plain-letter`).
PLAIN_LETTER_VERSION: Final = 1
#: La famille servie, et sa jumelle système (pour le DOCX ouvert ailleurs).
PLAIN_FAMILY: Final = "Carlito"
#: Boîte de texte (ascendante − descendante) de Carlito, en fraction du corps,
#: lue dans sa table `hhea` (0,9521 − (−0,2686)).
PLAIN_BOX_RATIO: Final = 1.2207
MM_PER_PT: Final = 25.4 / 72

PAGE_W: Final = 210.0
PAGE_H: Final = 297.0
MARGIN: Final = 25.0
TEXT_W: Final = PAGE_W - 2 * MARGIN
INK: Final = "#111111"
INK_SOFT: Final = "#444444"
BODY_PT: Final = 11.0
#: Pas de ligne du texte courant (mm) : 11 pt × 1,39 — aéré sans être creux.
BODY_PITCH_MM: Final = 5.4
#: « Objet : » avec l'espace insécable que la typographie française met devant
#: les deux-points ; une lettre appariée garde le préfixe que son modèle écrit.
SUBJECT_PREFIX: Final = "Objet\u00a0: "


def _style(
    *,
    size_pt: float,
    weight: int = 400,
    color: str = INK,
    pitch_mm: float | None = None,
    italic: bool = False,
) -> Json:
    """Un style de la spécification, aux mesures de Carlito."""
    return {
        "family": PLAIN_FAMILY,
        "weight": weight,
        "italic": italic,
        "size_pt": size_pt,
        "color": color,
        "letter_spacing_pt": 0.0,
        "uppercase": False,
        "box_h_mm": round(size_pt * PLAIN_BOX_RATIO * MM_PER_PT, 3),
        "pitch_mm": pitch_mm,
    }


def plain_letter_spec() -> Json:
    """La spécification de la lettre sobre (version `PLAIN_LETTER_VERSION`).

    Les positions sont en millimètres, origine en haut à gauche, comme dans
    une spécification mesurée. L'en-tête : trois blocs — l'expéditeur à gauche
    (nom 15 pt gras, intitulé, puis les coordonnées), le destinataire à droite,
    la date à droite. Le corps : l'objet en gras, la civilité, les paragraphes
    justifiés séparés d'une ligne, la formule, la signature.
    """
    styles: dict[str, Json] = {
        "header.full_name": _style(size_pt=15.0, weight=700, pitch_mm=7.6),
        "header.headline": _style(size_pt=BODY_PT, color=INK_SOFT, pitch_mm=8.4),
        "header.address": _style(size_pt=10.5, pitch_mm=5.0),
        "header.phone": _style(size_pt=10.5, pitch_mm=5.0),
        "header.email": _style(size_pt=10.5, pitch_mm=5.0),
        "recipient.company": _style(size_pt=BODY_PT, weight=700, pitch_mm=BODY_PITCH_MM),
        "recipient.city": _style(size_pt=BODY_PT, pitch_mm=BODY_PITCH_MM),
        "date_line": _style(size_pt=BODY_PT, pitch_mm=BODY_PITCH_MM),
        "subject": _style(size_pt=BODY_PT, weight=700, pitch_mm=BODY_PITCH_MM),
        "greeting": _style(size_pt=BODY_PT, pitch_mm=BODY_PITCH_MM),
        "paragraph": _style(size_pt=BODY_PT, pitch_mm=BODY_PITCH_MM),
        "closing": _style(size_pt=BODY_PT, pitch_mm=BODY_PITCH_MM),
        "signature": _style(size_pt=BODY_PT, pitch_mm=BODY_PITCH_MM),
    }
    right_edge = PAGE_W - MARGIN

    def element(field: str, style: str) -> Json:
        return {"field": field, "style": style, "pitch_mm": styles[style]["pitch_mm"], "w_mm": None}

    return {
        "kind": "letter",
        "version": PLAIN_LETTER_VERSION,
        "source": {"producer": "karria"},
        "page": {"width_mm": PAGE_W, "height_mm": PAGE_H, "background": "#FFFFFF"},
        "fonts": [
            {"family": PLAIN_FAMILY, "weight": 400, "italic": False},
            {"family": PLAIN_FAMILY, "weight": 700, "italic": False},
        ],
        "styles": styles,
        "header": {
            "blocks": [
                {
                    "anchor": "left",
                    "x_mm": MARGIN,
                    "y_mm": 22.0,
                    "max_w_mm": 100.0,
                    "elements": [
                        element("full_name", "header.full_name"),
                        element("headline", "header.headline"),
                        element("address", "header.address"),
                        element("phone", "header.phone"),
                        element("email", "header.email"),
                    ],
                },
                {
                    "anchor": "right",
                    "right_mm": PAGE_W - right_edge,
                    "y_mm": 62.0,
                    "max_w_mm": 80.0,
                    "elements": [
                        element("company", "recipient.company"),
                        element("city", "recipient.city"),
                    ],
                },
                {
                    "anchor": "right",
                    "right_mm": PAGE_W - right_edge,
                    "y_mm": 80.0,
                    "max_w_mm": 100.0,
                    "elements": [element("date_line", "date_line")],
                },
            ],
            "decorations": [],
        },
        "body": {
            "x_mm": MARGIN,
            "w_mm": TEXT_W,
            "top_mm": 96.0,
            "blocks": [
                {
                    "role": "subject",
                    "style": "subject",
                    "align": "left",
                    "gap_before_mm": 0.0,
                    "prefix": SUBJECT_PREFIX,
                },
                {"role": "greeting", "style": "greeting", "align": "left", "gap_before_mm": 9.0},
                {
                    "role": "paragraph",
                    "style": "paragraph",
                    "align": "justify",
                    "gap_before_mm": 6.0,
                    "gap_between_mm": BODY_PITCH_MM,
                },
                {"role": "closing", "style": "closing", "align": "justify", "gap_before_mm": 6.0},
                {"role": "signature", "style": "signature", "align": "left", "gap_before_mm": 12.0},
            ],
        },
        "page_decorations": [],
        "content_bottom_mm": 272.0,
    }


def letter_style_served(requested: str | None, *, has_template_letter: bool) -> LetterStyle:
    """Le style réellement servi pour un choix demandé.

    `None` : `template` si le modèle a une lettre appariée, sinon `plain`.
    `template` sans lettre appariée retombe sur `plain` — la réponse le dit.

    Raises:
        ValueError: Un style inconnu (le schéma HTTP l'a normalement refusé).
    """
    if requested is not None and requested not in LETTER_STYLES:
        msg = f"style de lettre inconnu : {requested}"
        raise ValueError(msg)
    if not has_template_letter:
        return LETTER_STYLE_PLAIN
    return LETTER_STYLE_PLAIN if requested == LETTER_STYLE_PLAIN else LETTER_STYLE_TEMPLATE


#: Le contenu d'exemple de la lettre sobre, rendu une fois pour la galerie
#: (`scripts.templates plain-letter`). Fictif, en français, cinq paragraphes
#: mesurés pour tenir sur la page avec de l'air.
PLAIN_LETTER_SAMPLE: Final[Json] = {
    "recipient": {"company": "Maison Lesage", "city": "Paris"},
    "subject": "Candidature au poste de chargée de communication",
    "greeting": "Madame, Monsieur,",
    "paragraphs": [
        {
            "kind": "interet_entreprise",
            "text": (
                "Votre maison défend depuis trente ans une idée exigeante de la communication : "
                "des messages justes, portés par des images soignées et une attention réelle aux "
                "personnes à qui l'on s'adresse. C'est cette exigence qui me conduit à vous écrire."
            ),
        },
        {
            "kind": "interet_poste",
            "text": (
                "Le poste de chargée de communication que vous ouvrez réunit ce que j'aime faire : "
                "écrire pour des publics différents, coordonner les prises de parole d'une équipe et "
                "mesurer ce qui a été compris. Je souhaite y apporter ma méthode et mon énergie."
            ),
        },
        {
            "kind": "competences",
            "text": (
                "Pendant trois ans chez Atelier Nord, j'ai conduit la refonte de la lettre "
                "d'information mensuelle, animé les réseaux sociaux de l'agence et accompagné "
                "douze lancements de produits, de la rédaction des communiqués au suivi des "
                "retombées. Les résultats ont été suivis et partagés chaque trimestre."
            ),
        },
        {
            "kind": "pourquoi_moi",
            "text": (
                "Organisée et attentive au détail, je travaille volontiers en équipe et je sais "
                "tenir un calendrier serré sans sacrifier la qualité des textes. Je serais heureuse "
                "de mettre ces qualités au service de votre maison et d'apprendre à vos côtés."
            ),
        },
        {
            "kind": "libre",
            "text": (
                "Je me tiens à votre disposition pour un entretien, à la date qui vous conviendra, "
                "et vous remercie de l'attention que vous porterez à ma candidature."
            ),
        },
    ],
    "closing": (
        "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées."
    ),
    "sender": {
        "first_name": "Camille",
        "last_name": "Marchand",
        "headline": "Chargée de communication",
        "email": "camille.marchand@exemple.fr",
        "phone": "06 12 34 56 78",
        "city": "Lyon",
    },
    "date_line": "Lyon, le 6 novembre 2026",
}
