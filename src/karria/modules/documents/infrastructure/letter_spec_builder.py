"""PDF d'une lettre appariée + rôles des lignes → spécification de lettre (ADR-0036).

Contrat
-------
Entrées : la page lue par `pdf_reader` (lignes, formes), les rôles que le
modèle de vision a donnés à chaque ligne (`labels`, un `LetterLineLabeler`), les
polices servies (`FontSet`, résolues par `FontLibrary`) et, s'il existe, la couleur
du **décor** du CV apparié (`accent`, celle que la personne peut changer). Sorties :

- `spec` — la **spécification de lettre** (`domain/plain_letter.py` en décrit la
  forme) : page, polices, styles par rôle, blocs d'en-tête ancrés à gauche ou à
  droite avec leurs lignes empilées au pas mesuré, colonne de texte et ses blocs
  (objet, civilité, paragraphes, formule, signature) avec leurs blancs mesurés,
  justification détectée, décorations absolues ;
- `sample` — le contenu d'exemple de la lettre, au format de la lettre
  (`domain/letter.py`), date imprimée comprise (`date_line`) : c'est avec lui
  que la fidélité se mesure.

Toute la géométrie, les polices et les couleurs viennent du PDF ; le modèle de
langage ne fournit que le **rôle** des lignes et le numéro de paragraphe. Ce que
le modèle n'a pas (un intitulé, une coordonnée, une ville de destinataire, la
date) reçoit un emplacement **dans sa grammaire** : le style de la ligne voisine,
le pas de la pile — jamais un blanc, jamais rien d'omis (`complete_spec`).
"""

from __future__ import annotations

import itertools
import re
import statistics
from collections import defaultdict
from typing import Any, Final

from karria.modules.documents.domain.plain_letter import SUBJECT_PREFIX
from karria.modules.documents.infrastructure.fonts import MM_PER_PT, FontSet
from karria.modules.documents.infrastructure.pdf_reader import PdfPage

Json = dict[str, Any]

LETTER_SPEC_VERSION: Final = 1
HEADER_ROLES: Final = frozenset(
    {
        "sender_name",
        "sender_headline",
        "sender_contact",
        "recipient_name",
        "recipient_address",
        "date_line",
    }
)
BODY_ROLES: Final = ("subject", "greeting", "paragraph", "closing", "signature")
#: Deux bords alignés à cette tolérance près (mm) le sont pour l'œil.
ALIGN_TOL_MM: Final = 1.5
#: Une ligne « pleine » d'un paragraphe justifié touche la marge droite à ce près (mm).
FULL_LINE_TOL_MM: Final = 1.0
#: Le préfixe de l'objet tel que le modèle l'écrit (« Objet : », « OBJET - »).
_SUBJECT_PREFIX: Final = re.compile(r"^\s*(objet\s*[:\-–—]\s*)", re.IGNORECASE)
_EMAIL: Final = re.compile(r"\S+@\S+\.\S+")
_PHONE: Final = re.compile(r"^[+()\d][\d\s().\-]{6,}$")
#: Ordre des champs de l'expéditeur dans la pile d'en-tête.
_SENDER_ORDER: Final = (
    "first_name",
    "last_name",
    "full_name",
    "headline",
    "address",
    "phone",
    "email",
)


def med(values: list[float], default: float = 0.0) -> float:
    """Médiane des valeurs connues."""
    return float(statistics.median(values)) if values else default


class LetterLine:
    """Une ligne du PDF de la lettre et son rôle."""

    def __init__(self, raw: Json, label: Json) -> None:
        self.raw = raw
        self.id: str = raw["id"]
        self.text: str = raw["text"]
        self.role: str = label["role"]
        self.paragraph: int = int(label.get("paragraph") or 0)
        self.x: float = raw["x"]
        self.y: float = raw["y"]
        self.w: float = raw["w"]
        self.h: float = raw["h"]

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


def contact_field(text: str) -> str:
    """`email`, `phone` ou `address`, d'après le texte d'une coordonnée."""
    if _EMAIL.search(text):
        return "email"
    if _PHONE.match(text.strip()):
        return "phone"
    return "address"


def split_subject(text: str) -> tuple[str, str]:
    """« Objet : Candidature… » → (« Objet : », « Candidature… ») ; sans préfixe : (« », texte)."""
    match = _SUBJECT_PREFIX.match(text)
    if match is None:
        return "", text.strip()
    return match.group(1), text[match.end() :].strip()


