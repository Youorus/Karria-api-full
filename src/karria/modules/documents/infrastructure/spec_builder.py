"""PDF de modèle + rôles des lignes → spécification canonique de mise en page (version 1).

Contrat
-------
Entrées : la page lue par `pdf_reader` (lignes, formes, images), les rôles que
le modèle de vision a donnés à chaque ligne (`labels`), et les polices servies
(`FontSet`, résolues par `fonts.FontLibrary`). Sorties :

- `spec` — page, polices, styles par rôle et par rubrique, en-tête (positions
  absolues), bandes → colonnes → rubriques, gabarit d'élément (emplacements,
  marges mesurées, retraits, alignement), décorations ancrées (élément, ligne,
  rubrique, bande, page), puces, pictogrammes ;
- `sample` — le contenu d'exemple du modèle, au format du CV (pour mesurer la
  fidélité en rendant le modèle avec son propre contenu) ;
- `assets` — pictogrammes rastérisés à 600 dpi, photo d'exemple (octets PNG).

Toute la géométrie, les polices et les couleurs viennent du PDF ; le modèle
de langage ne fournit que le **rôle** des lignes (ADR-0020 § 1). Les mesures
sont **relatives** (marges, retraits) sauf dans l'en-tête : un intitulé qui
passe sur deux lignes ne casse pas la page.

Détails mesurés pendant la preuve, à ne pas « simplifier » :

- l'interlettrage se recalcule avec la **police servie** (largeur mesurée −
  largeur naturelle) ; sous 0,3 pt c'est du crénage : 0. Avec une police de
  substitution, le même calcul compense l'écart de chasse ;
- Canva détoure des carrés en ronds : la forme se juge sur le rendu
  (`PdfDocument.is_round`) ;
- un trait est parfois dessiné deux fois (filet + forme pleine) : on garde le filet.
"""

from __future__ import annotations

import itertools
import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Final

from karria.modules.documents.domain.fonts import family_match_key
from karria.modules.documents.domain.headline import synthesize_headline_style
from karria.modules.documents.infrastructure.fonts import MM_PER_PT, FontSet
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument, PdfPage

Json = dict[str, Any]

MM: Final = 72 / 25.4
SPEC_VERSION: Final = 1
ITEM_SECTIONS: Final = frozenset({"experiences", "educations", "projects", "references"})
LIST_ROLES: Final = frozenset({"skill", "language", "interest"})
LIST_KINDS: Final = frozenset({"skills", "languages", "interests"})
#: Écart relatif de corps à partir duquel un élément de l'exemple garde le sien.
SIZE_DEPARTURE: Final = 0.10
#: Un tracé complexe au moins aussi grand (mm) se rend en image plutôt qu'en forme.
RASTER_PATH_MM: Final = 8.0
#: Une forme pleine plus basse que ceci et plus large que cela dans une liste est une
#: barre de niveau (mm) ; une puce (1 mm) n'en est pas une.
LEVEL_BAR_MAX_H_MM: Final = 3.0
LEVEL_BAR_MIN_W_MM: Final = 5.0
#: Les rôles dont l'exemple garde les retours à la ligne de la source (intitulés), pas les paragraphes.
KEEP_BREAKS: Final = frozenset({"item_title", "item_subtitle"})
#: Espacement des mots retenu d'un paragraphe justifié : ni le bruit de mesure, ni une aberration.
WORD_SPACING_MIN_MM: Final = 0.1
WORD_SPACING_MAX_MM: Final = 1.5
#: Les coordonnées, dans l'en-tête, une rubrique Contact ou un élément (une référence).
CONTACT_ROLES: Final = ("phone", "email", "address", "website")
#: Préfixe des rubriques libres (« Réalisations », « Certifications »…), gardées
#: avec leur titre : `other:<slug>` (ADR-0037).
OTHER_PREFIX: Final = "other:"


def overlaps(a: Json, b: Json) -> bool:
    """Deux boîtes `x, y, w, h` (mm) qui se recouvrent."""
    return bool(
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"]
        and b["y"] < a["y"] + a["h"]
    )


def is_item_kind(kind: str) -> bool:
    """Une rubrique dont les éléments se répètent (expériences, formations, rubrique libre)."""
    return kind in ITEM_SECTIONS or kind.startswith(OTHER_PREFIX)


def med(values: list[float] | list[float | None], default: float = 0.0) -> float:
    """Médiane des valeurs connues."""
    known = [v for v in values if v is not None]
    return float(statistics.median(known)) if known else default


def majority_alignment(votes: list[str]) -> str:
    """L'alignement d'un emplacement : celui de la majorité de ses paragraphes repliés, à gauche sinon."""
    counted = Counter(v for v in votes if v != "left")
    if not counted:
        return "left"
    value, count = counted.most_common(1)[0]
    return value if count * 2 > len(votes) else "left"


def same_style(a: Json, b: Json) -> bool:
    """Deux styles mesurés qui se rendent pareil : même police, graisse, corps, couleur, capitales."""
    return bool(
        a["family"] == b["family"]
        and a["weight"] == b["weight"]
        and a["italic"] == b["italic"]
        and abs(float(a["size_pt"]) - float(b["size_pt"])) < 0.3  # noqa: PLR2004
        and a["color"] == b["color"]
        and a["uppercase"] == b["uppercase"]
    )


def reference(values: list[float]) -> float:
    """La valeur de référence d'une mesure répétée : la médiane, ou la **première** quand il n'y en a que deux.

    Deux mesures n'ont pas de médiane : la moyenne mettrait les deux éléments
    à côté de leur place (mesuré sur un modèle Canva dont le second élément
    est décalé de 4 mm par rapport au premier). Le premier élément est la
    référence du dessin ; au-delà de deux, la médiane écarte l'exception.
    """
    if len(values) == 2:  # noqa: PLR2004
        return float(values[0])
    return med(values)


def collapse_spaced(text: str) -> str:
    """« B E N J A M I N » (lettres séparées par des espaces tapées) → « BENJAMIN »."""
    joined = text.replace(" ", "")
    if re.fullmatch(r"(\S )+\S", text) and len(joined) >= 3 and joined.isalpha():  # noqa: PLR2004
        return joined
    return text


class Line:
    """Une ligne du PDF et son rôle."""

    def __init__(self, raw: Json, label: Json) -> None:
        self.raw = raw
        self.id: str = raw["id"]
        self.text: str = collapse_spaced(raw["text"])
        self.role: str = label["role"]
        self.section: str = label["section"]
        self.item: int = int(label["item"])
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


