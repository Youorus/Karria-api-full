"""Le bloc de titre de l'en-tête — nom, poste visé, précision — mis en page comme un tout.

Contrat
-------
La spécification d'un modèle place chaque champ de l'en-tête là où l'exemple
l'avait : « Sacha » ici, « Dubois » là, le poste dessous. Avec le nom d'une
personne, ajuster chaque champ **séparément** défait le dessin (revue du
2026-11-20, les neuf modèles publiés) : un prénom en 64 pt au-dessus d'un nom
réduit à 40, un nom replié sur quatre lignes dans le poste visé, un poste
synthétisé collé à la marge et réduit à 5 pt, des coordonnées de pied de page
prises pour de l'en-tête.

`layout_header` rend une **copie** de la spécification dont l'en-tête est
prêt à poser, pour le CV d'une personne (jamais pour l'exemple du modèle, qui
reste le dessin d'origine) :

- **Le nom est un seul caractère.** Prénom et nom prennent le style du plus
  grand des deux (famille, graisse, corps, casse, couleur, interlettrage) et
  la **même** réduction quand l'un des deux est trop long. Ils gardent la
  structure du modèle (empilés ou sur une ligne) et l'alignement que leurs
  bords partagent ; le bloc ne dépasse pas la hauteur qu'il avait.
- **Le poste visé se lit.** Sous le nom, aligné comme lui, jamais sous 80 % de
  son corps sur une ligne ; au-delà il se replie sur deux lignes, et ce qui
  est posé sous lui descend d'autant.
- **La précision** (`identity.availability` : « Recherche alternance 24 mois ·
  Disponible immédiatement ») a sa ligne sous le poste visé, dans la même
  famille, plus petite, en bas de casse.
- **Les coordonnées** empilées retrouvent le bord qu'elles partagent ; un
  centrage ou un fer à droite que la géométrie dément est retiré.

Les éléments posés portent `laid: true` : les moteurs les écrivent tels quels,
sans les réajuster. Tout est décidé ici, avec les métriques réelles des
polices servies, pour que le PDF, l'atelier et le DOCX disent la même chose.
"""

from __future__ import annotations

import copy
import itertools
import statistics
from typing import Any, Final

from karria.modules.documents.infrastructure.fonts import FontSet, facts_of

Json = dict[str, Any]

MM_PER_PT: Final = 25.4 / 72
#: Les styles que la mise en page ajoute à la spécification (le dernier segment
#: est le rôle : c'est lui qui nomme le style Word).
NAME_STYLE: Final = "header.title.name"
HEADLINE_STYLE: Final = "header.title.headline"
AVAILABILITY_STYLE: Final = "header.title.availability"
NAME_FIELDS: Final = frozenset({"first_name", "last_name", "full_name"})
CONTACT_FIELDS: Final = frozenset({"phone", "email", "address", "website"})
#: Un nom de rappel (le nom en petit dans un bandeau) fait moins de 40 % du grand ;
#: un prénom en 22 pt au-dessus d'un nom en 46 (Minimalist) fait partie du nom.
MAIN_NAME_RATIO: Final = 0.4
#: Deux bords « égaux » : l'écart toléré, en mm (approches latérales des glyphes).
EDGE_MM: Final = 1.5
#: Interligne minimal d'un nom sur plusieurs lignes, en corps : des capitales
#: se serrent, des bas de casse ont des hampes et des jambages.
NAME_LEAD_UPPER: Final = 0.95
NAME_LEAD_MIXED: Final = 1.08
#: Sous cette réduction, un nom écrit sur une ligne passe sur deux.
NAME_SPLIT_SCALE: Final = 0.6
NAME_MIN_SCALE: Final = 0.2
#: Avant de réduire un nom sous 70 %, son interlettrage se resserre.
NAME_TRACKING_FROM: Final = 0.7
#: Le poste visé : corps essayés sur une ligne, puis repliés.
HEADLINE_LINE_SCALES: Final = (1.0, 0.95, 0.9, 0.85, 0.8)
HEADLINE_WRAP_SCALES: Final = (1.0, 0.9, 0.8, 0.7, 0.6)
HEADLINE_MAX_LINES: Final = 2
TRACKING_STEPS: Final = (1.0, 0.6, 0.3)
WRAP_PITCH: Final = 1.12
#: La précision : corps relatif au poste visé, et bornes en points.
AVAILABILITY_RATIO: Final = 0.88
AVAILABILITY_MIN_PT: Final = 8.5
AVAILABILITY_MAX_PT: Final = 12.0
#: Un champ plus bas que cette part de la page est un pied de page.
FOOTER_FROM: Final = 0.7
NEIGHBOUR_GAP_MM: Final = 3.0
#: Un alignement déclaré qui s'écarte de la géométrie de plus que cela est faux.
MISMATCH_MM: Final = 3.0
#: Un champ écrit sur au moins deux lignes dans l'exemple : son alignement est mesuré.
MEASURED_LINES: Final = 2