def title_case_name(text: str) -> str:
    """« DUBOIS » → « Dubois », « JEAN-MARC » → « Jean-Marc » ; un nom déjà casé ne bouge pas."""
    if text != text.upper():
        return text.strip()
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), text.strip())


class LetterSpecBuilder:
    """Construit la spécification d'une lettre appariée (une page)."""

    def __init__(
        self,
        page: PdfPage,
        labels: Json,
        fonts: FontSet,
        served: dict[str, tuple[str, int, bool]],
        *,
        accent: str | None = None,
    ) -> None:
        """
        Args:
            served: Pour chaque police du PDF (nom de base), la police servie
                `(famille, graisse, italique)`.
            accent: La couleur du **décor** du CV apparié (`primary_color_of`,
                `#RRGGBB`) : gardée dans `spec["accent"]`, et chaque style de texte
                qui a exactement cette couleur dans le PDF est marqué `follows_decor`
                — il suivra la couleur choisie pour le CV ; les autres textes restent
                du texte (ADR-0034 § 2026-11-06).
        """
        self.page = page
        self.fonts = fonts
        self.served = served
        self.accent = accent.upper() if accent else None
        by_id = {lb["line"]: lb for lb in labels.get("lines", [])}
        self.lines = [
            LetterLine(r, by_id[r["id"]])
            for r in page.lines
            if r["id"] in by_id and by_id[r["id"]]["role"] != "other"
        ]
        self.pw, self.ph = page.width_mm, page.height_mm
        self.styles: dict[str, Json] = {}

    # ------------------------------------------------------------------ styles
    def served_style(self, font: str) -> tuple[str, int, bool]:
        return self.served.get(font, ("sans-serif", 400, False))

    def letter_spacing(self, ln: LetterLine) -> float | None:
        """Interlettrage réel : (largeur mesurée − largeur naturelle de la police servie) / (n − 1)."""
        family, weight, italic = self.served_style(ln.raw["font"])
        if self.fonts.get(family, weight, italic) is None or len(ln.text) < 2:  # noqa: PLR2004
            return None
        probe = {
            "family": family,
            "weight": weight,
            "italic": italic,
            "size_pt": ln.raw["size"],
            "letter_spacing_pt": 0.0,
        }
        return (ln.w - self.fonts.width_mm(ln.text, probe)) / MM_PER_PT / (len(ln.text) - 1)

    def style_of(self, lines: list[LetterLine]) -> Json:
        """Le style d'un groupe de lignes de même rôle : police servie, corps, couleur, pas."""
        r = lines[0].raw
        family, weight, italic = self.served_style(r["font"])
        pitch = [b.y - a.y for a, b in itertools.pairwise(lines) if 0 < b.y - a.y < a.h * 2.2]
        right = max(ln.right for ln in lines)
        # Les lignes pleines d'un paragraphe justifié sont étirées : on mesure sur les autres.
        probe = [ln for ln in lines if len(lines) < 3 or right - ln.right > 1.0] or lines  # noqa: PLR2004
        spacing = [v for v in (self.letter_spacing(ln) for ln in probe) if v is not None]
        ls = med(spacing)
        # Sous 0,3 pt, l'écart mesuré est du bruit (crénage, arrondis) — sauf
        # s'il est le même sur plusieurs lignes : c'est alors l'écart de chasse
        # entre la police du modèle et sa remplaçante, à compenser sur chaque
        # ligne d'un paragraphe (sinon les retours à la ligne ne tombent plus
        # au même mot).
        consistent = len(spacing) >= 3 and statistics.pstdev(spacing) < 0.1  # noqa: PLR2004
        return {
            "family": family,
            "weight": weight,
            "italic": italic or bool(r.get("italic")),
            "size_pt": round(r["size"], 2),
            "color": r["color"],
            "letter_spacing_pt": round(ls, 2) if consistent or abs(ls) >= 0.3 else 0.0,  # noqa: PLR2004
            "uppercase": all(ln.raw["caps"] for ln in lines),
            "box_h_mm": round(med([ln.h for ln in lines]), 3),
            "pitch_mm": round(med(pitch), 3) if pitch else None,
            "source_font": r["font"],
        }

    def leading(self) -> float:
        """Rapport pas / boîte du texte courant — l'interligne du modèle, à réemployer."""
        para = self.styles.get("paragraph")
        if para and para.get("pitch_mm"):
            return float(para["pitch_mm"]) / float(para["box_h_mm"])
        return 1.15

    def natural_pitch(self, style: Json) -> float:
        """Le pas d'une ligne seule : la boîte au rapport d'interligne du modèle."""
        if style.get("pitch_mm"):
            return float(style["pitch_mm"])
        return round(float(style["box_h_mm"]) * self.leading(), 3)

    # ------------------------------------------------------------------ construction
    def build(self) -> tuple[Json, Json]:
        """La spécification et le contenu d'exemple."""
        by_role: dict[str, list[LetterLine]] = defaultdict(list)
        for ln in sorted(self.lines, key=lambda line: (line.y, line.x)):
            by_role[ln.role].append(ln)
        body_lines = [ln for role in BODY_ROLES for ln in by_role.get(role, [])]
        if not by_role.get("paragraph") or not body_lines:
            msg = "Aucun paragraphe reconnu dans la lettre."
            raise ValueError(msg)
        paragraphs = self.paragraphs(by_role["paragraph"])
        self.styles["paragraph"] = self.style_of([ln for p in paragraphs for ln in p])
        body_x = min(ln.x for ln in body_lines)
        body_right = max(ln.right for ln in body_lines)
        sample: Json = {
            "recipient": {"company": "", "city": ""},
            "subject": "",
            "greeting": "",
            "paragraphs": [],
            "closing": "",
            "sender": {},
            "date_line": "",
        }
        header_elements = self.header_elements(by_role, sample)
        blocks = self.header_blocks(header_elements, body_x, body_right)
        body = self.body_blocks(by_role, paragraphs, body_x, body_right, sample)
        decorations = self.decorations()
        fonts = sorted({(st["family"], st["weight"], st["italic"]) for st in self.styles.values()})
        spec: Json = {
            "kind": "letter",
            "version": LETTER_SPEC_VERSION,
            "source": {"producer": self.page.producer},
            "page": {"width_mm": self.pw, "height_mm": self.ph, "background": "#FFFFFF"},
            "fonts": [{"family": f, "weight": w, "italic": i} for f, w, i in fonts],
            "styles": dict(self.styles),
            "header": {"blocks": blocks, "decorations": []},
            "body": body,
            "page_decorations": decorations,
            "content_bottom_mm": round(max(ln.bottom for ln in self.lines), 2),
        }
        if self.accent is not None:
            spec["accent"] = self.accent
            for st in spec["styles"].values():
                if str(st.get("color", "")).upper() == self.accent:
                    st["follows_decor"] = True
        return complete_spec(spec), sample

    @staticmethod
    def paragraphs(lines: list[LetterLine]) -> list[list[LetterLine]]:
        """Les lignes des paragraphes, groupées par numéro (sinon par le blanc qui les sépare)."""
        groups: dict[int, list[LetterLine]] = defaultdict(list)
        for ln in lines:
            groups[ln.paragraph].append(ln)
        if len(groups) > 1 or (len(groups) == 1 and 0 not in groups):
            return [sorted(groups[k], key=lambda ln: ln.y) for k in sorted(groups)]
        # Un seul numéro (ou aucun) : un blanc supérieur à 1,6 × la boîte ouvre un paragraphe.
        out: list[list[LetterLine]] = []
        for ln in sorted(lines, key=lambda line: line.y):
            if out and ln.y - out[-1][-1].y < 1.6 * ln.h:
                out[-1].append(ln)
            else:
                out.append([ln])
        return out

    # ------------------------------------------------------------------ en-tête
    def header_elements(self, by_role: dict[str, list[LetterLine]], sample: Json) -> list[Json]:
        """Chaque ligne d'en-tête → un élément (champ, style, géométrie) ; le contenu d'exemple se remplit."""
        elements: list[Json] = []

        def add(field: str, key: str, ln: LetterLine) -> None:
            self.styles.setdefault(key, self.style_of([ln]))
            elements.append(
                {
                    "field": field,
                    "style": key,
                    "x": ln.x,
                    "y": ln.y,
                    "w": ln.w,
                    "h": ln.h,
                    "right": ln.right,
                }
            )

        names = by_role.get("sender_name", [])
        if len(names) == 1:
            add("full_name", "header.full_name", names[0])
            parts = names[0].text.split()
            sample["sender"]["first_name"] = title_case_name(parts[0]) if parts else ""
            sample["sender"]["last_name"] = title_case_name(" ".join(parts[1:]))
        elif names:
            add("first_name", "header.first_name", names[0])
            add("last_name", "header.last_name", names[-1])
            sample["sender"]["first_name"] = title_case_name(names[0].text)
            sample["sender"]["last_name"] = title_case_name(" ".join(n.text for n in names[1:]))
        headlines = by_role.get("sender_headline", [])
        if headlines:
            add("headline", "header.headline", headlines[0])
            text = " ".join(h.text for h in headlines)
            sample["sender"]["headline"] = text.capitalize() if text.isupper() else text
        seen: set[str] = set()
        for ln in by_role.get("sender_contact", []):
            field = contact_field(ln.text)
            if field in seen:
                continue
            seen.add(field)
            add(field, f"header.{field}", ln)
            sample["sender"]["city" if field == "address" else field] = ln.text
        companies = by_role.get("recipient_name", [])
        if companies:
            add("company", "recipient.company", companies[0])
            sample["recipient"]["company"] = " ".join(c.text for c in companies)
        addresses = by_role.get("recipient_address", [])
        if addresses:
            add("city", "recipient.city", addresses[0])
            sample["recipient"]["city"] = " ".join(a.text for a in addresses)
        dates = by_role.get("date_line", [])
        if dates:
            add("date_line", "date_line", dates[0])
            sample["date_line"] = dates[0].text
        return elements

    def anchor_of(  # noqa: PLR0911 - une table de décision, lue de haut en bas
        self, elems: list[Json], body_x: float, body_right: float
    ) -> tuple[str, float]:
        """L'ancrage d'un groupe de lignes : (`left` | `right` | `center`, coordonnée en mm)."""
        lefts = [e["x"] for e in elems]
        rights = [e["right"] for e in elems]
        tol = max(ALIGN_TOL_MM, 0.12 * max(float(e.get("h") or 0.0) for e in elems))
        left_aligned = max(lefts) - min(lefts) <= tol
        right_aligned = max(rights) - min(rights) <= tol
        if left_aligned and not right_aligned:
            return "left", med(lefts)
        if right_aligned and not left_aligned:
            return "right", round(self.pw - med(rights), 2)
        # Une ligne seule : ce qu'elle touche décide — la marge droite du texte,
        # sa marge gauche, ou le centre de la page.
        if (
            abs(med(rights) - body_right) <= ALIGN_TOL_MM
            and abs(med(lefts) - body_x) > ALIGN_TOL_MM
        ):
            return "right", round(self.pw - med(rights), 2)
        if abs(med(lefts) - body_x) <= ALIGN_TOL_MM:
            return "left", med(lefts)
        center = med([(e["x"] + e["right"]) / 2 for e in elems])
        if abs(center - self.pw / 2) <= 2.0 and med(lefts) > body_x + 10:  # noqa: PLR2004
            return "center", round(center, 2)
        if med(rights) > self.pw * 0.75:
            return "right", round(self.pw - med(rights), 2)
        return "left", med(lefts)

    def header_blocks(  # noqa: PLR0912 - regrouper, ancrer, puis borner chaque bloc
        self, elements: list[Json], body_x: float, body_right: float
    ) -> list[Json]:
        """Les éléments empilés en blocs : même bord, à la suite les uns des autres."""
        groups: list[list[Json]] = []
        for e in sorted(elements, key=lambda e: (e["y"], e["x"])):
            placed = False
            for g in groups:
                last = g[-1]
                # Un nom en très grand corps a des approches latérales de
                # plusieurs millimètres : la tolérance suit la taille.
                tol = max(ALIGN_TOL_MM, 0.12 * max(last["h"], e["h"]))
                same_left = abs(e["x"] - last["x"]) <= tol
                same_right = abs(e["right"] - last["right"]) <= tol
                same_axis = abs((e["x"] + e["right"]) - (last["x"] + last["right"])) <= 2 * tol
                follows = 0 <= e["y"] - last["y"] <= 3.0 * max(last["h"], e["h"])
                if (same_left or same_right or same_axis) and follows:
                    g.append(e)
                    placed = True
                    break
            if not placed:
                groups.append([e])
        blocks: list[Json] = []
        for g in groups:
            anchor, coord = self.anchor_of(g, body_x, body_right)
            elems = []
            for i, e in enumerate(g):
                style = self.styles[e["style"]]
                pitch = g[i + 1]["y"] - e["y"] if i + 1 < len(g) else self.natural_pitch(style)
                elems.append(
                    {
                        "field": e["field"],
                        "style": e["style"],
                        "pitch_mm": round(pitch, 3),
                        "w_mm": round(e["w"], 2),
                    }
                )
            block: Json = {"anchor": anchor, "y_mm": round(g[0]["y"], 2), "elements": elems}
            if anchor == "left":
                block["x_mm"] = round(coord, 2)
            elif anchor == "right":
                block["right_mm"] = coord
            else:
                block["cx_mm"] = coord
            block["_top"] = g[0]["y"]
            block["_bottom"] = max(e["y"] + e["h"] for e in g)
            block["_left"] = min(e["x"] for e in g)
            block["_right"] = max(e["right"] for e in g)
            blocks.append(block)
        for b in blocks:
            others = [
                o
                for o in blocks
                if o is not b and o["_top"] < b["_bottom"] + 2 and o["_bottom"] > b["_top"] - 2
            ]
            if b["anchor"] == "left":
                limit = min(
                    [o["_left"] for o in others if o["_left"] > b["_left"] + 5] + [body_right]
                )
                b["max_w_mm"] = round(max(limit - b["x_mm"] - 3.0, 20.0), 2)
            elif b["anchor"] == "right":
                edge = self.pw - b["right_mm"]
                floor = max(
                    [o["_right"] for o in others if o["_right"] < b["_right"] - 5] + [body_x]
                )
                b["max_w_mm"] = round(max(edge - floor - 3.0, 20.0), 2)
            else:
                b["max_w_mm"] = round(body_right - body_x, 2)
        for b in blocks:
            for key in ("_top", "_bottom", "_left", "_right"):
                del b[key]
        return blocks

    # ------------------------------------------------------------------ corps
    def alignment(self, lines: list[LetterLine], body_right: float) -> str:
        """Justifié quand les lignes non finales touchent la marge droite du texte."""
        inner = lines[:-1]
        if not inner:
            return "left"
        full = [ln for ln in inner if body_right - ln.right <= FULL_LINE_TOL_MM]
        return "justify" if len(full) >= max(1, 0.6 * len(inner)) else "left"

    def body_blocks(
        self,
        by_role: dict[str, list[LetterLine]],
        paragraphs: list[list[LetterLine]],
        body_x: float,
        body_right: float,
        sample: Json,
    ) -> Json:
        """La colonne de texte : ses blocs dans l'ordre vertical, avec leurs blancs mesurés."""
        entries: list[tuple[float, str, list[LetterLine]]] = []
        for role in ("subject", "greeting", "closing", "signature"):
            lines = by_role.get(role, [])
            if lines:
                entries.append((lines[0].y, role, lines))
        entries.append((paragraphs[0][0].y, "paragraph", [ln for p in paragraphs for ln in p]))
        entries.sort(key=lambda t: t[0])
        blocks: list[Json] = []
        prev_bottom: float | None = None
        for _, role, lines in entries:
            if role != "paragraph":
                self.styles.setdefault(role, self.style_of(lines))
            style = self.styles[role]
            first = lines[0]
            block: Json = {
                "role": role,
                "style": role,
                "align": "left",
                "gap_before_mm": 0.0 if prev_bottom is None else round(first.y - prev_bottom, 2),
            }
            if role == "paragraph":
                gaps = [b[0].y - (a[-1].y + a[-1].h) for a, b in itertools.pairwise(paragraphs)]
                block["gap_between_mm"] = round(med(gaps, self.natural_pitch(style)), 2)
                aligns = [self.alignment(p, body_right) for p in paragraphs if len(p) > 1]
                block["align"] = (
                    "justify" if aligns and aligns.count("justify") * 2 > len(aligns) else "left"
                )
                sample["paragraphs"] = [
                    {"kind": "libre", "text": " ".join(ln.text for ln in p)} for p in paragraphs
                ]
                prev_bottom = paragraphs[-1][-1].bottom
            else:
                text = " ".join(ln.text for ln in lines)
                if role == "subject":
                    prefix, text = split_subject(text)
                    block["prefix"] = prefix or SUBJECT_PREFIX
                elif len(lines) > 1:
                    block["align"] = self.alignment(lines, body_right)
                if role == "signature":
                    text = title_case_name(text)
                else:
                    sample[role] = text
                anchor, _ = self.anchor_of(
                    [{"x": ln.x, "right": ln.right} for ln in lines], body_x, body_right
                )
                if anchor != "left" and block["align"] == "left":
                    block["align"] = anchor
                prev_bottom = lines[-1].bottom
            blocks.append(block)
        first_block = entries[0]
        return {
            "x_mm": round(body_x, 2),
            "w_mm": round(body_right - body_x, 2),
            "top_mm": round(first_block[0], 2),
            "blocks": blocks,
        }

    # ------------------------------------------------------------------ décor
    def decorations(self) -> list[Json]:
        """Les formes du modèle, hors fond de page, en position absolue."""
        out: list[Json] = []
        for d in self.page.drawings:
            if d["kind"] == "rect" and d["w"] > self.pw * 0.95 and d["h"] > self.ph * 0.95:
                continue
            if not (d.get("fill") or d.get("stroke")):
                continue
            out.append(
                {
                    "kind": d["kind"],
                    "x_mm": round(d["x"], 2),
                    "y_mm": round(d["y"], 2),
                    "w_mm": round(d["w"], 2),
                    "h_mm": round(d["h"], 2),
                    "fill": d.get("fill"),
                    "stroke": d.get("stroke"),
                    "stroke_pt": d.get("stroke_width_pt"),
                    "anchor": "page",
                }
            )
        return out