class SpecBuilder:
    """Construit la spécification d'un modèle (une page)."""

    def __init__(
        self,
        doc: PdfDocument,
        page: PdfPage,
        labels: Json,
        fonts: FontSet,
        served: dict[str, tuple[str, int, bool]],
        *,
        substituted: frozenset[str] = frozenset(),
    ) -> None:
        """
        Args:
            served: Pour chaque police du PDF (nom de base), la police servie
                `(famille, graisse, italique)`.
        """
        self.doc = doc
        self.page = page
        #: Familles de la source servies par substitution (clés de `family_match_key`).
        self.substituted = substituted
        self.fonts = fonts
        self.served = served
        self.meta = {
            k: labels.get(k)
            for k in ("title", "category", "description_fr", "tags", "photo_shape", "columns")
        }
        by_id = {lb["line"]: lb for lb in labels["lines"]}
        self.lines = [Line(r, by_id[r["id"]]) for r in page.lines if r["id"] in by_id]
        self.pw, self.ph = page.width_mm, page.height_mm
        self.assets: dict[str, bytes] = {}
        drawings = [
            dict(d)
            for d in page.drawings
            if not (d["kind"] == "rect" and d["w"] > self.pw * 0.95 and d["h"] > self.ph * 0.95)
        ]
        strokes = [d for d in drawings if d["kind"] in ("vline", "hline") and d.get("stroke")]

        def duplicate(d: Json) -> bool:
            if d.get("stroke") or not d.get("fill") or min(d["w"], d["h"]) > 2:  # noqa: PLR2004
                return False
            return any(
                abs((d["x"] + d["w"] / 2) - (v["x"] + v["w"] / 2)) < 1.2  # noqa: PLR2004
                and abs((d["y"] + d["h"] / 2) - (v["y"] + v["h"] / 2)) < 1.2  # noqa: PLR2004
                and abs(max(d["w"], d["h"]) - max(v["w"], v["h"])) < 1.5  # noqa: PLR2004
                for v in strokes
            )

        self.drawings = [d for d in drawings if not duplicate(d)]
        self.used: set[int] = set()
        self._col_right = 0.0

    # ------------------------------------------------------------------ styles
    def served_style(self, font: str) -> tuple[str, int, bool]:
        """La police servie pour une police du PDF."""
        return self.served.get(font, ("sans-serif", 400, False))

    def letter_spacing(self, ln: Line) -> float | None:
        """Interlettrage réel : (largeur mesurée − largeur naturelle de la police servie) / (n − 1)."""
        r = ln.raw
        family, weight, italic = self.served_style(r["font"])
        if self.fonts.get(family, weight, italic) is None or len(ln.text) < 2:  # noqa: PLR2004
            return None
        probe = {
            "family": family,
            "weight": weight,
            "italic": italic,
            "size_pt": r["size"],
            "letter_spacing_pt": 0.0,
        }
        natural_mm = self.fonts.width_mm(ln.text, probe)
        return (ln.w - natural_mm) / MM_PER_PT / (len(ln.text) - 1)

    def style_of(self, lines: list[Line]) -> Json:
        """Le style d'un groupe de lignes de même rôle : police servie, corps, couleur, interlignes."""
        r = lines[0].raw
        family, weight, italic = self.served_style(r["font"])
        pitch = [b.y - a.y for a, b in itertools.pairwise(lines) if 0 < b.y - a.y < a.h * 2.2]
        right = max(ln.right for ln in lines)
        # Les lignes pleines d'un paragraphe justifié sont étirées : on mesure sur les autres.
        probe = [ln for ln in lines if len(lines) < 3 or right - ln.right > 1.0] or lines  # noqa: PLR2004
        spacing = [v for v in (self.letter_spacing(ln) for ln in probe) if v is not None]
        ls = med(spacing) if spacing else 0.0
        return {
            "family": family,
            "weight": weight,
            "italic": italic,
            "size_pt": round(r["size"], 2),
            "color": r["color"],
            # Sous 0,3 pt, l'écart mesuré est du bruit (crénage, arrondis) : interlettrage nul.
            "letter_spacing_pt": 0.0 if abs(ls) < 0.3 else round(ls, 2),  # noqa: PLR2004
            "uppercase": all(ln.raw["caps"] for ln in lines),
            "box_h_mm": round(med([ln.h for ln in lines]), 3),
            "pitch_mm": round(med(pitch), 3) if pitch else None,
            "source_font": r["font"],
        }

    # ------------------------------------------------------------------ structure
    def sections(self) -> list[Json]:
        """Une rubrique = son titre + ses lignes ; colonne par abscisse du titre."""
        titles = sorted(
            (ln for ln in self.lines if ln.role == "section_title"), key=lambda ln: (ln.y, ln.x)
        )
        merged: list[Json] = []
        for t in titles:
            prev = next(
                (
                    s
                    for s in merged
                    if s["kind"] == t.section
                    and abs(s["x"] - t.x) < 3  # noqa: PLR2004
                    and 0 < t.y - s["title"][-1].y < t.h * 2.2
                ),
                None,
            )
            if prev:
                prev["title"].append(t)
                continue
            merged.append({"kind": t.section, "x": t.x, "y": t.y, "title": [t]})
        xs = sorted({round(s["x"]) for s in merged})
        cols: list[float] = []
        for x in xs:
            if not cols or x - cols[-1] > 15:  # noqa: PLR2004
                cols.append(x)
        for s in merged:
            s["col"] = min(range(len(cols)), key=lambda i: abs(cols[i] - s["x"]))
            s["lines"] = [
                ln for ln in self.lines if ln.section == s["kind"] and ln.role != "section_title"
            ]
        return merged

    def build(self) -> tuple[Json, Json, dict[str, bytes]]:
        """La spécification, le contenu d'exemple et les fichiers associés."""
        secs = self.sections()
        if not secs:
            msg = "Aucun titre de rubrique reconnu dans le modèle."
            raise ValueError(msg)
        first_top = min(s["y"] for s in secs)
        header_lines = [ln for ln in self.lines if ln.section == "header" or ln.y < first_top - 1]
        starts = sorted({round(s["y"], 1) for s in secs})
        bands: list[Json] = []
        ncols_total = len({s["col"] for s in secs})
        for y in starts:
            row = [s for s in secs if abs(s["y"] - y) < 2]  # noqa: PLR2004
            if (not bands or (len({s["col"] for s in row}) > 1 and ncols_total > 1)) and (
                not bands or y > bands[-1]["y"] + 2
            ):
                bands.append({"y": y, "sections": []})
        for s in sorted(secs, key=lambda s: s["y"]):
            band = max((b for b in bands if b["y"] <= s["y"] + 2), key=lambda b: b["y"])
            band["sections"].append(s)
        ncols = ncols_total
        col_x = {c: min(s["x"] for s in secs if s["col"] == c) for c in range(ncols)}
        col_right: dict[int, float] = {}
        for c in range(ncols):
            rights = [ln.right for s in secs if s["col"] == c for ln in s["lines"] + s["title"]]
            col_right[c] = max(rights)
        for c in range(ncols - 1):
            col_right[c] = min(col_right[c], col_x[c + 1] - 2)
        styles: dict[str, Json] = {}
        sample: Json = {
            "identity": {},
            "contact": {},
            "summary": None,
            "experiences": [],
            "educations": [],
            "skills": [],
            "languages": [],
            "interests": [],
            "projects": [],
        }
        spec_bands: list[Json] = []
        for band in bands:
            columns = []
            for c in range(ncols):
                col_secs = [s for s in band["sections"] if s["col"] == c]
                spec_secs = []
                prev_bottom = band["y"]
                for s in col_secs:
                    spec_secs.append(self.section_spec(s, col_x[c], prev_bottom, styles, sample))
                    prev_bottom = s["bottom"]
                columns.append(
                    {
                        "x_mm": round(col_x[c], 2),
                        "w_mm": round(col_right[c] - col_x[c], 2),
                        "sections": spec_secs,
                        "bottom_sample": prev_bottom,
                    }
                )
            spec_bands.append({"columns": columns, "y_sample": band["y"]})
        for i, spec_band in enumerate(spec_bands):
            bottom = max(c["bottom_sample"] for c in spec_band["columns"])
            spec_band["bottom_sample"] = bottom
            if i + 1 < len(spec_bands):
                spec_band["gap_after_mm"] = round(spec_bands[i + 1]["y_sample"] - bottom, 2)
        header = self.header_spec(header_lines, first_top, styles, sample)
        page_decor = self.page_decorations(spec_bands)
        fonts = sorted({(st["family"], st["weight"], st["italic"]) for st in styles.values()})
        spec: Json = {
            "version": SPEC_VERSION,
            "source": {"producer": self.page.producer},
            "meta": self.meta,
            "page": {"width_mm": self.pw, "height_mm": self.ph, "background": "#FFFFFF"},
            "fonts": [{"family": f, "weight": w, "italic": i} for f, w, i in fonts],
            "styles": styles,
            "header": header,
            "bands": spec_bands,
            "page_decorations": page_decor,
            "first_band_top_mm": round(first_top, 2),
            "content_bottom_mm": round(max(ln.bottom for ln in self.lines), 2),
        }
        return spec, sample, self.assets

    # ------------------------------------------------------------------ rubriques
    def section_spec(  # noqa: PLR0912, PLR0915 - mesure d'un gabarit, emplacement par emplacement
        self, s: Json, col_x: float, prev_bottom: float, styles: dict[str, Json], sample: Json
    ) -> Json:
        """Le gabarit d'une rubrique : titre, emplacements, puces, décor, colonne latérale.

        `s["title"]` peut être vide (rubrique sans titre : un profil, des
        coordonnées sous la photo) ; `s["side_roles"]` nomme les rôles posés
        dans une colonne latérale à gauche du contenu (frise chronologique) :
        leurs lignes se mesurent à part (`side`), le reste comme avant.
        """
        kind = s["kind"]
        title = s["title"]
        side_roles = frozenset(s.get("side_roles") or ())
        all_lines = sorted(s["lines"], key=lambda ln: (ln.y, ln.x))
        lines = [ln for ln in all_lines if ln.role not in side_roles]
        side_lines = [ln for ln in all_lines if ln.role in side_roles]
        self._col_right = max([ln.right for ln in all_lines] + [t.right for t in title])
        top = min([t.y for t in title] + [ln.y for ln in all_lines])
        title_key: str | None = None
        if title:
            # Un style de titre partagé ; une rubrique dont le titre diffère
            # (blanc sur une colonne sombre, foncé à côté) garde le sien.
            own = self.style_of(title)
            shared = styles.setdefault("section_title", own)
            title_key = "section_title"
            if not same_style(own, shared):
                title_key = f"{kind}.section_title"
                styles.setdefault(title_key, own)
            title_bottom = title[-1].y + (styles[title_key]["pitch_mm"] or title[-1].h)
        else:
            title_bottom = top
        # Les étiquettes fixes du modèle (« Phone », « Email : ») ne sont pas des
        # données : elles se mesurent à part et s'attachent au champ qu'elles annoncent.
        label_lines = [ln for ln in lines if ln.role == "label"]
        lines = [ln for ln in lines if ln.role != "label"]
        markers = self.markers_for(lines)
        elements = self.elements(lines, markers)
        labels = self.attach_labels(self.elements(label_lines, {}), elements) if label_lines else {}
        side_elements = self.elements(side_lines, {}) if side_lines else []
        bottom = max([title_bottom] + [e["bottom"] for e in elements + side_elements])
        s["bottom"] = bottom
        s["elements"] = elements
        spec: Json = {
            "kind": kind,
            "title_sample": " ".join(t.text for t in title),
            "title_style": title_key,
            "untitled": not title,
            "margin_top_mm": round(top - prev_bottom, 2),
            "title_dx_mm": round(title[0].x - col_x, 2) if title else 0.0,
            "title_w_mm": round(max(t.w for t in title) + 1.5, 2) if len(title) > 1 else None,
            # Un titre sur plusieurs lignes garde son interligne mesuré.
            "title_pitch_mm": round(med([b.y - a.y for a, b in itertools.pairwise(title)]), 3)
            if len(title) > 1
            else None,
            "title_sep": next((ln.text for ln in lines if ln.role == "item_title"), ""),
        }
        groups: dict[int, list[Json]] = defaultdict(list)
        for e in elements:
            groups[e["item"] if is_item_kind(kind) else 0].append(e)
        side_groups: dict[int, list[Json]] = defaultdict(list)
        for e in side_elements:
            side_groups[e["item"] if is_item_kind(kind) else 0].append(e)
        # Les éléments principaux d'abord : une pastille centrée sur une ligne
        # composée s'attache au champ du contenu, pas à celui de la colonne latérale.
        all_groups: dict[int, list[Json]] = defaultdict(list)
        for no in sorted(set(groups) | set(side_groups)):
            all_groups[no] = groups.get(no, []) + side_groups.get(no, [])
        by_role: dict[str, list[Json]] = defaultdict(list)
        for e in elements + side_elements + [lb for lb, _ in labels.values()]:
            by_role[e["role"]].append(e)
        for role, es in by_role.items():
            skey = f"{kind}.{role}"
            if skey in styles:
                continue
            st = self.style_of([ln for e in es for ln in e["lines"]])
            pitches = [
                b.y - a.y for e in es for a, b in zip(e["lines"], e["lines"][1:], strict=False)
            ]
            st["pitch_mm"] = round(med(pitches), 3) if pitches else None
            word_spacing = self.word_spacing(es, st)
            if word_spacing is not None:
                st["word_spacing_mm"] = word_spacing
            styles[skey] = st
        slots: dict[str, Json] = {}
        gaps_item: list[float] = []
        first_gap: list[float] = []
        previous_item_bottom: float | None = None
        previous_item_top: float | None = None
        # Des éléments côte à côte (deux références en colonnes) : une grille
        # d'éléments au pas mesuré ; chaque emplacement se mesure depuis la
        # colonne de son élément, et deux voisins de rangée n'ont pas d'écart.
        item_grid = self.item_grid(all_groups) if is_item_kind(kind) else {}
        origins: dict[int, float] = item_grid.pop("origins", {})
        spec.update(item_grid)
        for item_no in sorted(all_groups):
            origin_x = col_x + origins.get(item_no, 0.0)
            rows = self.rows(groups[item_no]) if groups.get(item_no) else []
            # Une liste en grille (deux entrées par ligne) : une entrée par rangée,
            # le nombre de colonnes se mesure ensuite (`grid_spec`).
            rows = (
                [
                    {"top": e["top"], "bottom": e["bottom"], "elems": [e]}
                    for r in rows
                    for e in r["elems"]
                ]
                if any(
                    all(e["role"] in LIST_ROLES for e in r["elems"]) and len(r["elems"]) > 1
                    for r in rows
                )
                else rows
            )
            all_rows = self.rows(all_groups[item_no])
            item_top = rows[0]["top"] if rows else all_rows[0]["top"]
            same_row = previous_item_top is not None and abs(item_top - previous_item_top) < 1.5  # noqa: PLR2004
            if same_row:
                pass
            elif previous_item_bottom is None:
                first_gap.append(item_top - title_bottom)
            else:
                gaps_item.append(item_top - previous_item_bottom)
            for row in rows:
                # Une étiquette au-dessus de la valeur : la rangée commence à l'étiquette.
                label = labels.get(id(row["elems"][0]))
                if label is not None and not label[1]:
                    row["top"] = min(row["top"], label[0]["top"])
            if rows and not same_row:
                item_top = rows[0]["top"]
                if previous_item_bottom is None:
                    first_gap[-1] = item_top - title_bottom
                else:
                    gaps_item[-1] = item_top - previous_item_bottom
            prev = item_top
            prev_top = -1e9
            prev_role: str | None = None
            for row in rows:
                key = "+".join(e["role"] for e in row["elems"])
                slot = slots.setdefault(
                    key,
                    {
                        "roles": [e["role"] for e in row["elems"]],
                        "margins": [],
                        "repeat": [],
                        "dx": [],
                        "gaps": [],
                        "align": [],
                    },
                )
                if prev_role == key:
                    if row["top"] > prev_top + 0.5:  # pas la même rangée d'une grille
                        slot["repeat"].append(row["top"] - prev)
                elif prev_role is not None:
                    slot["margins"].append(row["top"] - prev)
                slot.setdefault("xs", []).append(row["elems"][0]["x"])
                label = labels.get(id(row["elems"][0]))
                if label is not None:
                    slot.setdefault(
                        "label", self.label_spec(label[0], row["elems"][0], label[1], origin_x)
                    )
                slot["dx"].append(
                    (label[0]["x"] if label is not None and label[1] else row["elems"][0]["x"])
                    - origin_x
                )
                if len(row["elems"]) > 1:
                    a, b = row["elems"][0], row["elems"][1]
                    slot["gaps"].append(b["x"] - a["right"])
                    slot.setdefault("rights", []).append(b["right"])
                    sep = self.row_separator(a, b)
                    if sep:
                        slot["separator"] = sep
                for e in row["elems"]:
                    if len(e["lines"]) > 1:
                        slot["align"].append(self.alignment(e["lines"]))
                prev = row["bottom"]
                prev_top = row["top"]
                prev_role = key
            item_bottom = max(r["bottom"] for r in all_rows)
            previous_item_bottom = (
                max(previous_item_bottom, item_bottom)
                if same_row and previous_item_bottom is not None
                else item_bottom
            )
            previous_item_top = item_top
        spec["first_gap_mm"] = round(med(first_gap), 2)
        # Deux éléments côte à côte (deux références en colonnes) donneraient un
        # écart négatif : le gabarit les empile, à l'écart nul.
        spec["item_gap_mm"] = round(max(med(gaps_item, 0.0), 0.0), 2)
        spec["slots"] = [
            {
                "key": k,
                "roles": v["roles"],
                "margin_top_mm": round(med(v["margins"], 0.0), 2),
                "repeat_margin_mm": round(med(v["repeat"], 0.0), 2) if v["repeat"] else None,
                # Une grille : la première colonne ; sinon la référence des mesures.
                "dx_mm": round(min(v["dx"]) if self.grid_spec(v) else reference(v["dx"]), 2),
                "gap_mm": round(med(v["gaps"]), 2) if v["gaps"] else None,
                "align": majority_alignment(v["align"]),
                "separator": v.get("separator"),
                "label": v.get("label"),
                # Le second champ d'une ligne composée au fer à droite (dates à la
                # marge) : mêmes droites, écarts qui varient.
                "flush_right": bool(
                    len(v.get("rights", [])) >= 2  # noqa: PLR2004
                    and max(v["rights"]) - min(v["rights"]) < 1.0
                    and max(v["gaps"]) - min(v["gaps"]) > 2.0  # noqa: PLR2004
                ),
                **self.grid_spec(v),
            }
            for k, v in slots.items()
        ]
        spec["slot_order"] = list(slots.keys())
        spec["side"] = self.side_spec(side_groups, groups, col_x) if side_elements else None
        spec["marker"] = self.marker_spec(markers, lines)
        spec["icons"] = self.icons_for(lines) if kind == "contact" else {}
        left_bound = col_x - 8
        if spec["side"] is not None:
            left_bound = min(left_bound, col_x + spec["side"]["dx_mm"] - 3)
        spec["decorations"] = self.anchored_decorations(s, all_groups, col_x, left_bound=left_bound)
        if kind in LIST_KINDS:
            # Les barres de niveau d'une liste (langues) sont celles des entrées
            # de l'exemple : une personne a d'autres langues, d'autres niveaux.
            for d in spec["decorations"]:
                if (
                    d["kind"] in ("rect", "curve")
                    and d["h_mm"] <= LEVEL_BAR_MAX_H_MM
                    and d["w_mm"] >= LEVEL_BAR_MIN_W_MM
                ):
                    d["sample_only"] = True
        self.fill_sample(kind, all_groups, sample, styles)
        return spec

    def word_spacing(self, elements: list[Json], st: Json) -> float | None:
        """Le blanc ajouté à chaque espace d'un paragraphe justifié, lu sur sa dernière ligne.

        La dernière ligne n'est pas justifiée : sa largeur, moins celle que la
        police servie lui donne, répartie sur ses espaces, dit l'espacement des
        mots du dessinateur (Canva : 0,5 mm sur un corps de 8 pt). Sans lui, le
        navigateur remonte un mot que la source avait replié.
        """
        extras: list[float] = []
        for e in elements:
            lines = e["lines"]
            if len(lines) < 2 or self.alignment(lines) != "justify":  # noqa: PLR2004
                continue
            if any(family_match_key(ln.raw.get("font") or "") in self.substituted for ln in lines):
                # Une police substituée n'a pas les largeurs de la source : le
                # blanc mesuré mêlerait l'espacement des mots et l'écart de chasse.
                continue
            last = lines[-1]
            spaces = last.text.strip().count(" ")
            if spaces < 3:  # noqa: PLR2004
                continue
            extras.append((last.w - self.fonts.width_mm(last.text, st)) / spaces)
        if not extras:
            return None
        extra = med(extras)
        return round(extra, 2) if WORD_SPACING_MIN_MM <= extra <= WORD_SPACING_MAX_MM else None

    @staticmethod
    def item_sizes(kind: str, elements: list[Json], styles: dict[str, Json]) -> dict[str, float]:
        """Les corps d'un élément de l'exemple qui s'écartent de leur style (> 10 %).

        Un modèle Canva dont la troisième expérience est en 5,5 pt quand les
        autres sont en 7,9 pt : l'exemple se rend à son corps, le gabarit
        d'une personne garde celui du style.
        """
        sizes: dict[str, float] = {}
        by_role: dict[str, list[float]] = defaultdict(list)
        for e in elements:
            by_role[e["role"]].extend(float(ln.raw["size"]) for ln in e["lines"])
        for role, values in by_role.items():
            st = styles.get(f"{kind}.{role}")
            if st is None:
                continue
            size = med(values)
            if abs(size - float(st["size_pt"])) > SIZE_DEPARTURE * float(st["size_pt"]):
                sizes[role] = round(size, 2)
        return sizes

    @staticmethod
    def item_grid(groups: dict[int, list[Json]]) -> Json:
        """Des éléments côte à côte : le nombre de colonnes, leur pas, l'origine de chaque élément.

        Deux éléments d'une même rangée ont le même haut (à 1,5 mm) et des
        gauches écartées d'au moins 10 mm. Rend `{}` sans rangée partagée.
        """
        tops = {no: min(e["top"] for e in es) for no, es in groups.items() if es}
        lefts = {no: min(e["x"] for e in es) for no, es in groups.items() if es}
        rows: list[list[int]] = []
        for no in sorted(tops, key=lambda n: (tops[n], lefts[n])):
            row = next((r for r in rows if abs(tops[r[0]] - tops[no]) < 1.5), None)  # noqa: PLR2004
            if row is not None and lefts[no] - lefts[row[-1]] >= 10.0:  # noqa: PLR2004
                row.append(no)
            else:
                rows.append([no])
        columns = max((len(r) for r in rows), default=0)
        if columns < 2:  # noqa: PLR2004
            return {}
        x0 = min(lefts.values())
        pitches = [lefts[b] - lefts[a] for r in rows for a, b in itertools.pairwise(r)]
        return {
            "item_columns": columns,
            "item_pitch_mm": round(med(pitches), 2),
            "origins": {no: left - x0 for no, left in lefts.items()},
        }

    @staticmethod
    def grid_spec(slot: Json) -> Json:
        """Une liste en colonnes : le nombre de colonnes et leur pas, d'après les abscisses des entrées."""
        if not slot["roles"] or slot["roles"][0] not in LIST_ROLES:
            return {}
        columns: list[float] = []
        for x in sorted(slot.get("xs", [])):
            if not columns or x - columns[-1] > 2.0:  # noqa: PLR2004
                columns.append(x)
        if len(columns) < 2:  # noqa: PLR2004
            return {}
        return {
            "columns": len(columns),
            "column_pitch_mm": round(med([b - a for a, b in itertools.pairwise(columns)]), 2),
        }

    @staticmethod
    def attach_labels(
        label_elements: list[Json], elements: list[Json]
    ) -> dict[int, tuple[Json, bool]]:
        """Chaque étiquette → le champ qu'elle annonce : sur sa ligne (en ligne) ou juste au-dessous.

        Rend `{id(élément): (étiquette, en_ligne)}`.
        """
        out: dict[int, tuple[Json, bool]] = {}
        for lb in label_elements:
            below = [
                e
                for e in elements
                if e["item"] == lb["item"] and e["top"] >= lb["top"] - 1.2 and id(e) not in out
            ]
            if not below:
                continue
            target = min(below, key=lambda e: (round(e["top"] - lb["top"], 1), e["x"]))
            out[id(target)] = (lb, abs(target["top"] - lb["top"]) < 1.2)  # noqa: PLR2004
        return out

    @staticmethod
    def label_spec(label: Json, target: Json, inline: bool, col_x: float) -> Json:
        """L'étiquette d'un emplacement : son texte, en ligne (écart) ou au-dessus (pas)."""
        text = " ".join(ln.text for ln in label["lines"])
        if inline:
            return {
                "text": text,
                "inline": True,
                "gap_mm": round(target["x"] - label["right"], 2),
                "dx_mm": round(label["x"] - col_x, 2),
            }
        return {
            "text": text,
            "inline": False,
            "pitch_mm": round(target["top"] - label["top"], 2),
            "dx_mm": round(label["x"] - col_x, 2),
        }

    def side_spec(
        self, side_groups: dict[int, list[Json]], groups: dict[int, list[Json]], col_x: float
    ) -> Json:
        """La colonne latérale d'une frise : où elle est, son alignement, ses emplacements.

        `dx_mm` est négatif (à gauche du contenu) ; `top_offset_mm` est l'écart
        entre le haut de la première ligne latérale et celui de la première
        ligne du contenu d'un même élément ; les marges entre emplacements se
        mesurent comme celles du contenu.
        """
        all_side = [e for es in side_groups.values() for e in es]
        x0 = min(e["x"] for e in all_side)
        x1 = max(e["right"] for e in all_side)
        rights = [e["right"] for e in all_side]
        lefts = [e["x"] for e in all_side]
        align = "left"
        if max(rights) - min(rights) < 1.0 and max(lefts) - min(lefts) >= 1.0:
            align = "right"
        slots: dict[str, Json] = {}
        offsets: list[float] = []
        for no, es in side_groups.items():
            rows = self.rows(es)
            main = groups.get(no) or []
            main_top = min(e["top"] for e in main) if main else rows[0]["top"]
            offsets.append(rows[0]["top"] - main_top)
            prev: float | None = None
            prev_key: str | None = None
            for row in rows:
                key = "+".join(e["role"] for e in row["elems"])
                slot = slots.setdefault(
                    key,
                    {
                        "key": key,
                        "roles": [e["role"] for e in row["elems"]],
                        "margins": [],
                        "repeat": [],
                    },
                )
                if prev is not None and prev_key == key:
                    slot["repeat"].append(row["top"] - prev)
                elif prev is not None:
                    slot["margins"].append(row["top"] - prev)
                prev = row["bottom"]
                prev_key = key
        main_xs = [e["x"] for es in groups.values() for e in es]
        return {
            "dx_mm": round(x0 - col_x, 2),
            "w_mm": round(x1 - x0 + 0.5, 2),
            "align": align,
            "top_offset_mm": round(med(offsets), 2),
            # Où commence le contenu, depuis la colonne (le titre de la rubrique
            # peut être plus à gauche que la colonne latérale).
            "main_dx_mm": round((min(main_xs) if main_xs else x1 + 2.0) - col_x, 2),
            "slots": [
                {
                    "key": v["key"],
                    "roles": v["roles"],
                    "margin_top_mm": round(med(v["margins"], 0.0), 2),
                    "repeat_margin_mm": round(med(v["repeat"], 0.0), 2) if v["repeat"] else None,
                }
                for v in slots.values()
            ],
            "slot_order": list(slots),
        }

    def row_separator(self, a: Json, b: Json) -> Json | None:
        """Un filet vertical entre deux champs d'une même ligne (« Entreprise | 2016 »)."""
        for i, d in enumerate(self.drawings):
            if d["kind"] != "vline" or not (a["right"] < d["x"] < b["x"]):
                continue
            if d["y"] > a["bottom"] + 1 or d["y"] + d["h"] < a["top"] - 1:
                continue
            self.used.add(i)
            return {
                "stroke": d.get("stroke") or d.get("fill"),
                "stroke_pt": d.get("stroke_width_pt") or 0.75,
                "h_mm": round(d["h"], 2),
                "left_mm": round(d["x"] - a["right"], 2),
                "right_mm": round(b["x"] - d["x"], 2),
                "dy_mm": round(d["y"] - a["top"], 2),
            }
        return None

    def alignment(self, lines: list[Line]) -> str:
        """À droite ou centré si les lignes le partagent (gauches dispersées) ; justifié si
        les lignes non finales touchent toutes la même marge droite ; à gauche sinon."""
        right = max(ln.right for ln in lines)
        lefts = [ln.x for ln in lines]
        if max(lefts) - min(lefts) >= 2.0:  # noqa: PLR2004
            rights = [ln.right for ln in lines]
            if max(rights) - min(rights) < 0.8:  # noqa: PLR2004
                return "right"
            centers = [ln.x + ln.w / 2 for ln in lines]
            if max(centers) - min(centers) < 1.0:
                return "center"
            return "left"
        if len(lines) == 2:  # noqa: PLR2004
            return (
                "justify"
                if lines[1].right < right - 3 and self._col_right - lines[0].right < 0.8  # noqa: PLR2004
                else "left"
            )
        full = [ln for ln in lines[:-1] if right - ln.right < 0.8]  # noqa: PLR2004
        return "justify" if len(full) >= max(1, (len(lines) - 1) * 0.6) else "left"

    def markers_for(self, lines: list[Line]) -> dict[str, Json]:
        """Petites formes pleines à gauche d'une ligne, à sa hauteur : les puces."""
        found: dict[str, Json] = {}
        for i, d in enumerate(self.drawings):
            if i in self.used or not d.get("fill") or max(d["w"], d["h"]) > 1.8:  # noqa: PLR2004
                continue
            cy = d["y"] + d["h"] / 2
            for ln in lines:
                if ln.y <= cy <= ln.bottom and 0 < ln.x - (d["x"] + d["w"]) < 6:  # noqa: PLR2004
                    found[ln.id] = {"i": i, **d}
                    self.used.add(i)
                    break
        return found

    def marker_spec(self, markers: dict[str, Json], lines: list[Line]) -> Json | None:
        """La puce d'une rubrique : forme vue, taille, couleur, décalage."""
        if not markers:
            return None
        by_id = {ln.id: ln for ln in lines}
        ms = list(markers.items())
        d = ms[0][1]
        return {
            "shape": "circle" if self.doc.is_round((d["x"], d["y"], d["w"], d["h"])) else "square",
            "size_mm": round(med([m["w"] for _, m in ms]), 2),
            "color": d["fill"],
            "dx_mm": round(med([m["x"] - by_id[k].x for k, m in ms]), 2),
            "dy_mm": round(med([m["y"] + m["h"] / 2 - by_id[k].y for k, m in ms]), 2),
            # Les rôles que la puce marque : une liste, ou la première ligne d'un
            # élément (une pastille devant l'année d'une expérience).
            "roles": sorted({by_id[k].role for k in markers}),
        }

    def elements(self, lines: list[Line], markers: dict[str, Json]) -> list[Json]:
        """Lignes consécutives d'un même rôle et d'un même élément → un élément (paragraphe, puce)."""
        out: list[Json] = []
        for ln in sorted(lines, key=lambda line: (line.item, line.y, line.x)):
            # La suite d'un paragraphe partage sa marge gauche, ou sa marge
            # droite (au fer à droite), ou son axe (centré).
            last = next(
                (
                    e
                    for e in reversed(out)
                    if e["item"] == ln.item
                    and e["role"] == ln.role
                    and (
                        abs(e["x"] - ln.x) < 1.5  # noqa: PLR2004
                        or abs(e["lines"][-1].right - ln.right) < 1.5  # noqa: PLR2004
                        or abs(e["lines"][-1].x + e["lines"][-1].w / 2 - (ln.x + ln.w / 2)) < 1.5  # noqa: PLR2004
                    )
                ),
                None,
            )
            cont = (
                last is not None
                and 0 < ln.y - last["lines"][-1].y < ln.h * 2.0
                and ln.id not in markers
                # Une date, une étiquette : jamais la suite de la précédente.
                and ln.role not in ("item_dates", "label")
                # Une liste sans puces (compétences, langues, intérêts) : une
                # ligne = une entrée ; avec des puces, la ligne sans puce continue.
                and not (ln.role in LIST_ROLES and not markers)
            )
            if cont and last is not None:
                last["lines"].append(ln)
                last["bottom"] = ln.bottom
                last["right"] = max(last["right"], ln.right)
            else:
                out.append(
                    {
                        "role": ln.role,
                        "item": ln.item,
                        "x": ln.x,
                        "top": ln.y,
                        "bottom": ln.bottom,
                        "right": ln.right,
                        "lines": [ln],
                    }
                )
        for e in out:
            st = self.style_of(e["lines"])
            pitch = st["pitch_mm"] or st["box_h_mm"]
            e["bottom"] = e["lines"][0].y + pitch * (len(e["lines"]) - 1) + e["lines"][-1].h
        return out

    @staticmethod
    def rows(elems: list[Json]) -> list[Json]:
        """Les éléments d'un item rangés en lignes visuelles (même haut à 1,2 mm près)."""
        rows: list[Json] = []
        for e in sorted(elems, key=lambda e: (e["top"], e["x"])):
            row = next((r for r in rows if abs(r["top"] - e["top"]) < 1.2), None)  # noqa: PLR2004
            if row:
                row["elems"].append(e)
                row["elems"].sort(key=lambda x: x["x"])
                row["bottom"] = max(row["bottom"], e["bottom"])
            else:
                rows.append({"top": e["top"], "bottom": e["bottom"], "elems": [e]})
        return rows

    def anchored_decorations(  # noqa: PLR0912 - chaque forme est rangée selon ce qu'elle borde
        self,
        s: Json,
        groups: dict[int, list[Json]],
        col_x: float,
        *,
        left_bound: float | None = None,
    ) -> list[Json]:
        """Filets et formes de la rubrique : ancrés à l'élément qu'ils bordent, sinon à la rubrique.

        `left_bound` : jusqu'où à gauche de la colonne une forme appartient
        encore à la rubrique (la colonne latérale d'une frise l'élargit).
        """
        out: list[Json] = []
        top = (
            s["title"][0].y
            if s["title"]
            else min((e["top"] for es in groups.values() for e in es), default=s["top"])
        )
        bottom = s["bottom"]
        left = col_x - 8 if left_bound is None else left_bound
        items = [
            (
                no,
                min(e["top"] for e in elems),
                max(e["bottom"] for e in elems),
                min(e["x"] for e in elems),
            )
            for no, elems in groups.items()
            if no != 0
        ]
        first_decorated: int | None = None
        # Frise d'élément : le dernier élément a souvent un trait plus court
        # (il ne rejoint pas d'élément suivant) ; on garde son retrait.
        last_insets: dict[int, float] = {}
        for i, d in sorted(enumerate(self.drawings), key=lambda t: t[1]["y"]):
            if i in self.used:
                continue
            cx = d["x"] + d["w"] / 2
            near_column = left <= cx <= col_x + 30 or left <= d["x"] <= col_x + 5
            if not near_column and not any(left <= cx <= e[3] + 3 for e in items):
                continue
            if not (top - 3 <= d["y"] and d["y"] + d["h"] <= bottom + 14):
                continue
            kind_seen = d["kind"]
            if d["kind"] in ("rect", "curve") and d.get("fill") and max(d["w"], d["h"]) < 4:  # noqa: PLR2004
                kind_seen = (
                    "curve" if self.doc.is_round((d["x"], d["y"], d["w"], d["h"])) else "rect"
                )
            entry: Json = {
                "kind": kind_seen,
                "w_mm": round(d["w"], 2),
                "h_mm": round(d["h"], 2),
                "fill": d.get("fill"),
                "stroke": d.get("stroke"),
                "stroke_pt": d.get("stroke_width_pt"),
                "dx_mm": round(d["x"] - col_x, 2),
            }
            owner = next((it for it in items if it[1] - 4 <= d["y"] <= it[2] + 1), None)
            if owner and d["kind"] != "hline":
                h = owner[2] - owner[1]
                if first_decorated is None:
                    first_decorated = owner[0]
                if owner[0] != first_decorated:
                    # Un seul jeu de décor d'élément : celui du premier élément décoré.
                    if d["kind"] == "vline" and d["h"] > 0.45 * h:
                        last_insets[owner[0]] = round(owner[2] - (d["y"] + d["h"]), 2)
                    self.used.add(i)
                    continue
                entry.update(anchor="item", dy_mm=round(d["y"] - owner[1], 2))
                cy = d["y"] + d["h"] / 2
                ref = next(
                    (e for e in groups[owner[0]] if abs((e["top"] + e["bottom"]) / 2 - cy) < 1.0),
                    None,
                )
                if ref is not None and d["kind"] != "vline":
                    entry.update(
                        attach="row",
                        ref_role=ref["role"],
                        center_offset_mm=round(cy - (ref["top"] + ref["bottom"]) / 2, 2),
                    )
                if d["kind"] == "vline" and d["h"] > 0.45 * h:
                    inset = owner[2] - (d["y"] + d["h"])
                    entry.update(
                        stretch=True, bottom_inset_mm=round(inset, 2), connector=inset < -1.0
                    )
            else:
                entry.update(anchor="section", dy_mm=round(d["y"] - top, 2))
                if d["kind"] == "vline" and d["h"] > 0.45 * (bottom - top):
                    entry.update(stretch=True, bottom_inset_mm=round(bottom - (d["y"] + d["h"]), 2))
                if d["y"] > bottom:
                    entry.update(anchor="after", dy_mm=round(d["y"] - bottom, 2))
            self.used.add(i)
            out.append(entry)
        last_item = max((it[0] for it in items), default=None)
        for entry in out:
            if entry.get("connector") and last_item in last_insets:
                entry["last_bottom_inset_mm"] = last_insets[last_item]
        return out

    def icons_for(self, lines: list[Line]) -> dict[str, Json]:
        """Pictogrammes à gauche des lignes de contact : rastérisés depuis la source (600 dpi)."""
        icons: dict[str, Json] = {}
        for ln in lines:
            near = [
                (i, d)
                for i, d in enumerate(self.drawings)
                if i not in self.used
                and d["x"] + d["w"] < ln.x
                and ln.x - (d["x"] + d["w"]) < 8  # noqa: PLR2004
                and d["y"] < ln.bottom + 1
                and d["y"] + d["h"] > ln.y - 1
                and max(d["w"], d["h"]) < 8  # noqa: PLR2004
                and min(d["w"], d["h"]) >= 0.5  # noqa: PLR2004 - un filet n'est pas un pictogramme
            ]
            if not near:
                continue
            x0 = min(d["x"] for _, d in near)
            y0 = min(d["y"] for _, d in near)
            x1 = max(d["x"] + d["w"] for _, d in near)
            y1 = max(d["y"] + d["h"] for _, d in near)
            name = f"icon_{ln.role}.png"
            self.assets[name] = self.doc.render_png(dpi=600, box_mm=(x0, y0, x1 - x0, y1 - y0))
            icons[ln.role] = {
                "file": name,
                "w_mm": round(x1 - x0, 2),
                "h_mm": round(y1 - y0, 2),
                "dx_mm": round(x0 - ln.x, 2),
                "dy_mm": round(y0 - ln.y, 2),
            }
            for i, _ in near:
                self.used.add(i)
        return icons

    # ------------------------------------------------------------------ en-tête et page
    def header_spec(
        self, lines: list[Line], first_top: float, styles: dict[str, Json], sample: Json
    ) -> Json:
        """L'en-tête : éléments en position absolue, photo, ornements ; poste visé ajouté s'il manque."""
        elements: list[Json] = []
        for ln in sorted(lines, key=lambda line: line.y):
            key = f"header.{ln.role}"
            styles.setdefault(key, self.style_of([ln]))
            elements.append(
                {
                    "field": ln.role,
                    "style": key,
                    "x_mm": round(ln.x, 2),
                    "y_mm": round(ln.y, 2),
                    "w_mm": round(ln.w, 2),
                }
            )
            text = ln.text
            if ln.role in ("first_name", "last_name"):
                sample["identity"][ln.role] = text.title()
            elif ln.role == "headline":
                sample["identity"]["headline"] = text
            elif ln.role == "full_name":
                parts = text.split()
                sample["identity"].update(
                    first_name=parts[0].title(), last_name=" ".join(parts[1:]).title()
                )
        contact_lines = [ln for ln in lines if ln.role in ("phone", "email", "address", "website")]
        icons = self.icons_for(contact_lines) if contact_lines else {}
        for e in elements:
            if e["field"] in icons:
                e["icon"] = icons[e["field"]]
        for ln in contact_lines:
            sample["contact"][{"address": "city"}.get(ln.role, ln.role)] = ln.text
        fields = {e["field"] for e in elements}
        if "headline" not in fields:
            elements.append(self.synthesize_headline(elements, styles, first_top))
        photo = self.photo_spec()
        decor = []
        for i, d in enumerate(self.drawings):
            if i in self.used or d["y"] > first_top - 2:
                continue
            self.used.add(i)
            decor.append(
                {
                    "kind": d["kind"],
                    "x_mm": round(d["x"], 2),
                    "y_mm": round(d["y"], 2),
                    "w_mm": round(d["w"], 2),
                    "h_mm": round(d["h"], 2),
                    "fill": d.get("fill"),
                    "stroke": d.get("stroke"),
                    "stroke_pt": d.get("stroke_width_pt"),
                }
            )
        return {"elements": elements, "photo": photo, "decorations": decor}

    def synthesize_headline(
        self, elements: list[Json], styles: dict[str, Json], first_top: float
    ) -> Json:
        """Le modèle n'a pas de poste visé : on l'ajoute dans sa grammaire (`domain/headline.py`).

        Placé sous la dernière ligne du nom, à l'écart que le nom laisse sous
        lui ; centré si le nom l'est ; à côté d'un filet vertical d'en-tête
        qu'il chevaucherait, centré sur lui (Meridian).
        """
        name = [
            e for e in elements if e["field"] in ("first_name", "last_name", "full_name")
        ] or elements[:1]
        if not name:
            # Aucun texte d'en-tête : le poste visé ouvre la page, au-dessus
            # de la première rubrique, au style des titres.
            title = styles["section_title"]
            x = min((ln.x for ln in self.lines if ln.role == "section_title"), default=15.0)
            styles["header.headline"] = synthesize_headline_style(title, title)
            y = max(5.0, first_top - styles["header.headline"]["box_h_mm"] - 6.0)
            return {
                "field": "headline",
                "style": "header.headline",
                "x_mm": round(x, 2),
                "y_mm": round(y, 2),
                "w_mm": None,
                "synthesized": True,
            }
        last = max(name, key=lambda e: e["y_mm"])
        name_style = styles[last["style"]]
        body = med([ln.raw["size"] for ln in self.lines if ln.section not in ("header",)], 10.0)
        family, weight = name_style["family"], 500 if int(name_style["weight"]) >= 600 else 400  # noqa: PLR2004
        style = synthesize_headline_style(
            name_style,
            styles["section_title"],
            body_size_pt=body,
            box_ratio=self.fonts.box_ratio(family, weight, False),
        )
        styles["header.headline"] = style
        h = style["box_h_mm"]
        gap = max(2.0, 0.35 * name_style["box_h_mm"])
        under_name = last["y_mm"] + name_style["box_h_mm"] + gap
        y = min(under_name, first_top - h - 4)
        x = last["x_mm"]
        out: Json = {
            "field": "headline",
            "style": "header.headline",
            "x_mm": round(x, 2),
            "w_mm": None,
            "synthesized": True,
        }
        if y < under_name - 1.0 and y < last["y_mm"] + name_style["box_h_mm"]:
            # Pas de place entre le nom et la première bande (le profil suit le
            # nom) : le poste visé ouvre le flux, et pousse la première bande.
            out.update(flow=True, y_mm=round(first_top, 2), gap_mm=round(gap, 2))
            return out
        center = last["x_mm"] + (last.get("w_mm") or 0) / 2
        if last.get("w_mm") and x > 20 and abs(center - self.pw / 2) < 4:  # noqa: PLR2004
            out.update(align="center", cx_mm=round(center, 2))
        for d in self.drawings:
            if (
                d["kind"] == "vline"
                and d["y"] < y + h
                and d["y"] + d["h"] > y
                and x - 3 <= d["x"] <= x + 40
            ):
                out["x_mm"] = round(d["x"] + 3.5, 2)
                y = d["y"] + (d["h"] - h) / 2
                break
        out["y_mm"] = round(y, 2)
        return out

    def photo_spec(self, img: Json | None = None) -> Json | None:
        """Le cadre de la photo (détourage réel), sa forme et la position de l'image d'exemple.

        `img` : l'image qui est la photo ; à défaut la plus grande de la page.
        """
        if img is None:
            if not self.page.images:
                return None
            img = max(self.page.images, key=lambda i: i["w"] * i["h"])
        frame = img.get("clip") or {k: img[k] for k in ("x", "y", "w", "h")}
        if frame["w"] > self.pw * 0.9:
            frame = {k: img[k] for k in ("x", "y", "w", "h")}
        shape = self.meta.get("photo_shape") or "rect"
        fx, fy, fw, fh = frame["x"], frame["y"], frame["w"], frame["h"]
        if shape == "circle":
            d = min(fw, fh)
            fx, fy, fw, fh = fx + (fw - d) / 2, fy + (fh - d) / 2, d, d
        ox = 50.0 if img["w"] <= fw + 0.1 else round((fx - img["x"]) / (img["w"] - fw) * 100, 1)
        oy = 50.0 if img["h"] <= fh + 0.1 else round((fy - img["y"]) / (img["h"] - fh) * 100, 1)
        self.assets["sample_photo.png"] = img["png"]
        return {
            "x_mm": round(fx, 2),
            "y_mm": round(fy, 2),
            "w_mm": round(fw, 2),
            "h_mm": round(fh, 2),
            "shape": shape,
            "sample_object_position": f"{ox}% {oy}%",
            # L'image d'exemple à son échelle réelle dans le cadre (Canva zoome
            # une grande photo dans un petit cadre) ; la photo d'une personne,
            # elle, couvre le cadre, centrée.
            "sample_image": {
                "x_mm": round(img["x"] - fx, 2),
                "y_mm": round(img["y"] - fy, 2),
                "w_mm": round(img["w"], 2),
                "h_mm": round(img["h"], 2),
            },
            "sample_file": "sample_photo.png",
        }

    def page_decorations(self, bands: list[Json]) -> list[Json]:
        """Ce qui reste : filets pleine largeur entre bandes (ancrés à la bande), le reste fixe."""
        out: list[Json] = []
        for i, d in enumerate(self.drawings):
            if i in self.used:
                continue
            self.used.add(i)
            entry: Json = {
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
            for b_i, b in enumerate(bands):
                nxt = bands[b_i + 1]["y_sample"] if b_i + 1 < len(bands) else self.ph
                if d["kind"] == "hline" and b["bottom_sample"] - 1 <= d["y"] <= nxt:
                    entry.update(
                        anchor="band", band=b_i, dy_mm=round(d["y"] - b["bottom_sample"], 2)
                    )
                    break
            self.rasterize_path(i, d, entry)
            out.append(entry)
        return out

    def rasterize_path(self, index: int, d: Json, entry: Json) -> None:
        """Un tracé complexe et grand (le cadre d'un QR code, une vague) se dessine en image.

        Les moteurs ne tracent que rectangles, ronds et filets : un tracé carré
        mais non rond devenait un disque plein (mesuré sur cv3). Rastérisé
        depuis la source, à condition qu'aucun texte ne passe dessous (sinon
        le texte y serait deux fois). S'il entoure une image (le QR code de
        l'exemple), il ne vaut que pour l'exemple.
        """
        if d["kind"] != "curve" or max(d["w"], d["h"]) < RASTER_PATH_MM:
            return
        if self.doc.is_round((d["x"], d["y"], d["w"], d["h"])):
            return
        if any(overlaps(d, ln.raw) for ln in self.lines):
            return
        name = f"shape_{index}.png"
        self.assets[name] = self.doc.render_png(dpi=300, box_mm=(d["x"], d["y"], d["w"], d["h"]))
        entry["asset"] = name
        if any(overlaps(d, im) for im in self.page.images):
            entry["sample_only"] = True

    # ------------------------------------------------------------------ contenu d'exemple
    def fill_sample(  # noqa: PLR0912 - une branche par genre de rubrique
        self,
        kind: str,
        groups: dict[int, list[Json]],
        sample: Json,
        styles: dict[str, Json] | None = None,
    ) -> None:
        """Le contenu d'exemple de la rubrique, au format du CV."""

        def joined(e: Json) -> str:
            # Un intitulé écrit sur deux lignes (« Licence… » / « Université… »)
            # garde son retour à la ligne : l'exemple le rend tel quel, quelle
            # que soit la boîte ; un paragraphe se replie dans la sienne.
            sep = "\n" if e["role"] in KEEP_BREAKS else " "
            return sep.join(ln.text for ln in e["lines"])

        def text(es: list[Json], role: str) -> list[str]:
            return [joined(e) for e in es if e["role"] == role]

        if kind == "contact":
            for es in groups.values():
                for e in es:
                    field = {
                        "phone": "phone",
                        "email": "email",
                        "address": "city",
                        "website": "website",
                    }.get(e["role"])
                    if field:
                        sample["contact"][field] = joined(e)
        elif kind == "summary":
            sample["summary"] = " ".join(
                t for es in groups.values() for t in text(es, "summary") + text(es, "item_text")
            )
        elif is_item_kind(kind):
            for no in sorted(groups):
                es = groups[no]
                item: Json = {
                    "title_raw": " ".join(text(es, "item_title")),
                    "subtitle_raw": " ".join(text(es, "item_subtitle")),
                    "dates_raw": " ".join(text(es, "item_dates")),
                    "location": " ".join(text(es, "item_location")) or None,
                    "bullets": text(es, "bullet"),
                    "description": " ".join(text(es, "item_text") + text(es, "other")) or None,
                }
                # Les coordonnées d'un élément (une référence : téléphone, courriel)
                # restent dans l'exemple, à leur rôle.
                extra = {r: " ".join(text(es, r)) for r in CONTACT_ROLES if text(es, r)}
                if extra:
                    item["extra"] = extra
                sizes = self.item_sizes(kind, es, styles or {})
                if sizes:
                    item["sizes"] = sizes
                if kind.startswith(OTHER_PREFIX):
                    # Une rubrique libre garde son titre et ses éléments tels quels.
                    others = sample.setdefault("others", {})
                    others.setdefault(kind, {"title": "", "items": []})["items"].append(item)
                else:
                    sample.setdefault(kind, []).append(item)
        elif kind in ("skills", "languages", "interests"):
            role = {"skills": "skill", "languages": "language", "interests": "interest"}[kind]
            for es in groups.values():
                current = None
                for e in sorted(es, key=lambda e: e["top"]):
                    if e["role"] == "subsection_title":
                        current = joined(e)
                    elif e["role"] in (role, "bullet", "item_text"):
                        sample[kind].append({"name": joined(e), "category": current})
