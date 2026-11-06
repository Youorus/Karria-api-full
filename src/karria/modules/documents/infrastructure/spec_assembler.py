"""Plan de mise en page + mesures du PDF → spécification canonique (ADR-0037 § 2).

Contrat
-------
- Le **plan** (`layout_planner.LayoutPlan`) dit la structure : quelles lignes
  font l'en-tête, quelles bandes, quelles colonnes, quelles rubriques avec
  quels éléments et quels rôles, quelle colonne latérale, où s'ancre chaque
  décoration. Les **mesures** (`pdf_reader.PdfPage`) disent la géométrie, les
  polices et les couleurs. L'assembleur réunit les deux dans la spécification
  (même forme JSON, même contrat pour les moteurs) ; la mesure d'une rubrique
  reste celle de `spec_builder.SpecBuilder` (gabarit, puces, pictogrammes,
  décor d'élément), dont ceci est une spécialisation.
- Ce que le constructeur d'avant devinait par l'abscisse des titres (bandes,
  colonnes) vient ici du plan ; ce qu'il perdait est rendu : une rubrique
  sans titre, une rubrique libre (`other:<slug>`, titre gardé), une frise à
  colonne latérale, un champ d'en-tête écrit sur plusieurs lignes (recollé
  sans doublon, boîte et interligne mesurés, place exacte de chaque ligne
  pour l'exemple), des coordonnées d'en-tête sans titre, un filet qui court
  le long d'une bande ou de tout le contenu.
- Les bandes commencent là où le PDF les commence : le haut d'une bande est
  celui de sa rubrique la plus haute, l'en-tête s'arrête là.
- Une ligne que le plan n'a pas placée est relevée (`unplaced`) : elle
  bloque la publication, comme une ligne sans rôle avant.
"""

from __future__ import annotations

import itertools
from typing import Any, Final