# ---------------------------------------------------------------------- complétion


def _derived(style: Json, *, weight: int | None = None, uppercase: bool | None = None) -> Json:
    """Un style voisin, pour un champ que le modèle n'a pas."""
    out = {k: v for k, v in style.items() if k != "source_font"}
    if weight is not None:
        out["weight"] = weight
    if uppercase is not None:
        out["uppercase"] = uppercase
    out["synthesized"] = True
    return out


def _elements(spec: Json) -> dict[str, Json]:
    return {e["field"]: b for b in spec["header"]["blocks"] for e in b["elements"]}


def _insert_after(block: Json, after_fields: tuple[str, ...], element: Json) -> None:
    """Pose un élément après le dernier des champs donnés présents dans le bloc (sinon à la fin)."""
    fields = [e["field"] for e in block["elements"]]
    index = max([fields.index(f) + 1 for f in after_fields if f in fields] or [len(fields)])
    block["elements"].insert(index, element)


def complete_spec(spec: Json) -> Json:  # noqa: PLR0912, PLR0915 - une règle par champ qui peut manquer
    """Donne un emplacement, dans la grammaire du modèle, à chaque champ qu'il n'a pas.

    L'intitulé prend le style des coordonnées (ou du nom en graisse normale) sous
    le nom ; une coordonnée manquante suit la dernière coordonnée ; la ville du
    destinataire suit l'entreprise en graisse normale ; la date, s'il n'y en a
    pas, se pose à droite sous le destinataire ; l'objet, la civilité, la formule
    et la signature prennent le style des paragraphes (l'objet en gras). La
    spécification d'origine n'est pas modifiée.
    """
    spec = {
        **spec,
        "styles": dict(spec["styles"]),
        "header": {
            **spec["header"],
            "blocks": [
                {**b, "elements": [dict(e) for e in b["elements"]]}
                for b in spec["header"]["blocks"]
            ],
        },
        "body": {**spec["body"], "blocks": [dict(b) for b in spec["body"]["blocks"]]},
    }
    styles = spec["styles"]
    para = styles["paragraph"]
    pitch = float(para.get("pitch_mm") or para["box_h_mm"] * 1.15)
    present = _elements(spec)
    name_fields = [f for f in ("full_name", "first_name", "last_name") if f in present]
    sender_block = present[name_fields[-1]] if name_fields else None
    contact_fields = [f for f in ("address", "phone", "email") if f in present]
    if sender_block is None and contact_fields:
        sender_block = present[contact_fields[0]]
    if sender_block is None:
        # Aucune ligne d'expéditeur : une pile à gauche, en haut de la colonne de texte.
        sender_block = {
            "anchor": "left",
            "x_mm": spec["body"]["x_mm"],
            "y_mm": max(10.0, spec["body"]["top_mm"] - 6 * pitch),
            "max_w_mm": spec["body"]["w_mm"] * 0.6,
            "elements": [],
        }
        spec["header"]["blocks"].insert(0, sender_block)
    if not name_fields:
        styles.setdefault("header.full_name", _derived(para, weight=700))
        sender_block["elements"].insert(
            0, {"field": "full_name", "style": "header.full_name", "pitch_mm": pitch, "w_mm": None}
        )
    contact_style_key = f"header.{contact_fields[-1]}" if contact_fields else None
    if "headline" not in present:
        base = styles[contact_style_key] if contact_style_key else para
        styles["header.headline"] = _derived(base, weight=400, uppercase=False)
        _insert_after(
            sender_block,
            ("full_name", "first_name", "last_name"),
            {"field": "headline", "style": "header.headline", "pitch_mm": pitch, "w_mm": None},
        )
    for field in ("address", "phone", "email"):
        if field in present:
            continue
        base = (
            styles[contact_style_key] if contact_style_key else styles.get("header.headline", para)
        )
        styles[f"header.{field}"] = _derived(base, uppercase=False)
        _insert_after(
            sender_block,
            ("headline", "address", "phone", "email"),
            {"field": field, "style": f"header.{field}", "pitch_mm": pitch, "w_mm": None},
        )
    if "company" not in present:
        styles.setdefault("recipient.company", _derived(para, weight=700))
        target = present.get("city") or present.get("date_line")
        if target is None:
            target = {
                "anchor": "right",
                "right_mm": round(
                    spec["page"]["width_mm"] - spec["body"]["x_mm"] - spec["body"]["w_mm"], 2
                ),
                "y_mm": max(sender_block["y_mm"] + 2 * pitch, spec["body"]["top_mm"] - 5 * pitch),
                "max_w_mm": spec["body"]["w_mm"] * 0.5,
                "elements": [],
            }
            spec["header"]["blocks"].append(target)
        target["elements"].insert(
            0,
            {"field": "company", "style": "recipient.company", "pitch_mm": pitch, "w_mm": None},
        )
        present = _elements(spec)
    if "city" not in present:
        styles.setdefault("recipient.city", _derived(styles["recipient.company"], weight=400))
        _insert_after(
            present["company"],
            ("company",),
            {"field": "city", "style": "recipient.city", "pitch_mm": pitch, "w_mm": None},
        )
        present = _elements(spec)
    if "date_line" not in present:
        styles.setdefault("date_line", _derived(para))
        host = present["company"]
        host["elements"].append(
            {"field": "date_line", "style": "date_line", "pitch_mm": pitch, "w_mm": None}
        )
        # La date après une ligne de blanc sous le destinataire.
        host["elements"][-2]["pitch_mm"] = round(2 * pitch, 3)
    roles = [b["role"] for b in spec["body"]["blocks"]]
    order = {role: i for i, role in enumerate(BODY_ROLES)}
    for role in BODY_ROLES:
        if role in roles:
            continue
        styles.setdefault(role, _derived(para, weight=700 if role == "subject" else None))
        block: Json = {
            "role": role,
            "style": role,
            "align": "left",
            "gap_before_mm": round(pitch, 2),
            **({"prefix": SUBJECT_PREFIX} if role == "subject" else {}),
        }
        index = next(
            (i for i, b in enumerate(spec["body"]["blocks"]) if order[b["role"]] > order[role]),
            len(spec["body"]["blocks"]),
        )
        spec["body"]["blocks"].insert(index, block)
        if index + 1 < len(spec["body"]["blocks"]):
            spec["body"]["blocks"][index + 1]["gap_before_mm"] = max(
                float(spec["body"]["blocks"][index + 1]["gap_before_mm"]), round(pitch, 2)
            )
    spec["body"]["blocks"][0]["gap_before_mm"] = 0.0
    spec["fonts"] = [
        {"family": f, "weight": w, "italic": i}
        for f, w, i in sorted(
            {(st["family"], st["weight"], st["italic"]) for st in styles.values()}
        )
    ]
    return spec
