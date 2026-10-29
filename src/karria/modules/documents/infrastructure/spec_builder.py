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
from collections import defaultdict
from typing import Any, Final

from karria.modules.documents.domain.headline import synthesize_headline_style
from karria.modules.documents.infrastructure.fonts import MM_PER_PT, FontSet
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument, PdfPage

Json = dict[str, Any]

MM: Final = 72 / 25.4
SPEC_VERSION: Final = 1
ITEM_SECTIONS: Final = frozenset({"experiences", "educations", "projects", "references"})
LIST_ROLES: Final = frozenset({"skill", "language", "interest"})


def med(values: list[float] | list[float | None], default: float = 0.0) -> float:
    """Médiane des valeurs connues."""
    known = [v for v in values if v is not None]
    return float(statistics.median(known)) if known else default


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
    ) -> None:
        """
        Args:
            served: Pour chaque police du PDF (nom de base), la police servie
                `(famille, graisse, italique)`.
        """
        self.doc = doc
        self.page = page
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
        """Le gabarit d'une rubrique : titre, emplacements, puces, décor."""
        kind = s["kind"]
        title = s["title"]
        self._col_right = max([ln.right for ln in s["lines"]] + [t.right for t in title])
        styles.setdefault("section_title", self.style_of(title))
        lines = sorted(s["lines"], key=lambda ln: (ln.y, ln.x))
        title_bottom = title[-1].y + (styles["section_title"]["pitch_mm"] or title[-1].h)
        markers = self.markers_for(lines)
        elements = self.elements(lines, markers)
        bottom = max([title_bottom] + [e["bottom"] for e in elements])
        s["bottom"] = bottom
        spec: Json = {
            "kind": kind,
            "title_sample": " ".join(t.text for t in title),
            "margin_top_mm": round(title[0].y - prev_bottom, 2),
            "title_dx_mm": round(title[0].x - col_x, 2),
            "title_w_mm": round(max(t.w for t in title) + 1.5, 2) if len(title) > 1 else None,
            # Un titre sur plusieurs lignes garde son interligne mesuré.
            "title_pitch_mm": round(med([b.y - a.y for a, b in itertools.pairwise(title)]), 3)
            if len(title) > 1
            else None,
            "title_sep": next((ln.text for ln in lines if ln.role == "item_title"), ""),
        }
        groups: dict[int, list[Json]] = defaultdict(list)
        for e in elements:
            groups[e["item"] if kind in ITEM_SECTIONS else 0].append(e)
        by_role: dict[str, list[Json]] = defaultdict(list)
        for e in elements:
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
            styles[skey] = st
        slots: dict[str, Json] = {}
        gaps_item: list[float] = []
        first_gap: list[float] = []
        previous_item_bottom: float | None = None
        for item_no in sorted(groups):
            elems = groups[item_no]
            rows = self.rows(elems)
            if previous_item_bottom is None:
                first_gap.append(rows[0]["top"] - title_bottom)
            else:
                gaps_item.append(rows[0]["top"] - previous_item_bottom)
            prev = rows[0]["top"]
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
                    slot["repeat"].append(row["top"] - prev)
                elif prev_role is not None:
                    slot["margins"].append(row["top"] - prev)
                slot["dx"].append(row["elems"][0]["x"] - col_x)
                if len(row["elems"]) > 1:
                    a, b = row["elems"][0], row["elems"][1]
                    slot["gaps"].append(b["x"] - a["right"])
                    sep = self.row_separator(a, b)
                    if sep:
                        slot["separator"] = sep
                for e in row["elems"]:
                    if len(e["lines"]) > 1:
                        slot["align"].append(self.alignment(e["lines"]))
                prev = row["bottom"]
                prev_role = key
            previous_item_bottom = prev
        spec["first_gap_mm"] = round(med(first_gap), 2)
        spec["item_gap_mm"] = round(med(gaps_item, 0.0), 2)
        spec["slots"] = [
            {
                "key": k,
                "roles": v["roles"],
                "margin_top_mm": round(med(v["margins"], 0.0), 2),
                "repeat_margin_mm": round(med(v["repeat"], 0.0), 2) if v["repeat"] else None,
                "dx_mm": round(med(v["dx"]), 2),
                "gap_mm": round(med(v["gaps"]), 2) if v["gaps"] else None,
                "align": "justify" if v["align"].count("justify") * 2 > len(v["align"]) else "left",
                "separator": v.get("separator"),
            }
            for k, v in slots.items()
        ]
        spec["slot_order"] = list(slots.keys())
        spec["marker"] = self.marker_spec(markers, lines)
        spec["icons"] = self.icons_for(lines) if kind == "contact" else {}
        spec["decorations"] = self.anchored_decorations(s, groups, col_x)
        self.fill_sample(kind, groups, sample)
        return spec

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
        """Justifié si les lignes non finales touchent toutes la même marge droite."""
        right = max(ln.right for ln in lines)
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
        }

    def elements(self, lines: list[Line], markers: dict[str, Json]) -> list[Json]:
        """Lignes consécutives d'un même rôle et d'un même élément → un élément (paragraphe, puce)."""
        out: list[Json] = []
        for ln in sorted(lines, key=lambda line: (line.item, line.y, line.x)):
            last = next(
                (
                    e
                    for e in reversed(out)
                    if e["item"] == ln.item and e["role"] == ln.role and abs(e["x"] - ln.x) < 1.5  # noqa: PLR2004
                ),
                None,
            )
            cont = (
                last is not None
                and 0 < ln.y - last["lines"][-1].y < ln.h * 2.0
                and ln.id not in markers
                and ln.role != "item_dates"
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
        self, s: Json, groups: dict[int, list[Json]], col_x: float
    ) -> list[Json]:
        """Filets et formes de la rubrique : ancrés à l'élément qu'ils bordent, sinon à la rubrique."""
        out: list[Json] = []
        top = s["title"][0].y
        bottom = s["bottom"]
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
            near_column = col_x - 8 <= cx <= col_x + 30 or col_x - 8 <= d["x"] <= col_x + 5
            if not near_column and not any(col_x - 8 <= cx <= e[3] + 3 for e in items):
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
        y = min(last["y_mm"] + name_style["box_h_mm"] + gap, first_top - h - 4)
        x = last["x_mm"]
        out: Json = {
            "field": "headline",
            "style": "header.headline",
            "x_mm": round(x, 2),
            "w_mm": None,
            "synthesized": True,
        }
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

    def photo_spec(self) -> Json | None:
        """Le cadre de la photo (détourage réel), sa forme et la position de l'image d'exemple."""
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
            out.append(entry)
        return out

    # ------------------------------------------------------------------ contenu d'exemple
    def fill_sample(self, kind: str, groups: dict[int, list[Json]], sample: Json) -> None:
        """Le contenu d'exemple de la rubrique, au format du CV."""

        def joined(e: Json) -> str:
            return " ".join(ln.text for ln in e["lines"])

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
        elif kind in ("experiences", "educations", "projects", "references"):
            for no in sorted(groups):
                es = groups[no]
                item = {
                    "title_raw": " ".join(text(es, "item_title")),
                    "subtitle_raw": " ".join(text(es, "item_subtitle")),
                    "dates_raw": " ".join(text(es, "item_dates")),
                    "location": " ".join(text(es, "item_location")) or None,
                    "bullets": text(es, "bullet"),
                    "description": " ".join(text(es, "item_text")) or None,
                }
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