from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.layout_planner import (
    LayoutPlan,
    PlanDecoration,
    PlanSection,
    check_plan,
    plan_to_labels,
    shape_id,
    slugify,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument, PdfPage
from karria.modules.documents.infrastructure.spec_builder import (
    OTHER_PREFIX,
    SPEC_VERSION,
    Line,
    SpecBuilder,
    med,
)

Json = dict[str, Any]

#: Un filet vertical au-delà de cette hauteur (mm) court le long de son
#: conteneur : il s'étire avec le contenu.
STRETCH_MIN_MM: Final = 20.0
#: Marge ajoutée à la boîte d'un champ d'en-tête écrit sur plusieurs lignes,
#: pour que sa ligne la plus longue tienne sans retour forcé.
BOX_SLACK_MM: Final = 1.5
#: Tolérances d'alignement des éléments d'en-tête (mm).
EDGE_TOLERANCE_MM: Final = 1.0
CENTER_TOLERANCE_MM: Final = 1.5
#: Deux champs centrés l'un sur l'autre doivent différer en largeur pour que
#: le centrage se distingue d'un simple alignement à gauche.
WIDTH_DIFFERENCE_MM: Final = 2.0


class SpecAssembler(SpecBuilder):
    """Assemble la spécification d'un modèle d'après son plan et ses mesures."""

    def __init__(
        self,
        doc: PdfDocument,
        page: PdfPage,
        plan: LayoutPlan,
        fonts: FontSet,
        served: dict[str, tuple[str, int, bool]],
    ) -> None:
        for i, d in enumerate(page.drawings, 1):
            d["_id"] = shape_id(i)
        self.labels = plan_to_labels(plan, page)
        super().__init__(doc, page, self.labels, fonts, served)
        self.plan = plan
        self.check = check_plan(plan, page)
        self.by_id: dict[str, Line] = {ln.id: ln for ln in self.lines}
        self.shape_index: dict[str, int] = {d["_id"]: i for i, d in enumerate(self.drawings)}
        self.owner: dict[str, str] = {}
        for h in plan.header.lines:
            self.owner.setdefault(h.line, "header")
        for ps in plan.sections():
            for t in ps.title_lines:
                self.owner.setdefault(t, ps.id)
            for it in ps.items:
                for pl in it.lines:
                    self.owner.setdefault(pl.line, ps.id)

    # ------------------------------------------------------------------ construction
    def build(self) -> tuple[Json, Json, dict[str, bytes]]:  # noqa: PLR0912
        """La spécification, le contenu d'exemple et les fichiers associés."""
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
        bands = self.sections_from_plan()
        if not bands:
            msg = "Aucune rubrique reconnue dans le modèle."
            raise ValueError(msg)
        anchors = self.plan_anchors()
        # Les formes que le plan ancre hors des rubriques sont réservées
        # avant la mesure des rubriques, qui ne les verra pas.
        for d in anchors:
            if d.anchor in ("header", "page", "flow", "band", "band_separator"):
                self.used.add(self.shape_index[d.shape])
        by_section: dict[str, list[PlanDecoration]] = {}
        for d in anchors:
            if d.section is not None and d.anchor in ("section", "item", "after_section"):
                by_section.setdefault(d.section, []).append(d)
        spec_bands: list[Json] = []
        for cols in bands:
            band_top = min(s["top"] for secs in cols for s in secs)
            xs = [min(s["x"] for s in secs) for secs in cols]
            rights = [max(ln.right for s in secs for ln in s["all_lines"]) for secs in cols]
            columns: list[Json] = []
            for ci, secs in enumerate(cols):
                col_x = xs[ci]
                right = rights[ci]
                if ci + 1 < len(cols):
                    right = min(right, xs[ci + 1] - 2)
                prev_bottom = band_top
                spec_secs: list[Json] = []
                for s in secs:
                    spec_sec = self.section_spec(s, col_x, prev_bottom, styles, sample)
                    self.section_anchors(s, spec_sec, col_x, by_section.get(s["id"], []))
                    if s["kind"].startswith(OTHER_PREFIX):
                        others = sample.setdefault("others", {})
                        others.setdefault(s["kind"], {"title": "", "items": []})["title"] = (
                            spec_sec["title_sample"]
                        )
                    spec_secs.append(spec_sec)
                    prev_bottom = s["bottom"]
                columns.append(
                    {
                        "x_mm": round(col_x, 2),
                        "w_mm": round(max(right - col_x, 10.0), 2),
                        "sections": spec_secs,
                        "bottom_sample": prev_bottom,
                    }
                )
            spec_bands.append(
                {"columns": columns, "y_sample": round(band_top, 2), "decorations": []}
            )
        for i, spec_band in enumerate(spec_bands):
            bottom = max(c["bottom_sample"] for c in spec_band["columns"])
            spec_band["bottom_sample"] = bottom
            if i + 1 < len(spec_bands):
                spec_band["gap_after_mm"] = round(spec_bands[i + 1]["y_sample"] - bottom, 2)
        first_top = spec_bands[0]["y_sample"]
        content_bottom = max(ln.bottom for ln in self.lines)
        header = self.header_from_plan(first_top, styles, sample, anchors)
        page_decor = self.band_and_flow_decorations(spec_bands, anchors, first_top, content_bottom)
        page_decor.extend(self.page_decorations(spec_bands))
        if "section_title" not in styles:
            # Aucune rubrique titrée : les moteurs ont besoin d'un style de titre.
            base = next(iter(styles.values()))
            styles["section_title"] = {**base, "pitch_mm": None, "uppercase": True}
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
            "content_bottom_mm": round(content_bottom, 2),
        }
        return spec, sample, self.assets

    def sections_from_plan(self) -> list[list[list[Json]]]:
        """Bandes → colonnes → rubriques, avec leurs lignes ; les coquilles vides tombent."""
        seen_kinds: dict[str, int] = {}
        bands: list[list[list[Json]]] = []
        for pb in self.plan.bands:
            cols: list[list[Json]] = []
            for pc in pb.columns:
                secs = [s for ps in pc.sections if (s := self.section_lines(ps, seen_kinds))]
                if secs:
                    cols.append(secs)
            if cols:
                bands.append(cols)
        return bands

    def section_lines(self, ps: PlanSection, seen_kinds: dict[str, int]) -> Json | None:
        """Les lignes d'une rubrique du plan (celles qu'elle possède), et son genre."""
        title = [self.by_id[t] for t in ps.title_lines if self.owner.get(t) == ps.id]
        lines = [
            self.by_id[pl.line]
            for it in ps.items
            for pl in it.lines
            if self.owner.get(pl.line) == ps.id and pl.line in self.by_id
        ]
        if not title and not lines:
            return None
        kind: str = ps.kind
        if kind == "other":
            slug = slugify(" ".join(t.text for t in title)) or ps.id.lower()
            kind = f"{OTHER_PREFIX}{slug}"
            if kind in seen_kinds:
                seen_kinds[kind] += 1
                kind = f"{kind}-{seen_kinds[kind]}"
        seen_kinds.setdefault(kind, 1)
        side = frozenset(ps.side_roles)
        main = [ln for ln in lines if ln.role not in side]
        return {
            "id": ps.id,
            "kind": kind,
            "title": title,
            "lines": lines,
            "all_lines": title + lines,
            "side_roles": sorted(side),
            "x": min(ln.x for ln in title + (main or lines)),
            "top": min(ln.y for ln in title + lines),
        }

    # ------------------------------------------------------------------ décorations
    def plan_anchors(self) -> list[PlanDecoration]:
        """Les ancrages du plan qui désignent une forme connue, une seule fois chacune."""
        out: list[PlanDecoration] = []
        taken: set[str] = set()
        for d in self.plan.decorations:
            if d.shape in self.shape_index and d.shape not in taken:
                taken.add(d.shape)
                out.append(d)
        return out

    def shape_entry(self, d: Json) -> Json:
        """Les attributs communs d'une forme dans la spécification."""
        return {
            "kind": d["kind"],
            "x_mm": round(d["x"], 2),
            "y_mm": round(d["y"], 2),
            "w_mm": round(d["w"], 2),
            "h_mm": round(d["h"], 2),
            "fill": d.get("fill"),
            "stroke": d.get("stroke"),
            "stroke_pt": d.get("stroke_width_pt"),
        }

    def section_anchors(
        self, s: Json, spec_sec: Json, col_x: float, anchors: list[PlanDecoration]
    ) -> None:
        """Les formes que le plan ancre à cette rubrique et que la mesure n'a pas déjà rangées."""
        top = s["title"][0].y if s["title"] else s["top"]
        bottom = s["bottom"]
        for a in anchors:
            i = self.shape_index[a.shape]
            if i in self.used:
                continue
            d = self.drawings[i]
            entry = {
                **self.shape_entry(d),
                "dx_mm": round(d["x"] - col_x, 2),
                "anchor": "after" if a.anchor == "after_section" else "section",
                "dy_mm": round(d["y"] - (bottom if a.anchor == "after_section" else top), 2),
            }
            entry.pop("x_mm")
            entry.pop("y_mm")
            if (
                a.anchor != "after_section"
                and d["kind"] == "vline"
                and d["h"] > 0.45 * (bottom - top)
            ):
                entry.update(stretch=True, bottom_inset_mm=round(bottom - (d["y"] + d["h"]), 2))
            self.used.add(i)
            spec_sec["decorations"].append(entry)

    def band_and_flow_decorations(
        self,
        spec_bands: list[Json],
        anchors: list[PlanDecoration],
        first_top: float,
        content_bottom: float,
    ) -> list[Json]:
        """Filets de bande (étirés avec elle), séparateurs de bandes, filets de flux, fonds de page."""
        page_decor: list[Json] = []
        for a in anchors:
            d = self.drawings[self.shape_index[a.shape]]
            if a.anchor == "page":
                page_decor.append({**self.shape_entry(d), "anchor": "page"})
            elif a.anchor == "flow":
                page_decor.append(
                    {
                        **self.shape_entry(d),
                        "anchor": "flow",
                        "dy_mm": round(d["y"] - first_top, 2),
                        "stretch": d["h"] > STRETCH_MIN_MM,
                        "bottom_inset_mm": round(content_bottom - (d["y"] + d["h"]), 2),
                    }
                )
            elif a.anchor in ("band", "band_separator"):
                if a.band is None or not 0 <= a.band < len(spec_bands):
                    page_decor.append({**self.shape_entry(d), "anchor": "page"})
                    continue
                band = spec_bands[a.band]
                if a.anchor == "band":
                    band["decorations"].append(
                        {
                            **self.shape_entry(d),
                            "dy_mm": round(d["y"] - band["y_sample"], 2),
                            "stretch": d["h"] > STRETCH_MIN_MM,
                            "bottom_inset_mm": round(band["bottom_sample"] - (d["y"] + d["h"]), 2),
                        }
                    )
                else:
                    page_decor.append(
                        {
                            **self.shape_entry(d),
                            "anchor": "band",
                            "band": a.band,
                            "dy_mm": round(d["y"] - band["bottom_sample"], 2),
                        }
                    )
        return page_decor

    # ------------------------------------------------------------------ en-tête
    def header_from_plan(
        self, first_top: float, styles: dict[str, Json], sample: Json, anchors: list[PlanDecoration]
    ) -> Json:
        """L'en-tête : champs (un par rôle, recollés s'ils tiennent sur plusieurs lignes),
        coordonnées, photo désignée par le plan, ornements ancrés à l'en-tête."""
        by_field: dict[str, list[Line]] = {}
        for h in self.plan.header.lines:
            if self.owner.get(h.line) == "header" and h.line in self.by_id:
                by_field.setdefault(h.field, []).append(self.by_id[h.line])
        elements = [
            self.header_element(field, sorted(lines, key=lambda ln: (ln.y, ln.x)), styles, sample)
            for field, lines in by_field.items()
        ]
        elements.sort(key=lambda e: e["y_mm"])
        self.align_header_elements(elements)
        contact_lines = [
            ln
            for lines in by_field.values()
            for ln in lines
            if ln.role in ("phone", "email", "address", "website")
        ]
        icons = self.icons_for(contact_lines) if contact_lines else {}
        for e in elements:
            if e["field"] in icons:
                e["icon"] = icons[e["field"]]
        if "headline" not in by_field:
            elements.append(self.synthesize_headline(elements, styles, first_top))
        photo = self.photo_from_plan()
        decor = [
            self.shape_entry(self.drawings[self.shape_index[a.shape]])
            for a in anchors
            if a.anchor == "header"
        ]
        for i, d in enumerate(self.drawings):
            if i in self.used or d["y"] > first_top - 2:
                continue
            self.used.add(i)
            decor.append(self.shape_entry(d))
        return {"elements": elements, "photo": photo, "decorations": decor}

    def header_element(
        self, field: str, lines: list[Line], styles: dict[str, Json], sample: Json
    ) -> Json:
        """Un champ de l'en-tête, sur une ou plusieurs lignes."""
        key = f"header.{field}"
        styles.setdefault(key, self.style_of(lines))
        text = " ".join(ln.text for ln in lines)
        e: Json = {
            "field": field,
            "style": key,
            "x_mm": round(min(ln.x for ln in lines), 2),
            "y_mm": round(lines[0].y, 2),
            "w_mm": round(max(ln.w for ln in lines), 2),
        }
        if len(lines) > 1:
            e["box_w_mm"] = round(max(ln.w for ln in lines) + BOX_SLACK_MM, 2)
            e["pitch_mm"] = round(med([b.y - a.y for a, b in itertools.pairwise(lines)]), 3)
            e["sample_lines"] = [
                {"text": ln.text, "x_mm": round(ln.x, 2), "y_mm": round(ln.y, 2)} for ln in lines
            ]
            e["align"] = self.lines_alignment(lines)
            if e["align"] == "center":
                e["cx_mm"] = round(med([ln.x + ln.w / 2 for ln in lines]), 2)
            elif e["align"] == "right":
                e["rx_mm"] = round(max(ln.right for ln in lines), 2)
        if field in ("first_name", "last_name"):
            sample["identity"][field] = text.title()
        elif field == "headline":
            sample["identity"]["headline"] = text
        elif field == "full_name":
            parts = text.split()
            sample["identity"].update(
                first_name=parts[0].title() if parts else "",
                last_name=" ".join(parts[1:]).title(),
            )
        else:
            sample["contact"][{"address": "city"}.get(field, field)] = text
        return e

    @staticmethod
    def lines_alignment(lines: list[Line]) -> str:
        """Gauche, droite ou centré : ce que les lignes d'un même champ partagent."""
        lefts = [ln.x for ln in lines]
        rights = [ln.right for ln in lines]
        centers = [ln.x + ln.w / 2 for ln in lines]
        spread = {
            "left": max(lefts) - min(lefts),
            "right": max(rights) - min(rights),
            "center": max(centers) - min(centers),
        }
        return min(spread, key=lambda k: (spread[k], ("left", "right", "center").index(k)))

    @staticmethod
    def align_header_elements(elements: list[Json]) -> None:
        """Un champ d'une ligne aligné à droite ou centré avec un voisin le reste au rendu."""
        singles = [e for e in elements if "box_w_mm" not in e]
        for e in singles:
            others = [o for o in singles if o is not e and abs(o["y_mm"] - e["y_mm"]) > 1.0]
            right = e["x_mm"] + e["w_mm"]
            center = e["x_mm"] + e["w_mm"] / 2
            if any(
                abs(o["x_mm"] + o["w_mm"] - right) < EDGE_TOLERANCE_MM
                and abs(o["x_mm"] - e["x_mm"]) > EDGE_TOLERANCE_MM
                for o in others
            ):
                e.update(align="right", rx_mm=round(right, 2))
            elif any(
                abs(o["x_mm"] + o["w_mm"] / 2 - center) < CENTER_TOLERANCE_MM
                and abs(o["w_mm"] - e["w_mm"]) > WIDTH_DIFFERENCE_MM
                for o in others
            ):
                e.update(align="center", cx_mm=round(center, 2))

    def photo_from_plan(self) -> Json | None:
        """La photo que le plan désigne (identifiant d'image), ou aucune."""
        pid = self.plan.header.photo
        if not pid or not pid[1:].isdigit():
            return None
        index = int(pid[1:]) - 1
        if not 0 <= index < len(self.page.images):
            return None
        return self.photo_spec(self.page.images[index])