def layout_header(spec: Json, view: Json, fonts: FontSet, *, photo: bool) -> Json:
    """La spécification, en-tête posé pour cette vue (l'exemple revient inchangé).

    Args:
        spec: Spécification du modèle (non modifiée).
        view: Vue du CV (`domain/view.from_cv`).
        fonts: Polices servies (mesure des textes).
        photo: Une photo sera-t-elle affichée ? (elle borne la place du nom).
    """
    if view.get("sample") or view.get("kind") == "letter" or not view.get("header"):
        return spec
    if not spec.get("header", {}).get("elements"):
        return spec
    out = copy.deepcopy(spec)
    layout = _HeaderLayout(out, view, fonts, photo=photo)
    layout.sanitize()
    layout.title_group()
    return out


def _spread(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


class _HeaderLayout:
    def __init__(self, spec: Json, view: Json, fonts: FontSet, *, photo: bool) -> None:
        self.spec, self.fonts, self.photo = spec, fonts, photo
        self.values: dict[str, str] = view["header"]
        self.styles: dict[str, Json] = spec["styles"]
        self.elements: list[Json] = spec["header"]["elements"]
        self.pw = float(spec["page"]["width_mm"])
        self.ph = float(spec["page"]["height_mm"])
        columns = [c for b in spec["bands"] for c in b["columns"]]
        self.margin = min((float(c["x_mm"]) for c in columns), default=15.0)

    # ------------------------------------------------------------------ mesures

    def value(self, e: Json) -> str:
        return self.values.get(e["field"], "") or ""

    def box(self, e: Json) -> float:
        return float(self.styles[e["style"]]["box_h_mm"])

    def width(self, text: str, style: Json) -> float:
        return float(self.fonts.width_mm(text, style))

    def extent(self, e: Json) -> tuple[float, float]:
        """Les abscisses qu'occupe réellement le texte de la personne dans cet élément."""
        w = self.width(self.value(e), self.styles[e["style"]])
        if e.get("box_w_mm"):
            w = min(w, float(e["box_w_mm"]))
        if e.get("align") == "right" and "rx_mm" in e:
            return float(e["rx_mm"]) - w, float(e["rx_mm"])
        if e.get("align") == "center" and "cx_mm" in e:
            return float(e["cx_mm"]) - w / 2, float(e["cx_mm"]) + w / 2
        return float(e["x_mm"]), float(e["x_mm"]) + w

    # --------------------------------------------------------------- coordonnées

    def sanitize(self) -> None:  # noqa: PLR0912 - une règle par défaut de mesure constaté
        """Retire les alignements que la géométrie dément ; rend aux coordonnées leur bord commun."""
        for e in self.elements:
            x, w = float(e["x_mm"]), float(e.get("w_mm") or 0.0)
            if (
                e.get("align") == "center"
                and abs(float(e.get("cx_mm", -99)) - (x + w / 2)) > MISMATCH_MM
            ):
                e.pop("align", None)
                e.pop("cx_mm", None)
            if (
                e.get("align") == "right"
                and abs(float(e.get("rx_mm", -99)) - (x + w)) > MISMATCH_MM
            ):
                e.pop("align", None)
                e.pop("rx_mm", None)
            if (
                e.get("align") == "right"
                and len(e.get("sample_lines") or []) < MEASURED_LINES
                and any(
                    o is not e
                    and abs(float(o["x_mm"]) - x) <= 1.0
                    and abs(float(o["y_mm"]) - float(e["y_mm"])) > 1.0
                    for o in self.elements
                )
            ):
                # Un bord gauche partagé avec un voisin l'emporte sur un fer à
                # droite déduit d'une seule ligne.
                e.pop("align", None)
                e.pop("rx_mm", None)
            lines = e.get("sample_lines") or []
            if e.get("box_w_mm") and (
                not e.get("pitch_mm") or _spread([float(ln["y_mm"]) for ln in lines]) < 1.0
            ):
                # Une « boîte » dont les lignes sont à la même hauteur n'en est pas une.
                for key in ("box_w_mm", "pitch_mm", "sample_lines"):
                    e.pop(key, None)
                if e.get("align") == "left":
                    e.pop("align")
        edge = self.pw - self.margin
        for e in self.elements:
            x, w = float(e["x_mm"]), float(e.get("w_mm") or 0.0)
            if not e.get("align") and w and x > self.pw / 2 and x + w >= edge - 1.5:
                # Un texte seul dont le bord droit touche la marge de droite s'y appuie.
                e.update(align="right", rx_mm=round(x + w, 2))
        for stack in self.contact_stacks():
            measured = [e for e in stack if not e.get("synthesized")]
            if len(measured) < 2:  # noqa: PLR2004
                continue
            lefts = [float(e["x_mm"]) for e in measured]
            rights = [float(e["x_mm"]) + float(e["w_mm"]) for e in measured]
            centers = [float(e["x_mm"]) + float(e["w_mm"]) / 2 for e in measured]
            if _spread(lefts) <= 1.0:
                for e in stack:
                    e.pop("align", None)
                    e.pop("rx_mm", None)
                    e.pop("cx_mm", None)
                    e["x_mm"] = round(min(lefts), 2)
            elif _spread(rights) <= 1.0:
                for e in stack:
                    e.pop("cx_mm", None)
                    e.update(align="right", rx_mm=round(max(rights), 2))
            elif _spread(centers) <= 1.0:
                for e in stack:
                    e.pop("rx_mm", None)
                    e.update(align="center", cx_mm=round(statistics.fmean(centers), 2))

    def contact_stacks(self) -> list[list[Json]]:
        """Les coordonnées empilées : voisines en hauteur, sur des abscisses qui se recouvrent."""
        contact = sorted(
            (e for e in self.elements if e["field"] in CONTACT_FIELDS), key=lambda e: e["y_mm"]
        )
        stacks: list[list[Json]] = []
        for e in contact:
            x0, x1 = float(e["x_mm"]), float(e["x_mm"]) + float(e.get("w_mm") or 0.0)
            for stack in stacks:
                last = stack[-1]
                l0, l1 = float(last["x_mm"]), float(last["x_mm"]) + float(last.get("w_mm") or 0.0)
                near = 0.5 < float(e["y_mm"]) - float(last["y_mm"]) < 12.0  # noqa: PLR2004
                if near and min(x1, l1) - max(x0, l0) > 2.0:  # noqa: PLR2004
                    stack.append(e)
                    break
            else:
                stacks.append([e])
        return stacks

    # ------------------------------------------------------------------- bornes

    def bounds(
        self, top: float, bottom: float, point: float, skip: list[Json]
    ) -> tuple[float, float]:
        """Jusqu'où un texte ancré à `point` peut s'étendre entre `top` et `bottom` (mm)."""
        left, right = min(self.margin, point), max(self.pw - self.margin, point)

        def obstacle(x0: float, x1: float, gap: float) -> None:
            nonlocal left, right
            if (x0 + x1) / 2 >= point:
                right = min(right, x0 - gap)
            else:
                left = max(left, x1 + gap)

        p = self.spec["header"].get("photo") if self.photo else None
        if p and float(p["y_mm"]) < bottom and float(p["y_mm"]) + float(p["h_mm"]) > top:
            obstacle(float(p["x_mm"]), float(p["x_mm"]) + float(p["w_mm"]), 4.0)
        for e in self.elements:
            if any(e is s for s in skip) or e.get("flow") or not self.value(e):
                continue
            if min(bottom, float(e["y_mm"]) + self.box(e)) - max(top, float(e["y_mm"])) <= 0.5:  # noqa: PLR2004
                continue
            obstacle(*self.extent(e), NEIGHBOUR_GAP_MM)
        for d in [
            *self.spec["header"].get("decorations", []),
            *self.spec.get("page_decorations", []),
        ]:
            if d.get("kind") != "vline":
                continue
            d_top = float(
                d["y_mm"]
                if "y_mm" in d
                else float(self.spec["first_band_top_mm"]) + float(d.get("dy_mm", 0.0))
            )
            if d_top < bottom and d_top + float(d["h_mm"]) > top:
                obstacle(float(d["x_mm"]), float(d["x_mm"]), 2.0)
        band = self.spec["bands"][0] if self.spec["bands"] else None
        if band and float(band["y_sample"]) < bottom and float(band["bottom_sample"]) > top:
            for c in band["columns"]:
                x0, x1 = float(c["x_mm"]), float(c["x_mm"]) + float(c["w_mm"])
                if x0 > point + 5 or x1 < point - 5:
                    obstacle(x0, x1, 4.0)
        return left, right

    @staticmethod
    def room(align: str, anchor: float, left: float, right: float) -> float:
        if align == "right":
            return max(anchor - left, 10.0)
        if align == "center":
            return max(2 * min(anchor - left, right - anchor), 10.0)
        return max(right - anchor, 10.0)

    @staticmethod
    def place(e: Json, align: str, anchor: float, width: float) -> None:
        """Écrit la position d'un élément : `x_mm` est toujours son bord gauche réel."""
        for key in ("align", "rx_mm", "cx_mm"):
            e.pop(key, None)
        if align == "right":
            e.update(align="right", rx_mm=round(anchor, 2), x_mm=round(anchor - width, 2))
        elif align == "center":
            e.update(align="center", cx_mm=round(anchor, 2), x_mm=round(anchor - width / 2, 2))
        else:
            e["x_mm"] = round(anchor, 2)
        e["w_mm"] = round(width, 2)

    # --------------------------------------------------------------------- titre

    def title_group(self) -> None:  # noqa: PLR0912, PLR0915 - le bloc de titre, de haut en bas
        names = [e for e in self.elements if e["field"] in NAME_FIELDS and self.value(e)]
        if not names:
            return
        top_size = max(float(self.styles[e["style"]]["size_pt"]) for e in names)
        main = [
            e
            for e in names
            if float(self.styles[e["style"]]["size_pt"]) >= MAIN_NAME_RATIO * top_size
        ]
        if any(e["field"] != "full_name" for e in main):
            main = [e for e in main if e["field"] != "full_name"]
        rows = self.rows(main)
        dominant = max(
            main,
            key=lambda e: (
                float(self.styles[e["style"]]["size_pt"]),
                int(self.styles[e["style"]]["weight"]),
                -float(self.styles[e["style"]]["letter_spacing_pt"]),
            ),
        )
        base = dict(self.styles[dominant["style"]])
        size0, box0 = float(base["size_pt"]), float(base["box_h_mm"])
        top0 = min(float(e["y_mm"]) for e in main)
        bottom0 = max(float(e["y_mm"]) + self.box(e) for e in main)
        lefts = [min(float(e["x_mm"]) for e in r) for r in rows]
        rights = [max(float(e["x_mm"]) + float(e.get("w_mm") or 0.0) for e in r) for r in rows]
        group_left, group_right = min(lefts), max(rights)
        headline = self.main_headline(top0)
        align, anchor = self.name_alignment(rows, lefts, rights, headline)

        # Largeur : la place réelle, à la hauteur du bloc.
        skip = [*main, *([headline] if headline is not None else [])]
        room = self.room(align, anchor, *self.bounds(top0, bottom0, anchor, skip))
        lines = [[(e, self.value(e)) for e in row] for row in rows]
        space = self.width("x x", base) - self.width("xx", base)

        def natural(parts: list[tuple[Json, str]]) -> float:
            return sum(self.width(t, base) for _, t in parts) + space * (len(parts) - 1)

        def fit() -> float:
            return min(1.0, room / max(natural(parts) for parts in lines))

        scale_h = fit()
        split = False
        if len(lines) == 1 and scale_h < NAME_SPLIT_SCALE:
            halves = self.split_line(lines[0])
            if halves is not None:
                lines, split = halves, True
                scale_h = fit()
        tracking0 = float(base["letter_spacing_pt"])
        for tracking in TRACKING_STEPS[1:]:
            if scale_h >= NAME_TRACKING_FROM or not tracking0:
                break
            base["letter_spacing_pt"] = tracking0 * tracking
            space = self.width("x x", base) - self.width("xx", base)
            scale_h = fit()
        # Hauteur : le bloc garde celle qu'il avait dans le modèle.
        em = size0 * MM_PER_PT
        lead = NAME_LEAD_UPPER if base.get("uppercase") else NAME_LEAD_MIXED
        sizes = [float(self.styles[e["style"]]["size_pt"]) for e in main]
        same_size = _spread(sizes) <= 1.0
        tops = [float(r[0]["y_mm"]) for r in rows]
        tpl_pitch = (
            statistics.median(b - a for a, b in itertools.pairwise(tops))
            if (len(rows) > 1 and same_size)
            else 0.0
        )
        pitch = max(tpl_pitch, lead * em)
        height = (len(lines) - 1) * pitch + box0
        scale_v = 1.0 if split else min(1.0, (bottom0 - top0) / height)
        scale = max(min(scale_h, scale_v), NAME_MIN_SCALE)

        style = {
            **base,
            "size_pt": round(size0 * scale, 2),
            "letter_spacing_pt": round(float(base["letter_spacing_pt"]) * scale, 2),
            "box_h_mm": round(box0 * scale, 3),
            "pitch_mm": None,
        }
        self.styles[NAME_STYLE] = style
        box, step = box0 * scale, pitch * scale
        # Réduit, le bloc reste posé sur son bas (le poste visé garde sa place) ;
        # replié sur une ligne de plus, il part de son haut et pousse la suite.
        # La ligne de base de la dernière ligne ne bouge pas : une boîte réduite
        # posée sur le bas de l'ancienne descendrait le texte dans le poste visé.
        rise = self.ascent_share(base)
        last_top = (bottom0 - box0) + (box0 - box) * rise
        first_top = top0 if split else last_top - (len(lines) - 1) * step
        gap = space * scale
        for i, parts in enumerate(lines):
            y = round(first_top + i * step, 2)
            widths = [self.width(t, style) for _, t in parts]
            total = sum(widths) + gap * (len(parts) - 1)
            x = (
                anchor - total
                if align == "right"
                else anchor - total / 2
                if align == "center"
                else anchor
            )
            for (e, _), w in zip(parts, widths, strict=True):
                e.update(style=NAME_STYLE, y_mm=y, laid=True)
                for key in ("box_w_mm", "pitch_mm", "sample_lines"):
                    e.pop(key, None)
                if len(parts) == 1:
                    self.place(e, align, anchor, w)
                else:
                    self.place(e, "left", x, w)
                x += w + gap
        name_bottom = first_top + (len(lines) - 1) * step + box
        grown = max(0.0, name_bottom - bottom0)
        tpl_bottom = bottom0
        bottom = name_bottom
        if headline is not None:
            if not headline.get("flow"):
                tpl_bottom = max(tpl_bottom, float(headline["y_mm"]) + self.box(headline))
            bottom = self.headline_block(
                headline, align, anchor, group_left, group_right, name_bottom, grown, skip
            )
        x0, x1 = min(group_left, anchor), max(group_right, anchor)
        self.push_below(tpl_bottom, bottom, x0, x1, skip)
        # Le bloc de titre pousse la première bande quand elle est **sous** lui ;
        # une bande posée à côté (le profil à gauche d'un grand nom, cv2) garde
        # sa place, et sa hauteur minimale (`band_floor`) protège la suivante.
        band = self.spec["bands"][0] if self.spec["bands"] else None
        under = band is not None and any(
            c.get("sections")
            and min(float(c["x_mm"]) + float(c["w_mm"]), x1) - max(float(c["x_mm"]), x0) > 5.0  # noqa: PLR2004
            for c in band["columns"]
        )
        if under and top0 < float(self.spec["first_band_top_mm"]):
            for e in self.elements:
                if e.get("laid") and not e.get("flow"):
                    e["pushes_band"] = True

    def ascent_share(self, style: Json) -> float:
        """La part de la boîte de texte au-dessus de la ligne de base."""
        font = self.fonts.get(str(style["family"]), int(style["weight"]), bool(style.get("italic")))
        if font is None:
            return 0.8
        facts = facts_of(font.data)
        return float(facts.ascent / (facts.ascent - facts.descent))

    def rows(self, main: list[Json]) -> list[list[Json]]:
        """Les lignes du nom dans le modèle : de haut en bas, chaque ligne de gauche à droite."""
        rows: list[list[Json]] = []
        for e in sorted(main, key=lambda e: (float(e["y_mm"]), float(e["x_mm"]))):
            if rows and abs(float(rows[-1][0]["y_mm"]) - float(e["y_mm"])) < 0.5 * min(
                self.box(e), self.box(rows[-1][0])
            ):
                rows[-1].append(e)
            else:
                rows.append([e])
        # Sur une même ligne, dans un même caractère : prénom puis nom, à la française.
        order = {"first_name": 0, "full_name": 1, "last_name": 2}
        return [sorted(r, key=lambda e: order[e["field"]]) for r in rows]

    def split_line(self, parts: list[tuple[Json, str]]) -> list[list[tuple[Json, str]]] | None:
        """Un nom d'une ligne, beaucoup trop long : prénom au-dessus, nom dessous."""
        if len(parts) >= 2:  # noqa: PLR2004
            return [[p] for p in parts]
        return None

    def main_headline(self, top0: float) -> Json | None:
        """Le poste visé qui accompagne le nom (pas un rappel plus haut dans un bandeau)."""
        found = [
            e
            for e in self.elements
            if e["field"] == "headline" and (e.get("flow") or float(e["y_mm"]) >= top0 - 1.0)
        ]
        if not found:
            return None
        return max(found, key=lambda e: float(self.styles[e["style"]]["size_pt"]))

    def name_alignment(  # noqa: PLR0911 - une issue par bord partagé
        self, rows: list[list[Json]], lefts: list[float], rights: list[float], headline: Json | None
    ) -> tuple[str, float]:
        """Le bord que les lignes du nom partagent (ou, sur une ligne, celui du poste visé)."""
        centers = [(a + b) / 2 for a, b in zip(lefts, rights, strict=True)]
        left, right, center = min(lefts), max(rights), statistics.fmean(centers)
        if len(rows) > 1:
            if _spread(lefts) <= EDGE_MM:
                return "left", left
            if _spread(rights) <= EDGE_MM:
                return "right", right
            if _spread(centers) <= EDGE_MM:
                return "center", center
        if headline is not None and not headline.get("synthesized") and headline.get("w_mm"):
            h_left = float(headline["x_mm"])
            h_right = h_left + float(headline["w_mm"])
            if abs(h_left - left) <= 2.0:  # noqa: PLR2004
                return "left", left
            if abs(h_right - right) <= 2.0:  # noqa: PLR2004
                return "right", right
            if abs((h_left + h_right) / 2 - center) <= 2.0:  # noqa: PLR2004
                return "center", center
        explicit = next((e.get("align") for r in rows for e in r if e.get("align")), None)
        if explicit == "right" and len(rows) == 1:
            return "right", right
        if explicit == "center":
            return "center", center
        if abs(center - self.pw / 2) <= 4.0 and left > self.margin + 10:  # noqa: PLR2004
            return "center", center
        if self.pw - self.margin - right <= 12.0 and left - self.margin > 25.0:  # noqa: PLR2004
            return "right", right
        return "left", left

    # --------------------------------------------------------------- poste visé

    def headline_block(  # noqa: PLR0917 - la géométrie du nom, telle que mesurée
        self,
        e: Json,
        name_align: str,
        name_anchor: float,
        group_left: float,
        group_right: float,
        name_bottom: float,
        grown: float,
        skip: list[Json],
    ) -> float:
        """Pose le poste visé puis la précision ; rend le bas du bloc (mm)."""
        text = self.value(e)
        extra = (self.values.get("availability") or "").strip()
        base = dict(self.styles[e["style"]])
        align, anchor = self.headline_alignment(e, name_align, name_anchor, group_left, group_right)
        if e.get("flow"):
            # Le poste visé ouvre le flux (pas de place sous le nom) : le moteur le
            # pose en bloc ; on lui donne son bord et sa largeur.
            left, right = self.bounds(name_bottom, name_bottom + 1.0, anchor, skip)
            e.update(x_mm=round(anchor if align == "left" else left, 2), laid=True)
            e["flow_w_mm"] = round(self.room(align, anchor, left, right), 2)
            e["flow_align"] = align
            if extra:
                self.styles[AVAILABILITY_STYLE] = self.availability_style(base)
                e["flow_extra"] = AVAILABILITY_STYLE
            return name_bottom
        y = float(e["y_mm"]) + grown
        bottom = max(name_bottom, y)
        anchor = self.clear_rule(y, y + float(base["box_h_mm"]), align, anchor)
        left, right = self.bounds(y, y + float(base["box_h_mm"]), anchor, skip)
        room = self.room(align, anchor, left, right)
        if text:
            size, spacing, count = self.fit_lines(text, base, room)
            scale = size / float(base["size_pt"])
            style = {
                **base,
                "size_pt": round(size, 2),
                "letter_spacing_pt": round(spacing, 2),
                "box_h_mm": round(float(base["box_h_mm"]) * scale, 3),
                "pitch_mm": None,
            }
            self.styles[HEADLINE_STYLE] = style
            for key in ("box_w_mm", "pitch_mm", "sample_lines", "lines"):
                e.pop(key, None)
            e.update(style=HEADLINE_STYLE, y_mm=round(y, 2), laid=True)
            pitch = float(style["box_h_mm"]) * WRAP_PITCH
            if count > 1:
                self.place(e, align, anchor, room)
                e.update(box_w_mm=round(room, 2), pitch_mm=round(pitch, 3), lines=count)
            else:
                self.place(e, align, anchor, self.width(text, style))
            bottom = y + float(style["box_h_mm"]) + (count - 1) * pitch
        if extra:
            style = self.availability_style(base)
            self.styles[AVAILABILITY_STYLE] = style
            gap = max(0.8, 0.3 * float(style["box_h_mm"])) if text else 0.0
            ay = bottom + gap if text else y
            width = self.width(extra, style)
            count = 1
            if width > room:
                count = min(3, len(self.wrap(extra, style, room)))
            pitch = float(style["box_h_mm"]) * WRAP_PITCH
            added: Json = {
                "field": "availability",
                "style": AVAILABILITY_STYLE,
                "y_mm": round(ay, 2),
                "x_mm": 0.0,
                "w_mm": 0.0,
                "laid": True,
                "synthesized": True,
            }
            if count > 1:
                self.place(added, align, anchor, room)
                added.update(box_w_mm=round(room, 2), pitch_mm=round(pitch, 3), lines=count)
            else:
                self.place(added, align, anchor, width)
            self.elements.insert(self.elements.index(e) + 1, added)
            bottom = ay + float(style["box_h_mm"]) + (count - 1) * pitch
        return bottom

    def clear_rule(self, top: float, bottom: float, align: str, anchor: float) -> float:
        """Un filet vertical tout contre le bord d'ancrage : le texte s'arrête avant lui (Meridian)."""
        for d in self.spec["header"].get("decorations", []):
            if d.get("kind") != "vline" or "y_mm" not in d:
                continue
            if not (float(d["y_mm"]) < bottom and float(d["y_mm"]) + float(d["h_mm"]) > top):
                continue
            x = float(d["x_mm"])
            if align == "right" and anchor - 8.0 <= x <= anchor + 1.0:
                return x - 2.5
            if align == "left" and anchor - 1.0 <= x <= anchor + 8.0:
                return x + 2.5
        return anchor

    def headline_alignment(  # noqa: PLR0911 - une issue par bord partagé
        self, e: Json, name_align: str, name_anchor: float, group_left: float, group_right: float
    ) -> tuple[str, float]:
        if e.get("synthesized") or not e.get("w_mm"):
            return name_align, name_anchor
        left = float(e["x_mm"])
        right = left + float(e["w_mm"])
        if len(e.get("sample_lines") or []) >= 2:  # noqa: PLR2004
            # Écrit sur plusieurs lignes dans l'exemple : son alignement est mesuré.
            if e.get("align") == "right" and "rx_mm" in e:
                return "right", float(e["rx_mm"])
            if e.get("align") == "center" and "cx_mm" in e:
                return "center", float(e["cx_mm"])
        if e.get("align") == "center" and "cx_mm" in e:
            return "center", float(e["cx_mm"])
        if abs(left - group_left) <= 2.0:  # noqa: PLR2004
            return "left", left
        if abs(right - group_right) <= 2.0:  # noqa: PLR2004
            return "right", right
        if abs((left + right) / 2 - (group_left + group_right) / 2) <= 2.0:  # noqa: PLR2004
            return "center", (left + right) / 2
        if e.get("align") == "right" and "rx_mm" in e:
            return "right", float(e["rx_mm"])
        return "left", left

    def availability_style(self, headline: Json) -> Json:
        """La précision : la famille du poste visé, en bas de casse, plus petite."""
        size = min(
            max(AVAILABILITY_RATIO * float(headline["size_pt"]), AVAILABILITY_MIN_PT),
            max(AVAILABILITY_MAX_PT, AVAILABILITY_MIN_PT),
            max(float(headline["size_pt"]), AVAILABILITY_MIN_PT),
        )
        family = str(headline["family"])
        ratio = self.fonts.box_ratio(family, 400, False)
        return {
            "family": family,
            "weight": 400,
            "italic": False,
            "size_pt": round(size, 2),
            "color": headline["color"],
            "letter_spacing_pt": 0.0,
            "uppercase": False,
            "box_h_mm": round(size * ratio * MM_PER_PT, 3),
            "pitch_mm": None,
            "synthesized": True,
        }

    def wrap(self, text: str, style: Json, room: float) -> list[str]:
        """Repli glouton, mot à mot, à la largeur donnée."""
        lines: list[str] = []
        current = ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if current and self.width(trial, style) > room:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        return lines

    def fit_lines(self, text: str, style: Json, room: float) -> tuple[float, float, int]:
        """(corps, interlettrage, lignes) du poste visé : une ligne si possible, sinon deux."""
        size0, ls0 = float(style["size_pt"]), float(style["letter_spacing_pt"])

        def sized(scale: float, tracking: float) -> Json:
            return {**style, "size_pt": size0 * scale, "letter_spacing_pt": ls0 * tracking * scale}

        for tracking in TRACKING_STEPS:
            if self.width(text, sized(1.0, tracking)) <= room:
                return size0, ls0 * tracking, 1
        for scale in HEADLINE_LINE_SCALES[1:]:
            if self.width(text, sized(scale, TRACKING_STEPS[-1])) <= room:
                return size0 * scale, ls0 * TRACKING_STEPS[-1] * scale, 1
        for scale in HEADLINE_WRAP_SCALES:
            tracking = 1.0 if scale == 1.0 else TRACKING_STEPS[1]
            trial = sized(scale, tracking)
            # Une marge sur la largeur : le navigateur replie à sa propre mesure.
            count = len(self.wrap(text, trial, room * 0.98))
            if count <= HEADLINE_MAX_LINES:
                return size0 * scale, ls0 * tracking * scale, count
        last = HEADLINE_WRAP_SCALES[-1]
        trial = sized(last, TRACKING_STEPS[-1])
        return (
            size0 * last,
            ls0 * TRACKING_STEPS[-1] * last,
            len(self.wrap(text, trial, room * 0.98)),
        )

    # ------------------------------------------------------------------- suite

    def push_below(
        self, tpl_bottom: float, bottom: float, x0: float, x1: float, skip: list[Json]
    ) -> None:
        """Ce qui était posé sous le bloc de titre descend d'autant que le bloc a grandi."""
        delta = bottom - tpl_bottom
        if delta <= 0.2:  # noqa: PLR2004
            return
        moved: list[Json] = []
        for e in self.elements:
            if any(e is s for s in skip) or e.get("laid") or e.get("flow"):
                continue
            y = float(e["y_mm"])
            if y < tpl_bottom - 1.0 or y > FOOTER_FROM * self.ph:
                continue
            left, right = (
                self.extent(e)
                if self.value(e)
                else (
                    float(e["x_mm"]),
                    float(e["x_mm"]) + float(e.get("w_mm") or 0.0),
                )
            )
            if min(right, x1) - max(left, x0) > 1.0:
                e["y_mm"] = round(y + delta, 2)
                # Un champ sans valeur (pas de site) n'entraîne personne : rien ne s'écrit là.
                if self.value(e):
                    moved.append(e)
        # Des coordonnées empilées descendent ensemble : la plus longue seule
        # poussée (une adresse de site) tombait sur la ligne d'après.
        for stack in self.contact_stacks_before(moved, delta):
            first = next(i for i, e in enumerate(stack) if any(e is m for m in moved))
            for e in stack[first:]:
                if not any(e is m for m in moved):
                    e["y_mm"] = round(float(e["y_mm"]) + delta, 2)

    def contact_stacks_before(self, moved: list[Json], delta: float) -> list[list[Json]]:
        """Les piles de coordonnées telles qu'elles étaient avant qu'un de leurs champs descende."""
        for e in moved:
            e["y_mm"] = round(float(e["y_mm"]) - delta, 2)
        stacks = [s for s in self.contact_stacks() if any(e is m for e in s for m in moved)]
        for e in moved:
            e["y_mm"] = round(float(e["y_mm"]) + delta, 2)
        return stacks
