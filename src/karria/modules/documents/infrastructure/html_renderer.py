"""Spécification + vue → HTML/CSS imprimable (Chromium, via Gotenberg).

Contrat
-------
- Le texte de la personne est **échappé** (la V1 rendait en `autoescape=False`).
- La page ne charge rien d'extérieur : polices (instances statiques) et
  images voyagent avec le HTML, référencées par leur nom de fichier.
- Tous les blocs de texte sont positionnés de la même façon (`position:
  relative`) : sinon Chromium les peint en deux phases et l'ordre du texte
  extrait ne suit plus l'ordre de lecture (ADR-0020 § 2).
- La mise en page tient sur une page de façon **déterministe** : après
  chargement des polices, espacements ×1 → ×0,6 puis corps ×1 → ×0,88 ; au-delà,
  une seconde page assumée. L'état retenu part dans le titre du document
  (`fit k=… f=… pages=…`), relu après conversion pour que le DOCX applique les
  mêmes facteurs. Aucun mot n'est réécrit.
"""

from __future__ import annotations

import html
from typing import Any, Final

from karria.modules.documents.infrastructure.fonts import FontSet

Json = dict[str, Any]

LIST_ROLE: Final = {"skills": "skill", "languages": "language", "interests": "interest"}
ROLE_MARKER: Final = frozenset({"bullet", "skill", "language", "interest"})
LIST_KINDS: Final = frozenset({"skills", "languages", "interests"})
SIBLINGS: Final[dict[str, list[str]]] = {
    "languages": ["skills", "interests"],
    "interests": ["languages", "skills"],
    "skills": ["languages", "interests"],
    "projects": ["experiences", "educations"],
    "references": ["educations", "experiences"],
    "educations": ["experiences"],
    "experiences": ["educations"],
    "summary": ["educations", "experiences"],
    "contact": ["skills", "languages"],
}
SPACING_STEPS: Final = (1, 0.9, 0.8, 0.7, 0.6)
FONT_STEPS: Final = (1, 0.96, 0.92, 0.88)


def esc(text: str | None) -> str:
    """Échappe un texte pour le HTML (attributs compris)."""
    return html.escape(text or "", quote=True)


def lh(st: Json) -> float:
    """Interligne d'un style (mm)."""
    return float(st["pitch_mm"] or st["box_h_mm"])


def half(st: Json) -> float:
    """Demi-blanc d'interligne : Chromium centre la boîte de texte dans la ligne."""
    return (lh(st) - float(st["box_h_mm"])) / 2


def style_key(styles: dict[str, Json], kind: str, role: str) -> str:
    """Le style d'un rôle dans une rubrique ; à défaut celui d'une rubrique sœur.

    Partagé par les deux moteurs : un rôle que le modèle n'a pas (un lieu, un
    sous-titre de liste) prend le même style en PDF et en DOCX.
    """
    key = f"{kind}.{role}"
    if key in styles:
        return key
    for k in styles:
        if k.endswith("." + role) and not k.startswith("header."):
            return k
    if role in ROLE_MARKER:
        for other in ("skill", "language", "interest", "bullet"):
            for k in styles:
                if k.endswith("." + other) and not k.startswith("header."):
                    return k
    fallback = {
        "item_subtitle": "item_dates",
        "item_location": "item_dates",
        "item_text": "bullet",
        "subsection_title": "item_title",
        "summary": "item_text",
        "item_dates": "item_title",
    }.get(role)
    if fallback and fallback != role:
        return style_key(styles, kind, fallback)
    return "section_title"


def deco_css(d: Json, left: float, top: float, extra: str = "") -> str:
    """Le CSS d'un ornement (filet, pastille, cadre)."""
    base = f"position:absolute;left:{left:.2f}mm;top:{top:.2f}mm;"
    sw = f"{d.get('stroke_pt') or 0.75}pt"
    color = d.get("stroke") or d.get("fill")
    if d["kind"] == "vline":
        return base + f"width:0;height:{d['h_mm']}mm;border-left:{sw} solid {color};{extra}"
    if d["kind"] == "hline":
        return base + f"height:0;width:{d['w_mm']}mm;border-top:{sw} solid {color};{extra}"
    round_ = d["kind"] == "curve" and abs(d["w_mm"] - d["h_mm"]) < 0.5  # noqa: PLR2004
    radius = "50%" if round_ else ("0" if d["kind"] == "rect" else "30%")
    fill = d.get("fill") or "transparent"
    border = f"border:{sw} solid {d['stroke']};box-sizing:border-box;" if d.get("stroke") else ""
    return (
        base
        + f"width:{d['w_mm']}mm;height:{d['h_mm']}mm;background:{fill};border-radius:{radius};{border}{extra}"
    )


def placement(spec: Json, view: Json, fonts: FontSet) -> list[Json]:
    """Chaque rubrique de la vue à sa place dans le modèle.

    Une rubrique absente du modèle emprunte le gabarit d'une rubrique sœur
    (rôles renommés, écart usuel entre rubriques) et va dans la colonne **la
    plus courte** de sa bande, d'après une pré-mise en page estimée avec les
    métriques réelles des polices. Partagé par les deux moteurs.
    """
    styles = spec["styles"]
    spec_secs = {
        s["kind"]: (bi, ci, s)
        for bi, b in enumerate(spec["bands"])
        for ci, c in enumerate(b["columns"])
        for s in c["sections"]
    }
    bands: list[Json] = [{"columns": [[] for _ in b["columns"]], "spec": b} for b in spec["bands"]]
    for sec in view["sections"]:
        if sec["kind"] in spec_secs:
            bi, ci, s = spec_secs[sec["kind"]]
            bands[bi]["columns"][ci].append((s, sec, False))
            continue
        for sib in SIBLINGS.get(sec["kind"], []):
            if sib not in spec_secs:
                continue
            bi, ci, s = spec_secs[sib]
            src_role, dst_role = LIST_ROLE.get(sib), LIST_ROLE.get(sec["kind"])
            slots = [
                {
                    **sl,
                    "key": sl["key"].replace(src_role, dst_role)
                    if src_role and dst_role
                    else sl["key"],
                    "roles": [dst_role if r == src_role and dst_role else r for r in sl["roles"]],
                }
                for sl in s["slots"]
            ]
            gaps = [
                x["margin_top_mm"]
                for b in spec["bands"]
                for c in b["columns"]
                for x in c["sections"][1:]
            ]
            clone = {
                **s,
                "kind": sec["kind"],
                "slots": slots,
                "slot_order": [sl["key"] for sl in slots],
                "margin_top_mm": max(gaps) if gaps else 10.0,
                "decorations": [d for d in s["decorations"] if d.get("anchor") != "after"],
            }
            bands[bi]["columns"][ci].append((clone, sec, True))
            break
    order = {k: i for i, k in enumerate(spec_secs)}
    for b in bands:
        for col in b["columns"]:
            col.sort(key=lambda t: (t[2], order.get(t[0]["kind"], 99)))
    for b in bands:
        if len(b["columns"]) < 2:  # noqa: PLR2004
            continue
        borrowed = [(ci, t) for ci, col in enumerate(b["columns"]) for t in col if t[2]]
        for ci, t in borrowed:
            b["columns"][ci].remove(t)
        for _, t in borrowed:
            heights = [
                sum(
                    estimate(styles, s, sec, b["spec"]["columns"][i]["w_mm"], fonts)
                    for s, sec, _ in col
                )
                for i, col in enumerate(b["columns"])
            ]
            target = min(range(len(heights)), key=heights.__getitem__)
            b["columns"][target].append(t)
    return bands


def estimate(styles: dict[str, Json], s: Json, sec: Json, col_w: float, fonts: FontSet) -> float:
    """Hauteur estimée d'une rubrique (mm), sans navigateur."""
    kind = sec["kind"]
    tst = styles["section_title"]
    title_lines = max(1, -(-fonts.width_mm(sec["title"], tst) // (s.get("title_w_mm") or col_w)))
    h = s["margin_top_mm"] + lh(tst) * title_lines
    slots = {sl["key"]: sl for sl in s["slots"]}
    for i, item in enumerate(sec["items"]):
        h += s["first_gap_mm"] if i == 0 else max(s["item_gap_mm"], 1.0)
        for key in s["slot_order"]:
            sl = slots[key]
            for role in sl["roles"]:
                val = item.get(role)
                vals = val if isinstance(val, list) else ([val] if val else [])
                st = styles[style_key(styles, kind, role)]
                width = col_w - sl["dx_mm"]
                for v in vals:
                    lines = max(1, -(-fonts.width_mm(v, st) // max(width, 10)))
                    h += sl["margin_top_mm"] + lh(st) * lines
                if len(sl["roles"]) > 1:
                    break
    return float(h)


def extra_contact_slots(s: Json, sec: Json) -> tuple[dict[str, Json], list[str]]:
    """Une coordonnée sans emplacement (site, LinkedIn) : une ligne de plus, au style de la dernière."""
    slots = {sl["key"]: sl for sl in s["slots"]}
    order = list(s["slot_order"])
    if sec["kind"] == "contact" and order:
        last = slots[order[-1]]
        for role in ("phone", "email", "address", "website"):
            if role not in slots and any(it.get(role) for it in sec["items"]):
                slots[role] = {**last, "key": role, "roles": [role], "repeat_margin_mm": None}
                order.append(role)
    return slots, order


class HtmlRenderer:
    """Rend une vue dans un modèle, en HTML imprimable."""

    def __init__(self, spec: Json, view: Json, fonts: FontSet, *, photo: str | None) -> None:
        """
        Args:
            photo: Nom du fichier de la photo joint à la requête, ou `None`.
        """
        self.spec, self.view, self.fonts, self.photo = spec, view, fonts, photo
        self.styles: dict[str, Json] = spec["styles"]
        counts: dict[str, int] = {}
        for v in self.styles.values():
            counts[v["family"]] = counts.get(v["family"], 0) + 1
        self.main_family = max(counts, key=lambda k: counts[k]) if counts else "sans-serif"

    def css_style(self, name: str, st: Json) -> str:
        """Une classe CSS par style de la spécification."""
        f = "" if name.startswith("header.") else "*var(--f)"
        return (
            f".s-{name.replace('.', '-')}{{font-family:'{st['family']}','{self.main_family}',sans-serif;"
            f"font-weight:{st['weight']};font-style:{'italic' if st['italic'] else 'normal'};"
            f"font-size:calc({st['size_pt']}pt{f});color:{st['color']};"
            f"letter-spacing:{st['letter_spacing_pt']}pt;line-height:calc({lh(st):.3f}mm{f});"
            f"text-transform:{'uppercase' if st['uppercase'] else 'none'}}}"
        )

    def font_faces(self) -> str:
        """`@font-face` des instances statiques servies (une variable sortirait en Type3)."""
        rules = [
            f"@font-face{{font-family:'{f.family}';src:url('{f.file_name}');"
            f"font-weight:{f.weight};font-style:{'italic' if f.italic else 'normal'}}}"
            for f in self.fonts.files
        ]
        return "".join(rules)

    def text(
        self,
        key: str,
        value: str,
        *,
        margin: float,
        dx: float,
        width: float,
        align: str = "left",
        extra: str = "",
    ) -> str:
        """Un bloc de texte positionné (ouvert : l'appelant ajoute puces et ornements puis ferme)."""
        return (
            f'<div class="t s-{key.replace(".", "-")}" style="margin-top:calc({margin:.2f}mm*var(--k));'
            f'margin-left:{dx:.2f}mm;width:{width:.2f}mm;text-align:{align};position:relative{extra}">{esc(value)}'
        )

    def section(  # noqa: PLR0912, PLR0915 - le gabarit d'une rubrique, dans l'ordre du modèle
        self, s: Json, sec: Json, col_w: float
    ) -> str:
        """Une rubrique : titre, éléments, puces, ornements ancrés."""
        kind = sec["kind"]
        tst = self.styles["section_title"]
        margin = s["margin_top_mm"] - half(tst)
        tw = s.get("title_w_mm") or col_w
        out = [f'<section style="position:relative;margin-top:calc({margin:.2f}mm*var(--k))">']
        nowrap = "" if s.get("title_w_mm") else ";white-space:nowrap"
        if s.get("title_pitch_mm"):
            # Interligne mesuré du titre sur plusieurs lignes. Le demi-blanc qu'il
            # ajoute au-dessus de la première ligne est repris par un décalage
            # visuel (`top`), et le blanc sous la dernière par une marge négative :
            # la première ligne et la suite de la rubrique restent à leur place.
            extra_lead = (s["title_pitch_mm"] - lh(tst)) / 2
            nowrap += f";line-height:{s['title_pitch_mm']:.3f}mm;top:{-extra_lead:.2f}mm;margin-bottom:{-2 * extra_lead:.2f}mm"
        out.append(
            self.text(
                "section_title", sec["title"], margin=0, dx=s["title_dx_mm"], width=tw, extra=nowrap
            )
            + "</div>"
        )
        prev_half = half(tst)
        slots, order = extra_contact_slots(s, sec)
        for i, item in enumerate(sec["items"]):
            gap = s["first_gap_mm"] if i == 0 else s["item_gap_mm"]
            if i and kind in LIST_KINDS and gap < 1.0:
                gap = s["first_gap_mm"]  # deux catégories de liste : l'écart titre → liste
            rows: list[tuple[Json, list[tuple[str, str]], float]] = []
            for key in order:
                sl = slots[key]
                roles = sl["roles"]
                if len(roles) == 1:
                    val = item.get(roles[0])
                    vals = val if isinstance(val, list) else ([val] if val else [])
                    for j, v in enumerate(vals):
                        repeat = (
                            sl["repeat_margin_mm"]
                            if sl["repeat_margin_mm"] is not None
                            else sl["margin_top_mm"]
                        )
                        rows.append(
                            (sl, [(roles[0], v)], sl["margin_top_mm"] if j == 0 else repeat)
                        )
                else:
                    pairs = [(r, item.get(r)) for r in roles if item.get(r)]
                    if pairs:
                        rows.append((sl, pairs, sl["margin_top_mm"]))
            if not rows:
                continue
            first_st = self.styles[style_key(self.styles, kind, rows[0][1][0][0])]
            item_margin = gap - prev_half - half(first_st)
            out.append(
                f'<div class="item" style="position:relative;margin-top:calc({item_margin:.2f}mm*var(--k))">'
            )
            last_item = i == len(sec["items"]) - 1
            for d in s["decorations"]:
                if d["anchor"] != "item" or d.get("attach") == "row":
                    continue
                inset = d.get("bottom_inset_mm", 0.0)
                if d.get("connector") and last_item:
                    # Le dernier élément : son propre trait (plus court) s'il en a un.
                    if d.get("last_bottom_inset_mm") is None:
                        continue
                    inset = d["last_bottom_inset_mm"]
                extra = f"height:auto;bottom:{inset:.2f}mm;" if d.get("stretch") else ""
                out.append(
                    f'<div style="{deco_css(d, d["dx_mm"], d["dy_mm"] + half(first_st), extra)}"></div>'
                )
            row_decor = {d["ref_role"]: d for d in s["decorations"] if d.get("attach") == "row"}
            done_roles: set[str] = set()
            last_half = half(first_st)
            for r_i, (sl, vals, m) in enumerate(rows):
                key0 = style_key(self.styles, kind, vals[0][0])
                st0 = self.styles[key0]
                margin_row = 0.0 if r_i == 0 else m - last_half - half(st0)
                width = col_w - sl["dx_mm"]
                if len(vals) == 1:
                    role, v = vals[0]
                    out.append(
                        self.text(
                            key0,
                            v,
                            margin=margin_row,
                            dx=sl["dx_mm"],
                            width=width,
                            align=sl["align"],
                            extra=";white-space:nowrap" if role == "item_dates" else "",
                        )
                        + self.marker(s, role, st0)
                        + self.icon(s, kind, role, st0)
                        + self.row_ornament(row_decor, done_roles, role, sl)
                        + "</div>"
                    )
                else:
                    # Une pastille de frise peut marquer un champ d'une ligne
                    # composée (« Poste | 2016 ») : elle se pose dans cette ligne.
                    ornament = "".join(
                        self.row_ornament(row_decor, done_roles, role, sl) for role, _ in vals
                    )
                    row = self.joined_row(kind, sl, vals, margin=margin_row, width=width, st0=st0)
                    out.append(row[: -len("</div>")] + ornament + "</div>")
                last_half = half(st0)
            out.append("</div>")
            prev_half = last_half
        for d in s["decorations"]:
            if d["anchor"] == "section":
                # Un filet étiré ne déborde pas de plus de 2 mm sur la rubrique suivante.
                extra = (
                    f"height:auto;bottom:{max(d['bottom_inset_mm'], -2.0):.2f}mm;"
                    if d.get("stretch")
                    else ""
                )
                out.append(
                    f'<div style="{deco_css(d, d["dx_mm"], d["dy_mm"] + half(tst), extra)}"></div>'
                )
        out.append("</section>")
        out.extend(
            f'<div style="position:relative;height:0"><div style="{deco_css(d, d["dx_mm"], d["dy_mm"] + prev_half)}"></div></div>'
            for d in s["decorations"]
            if d["anchor"] == "after"
        )
        return "".join(out)

    def marker(self, s: Json, role: str, st: Json) -> str:
        """La puce d'une ligne de liste."""
        mk = s.get("marker")
        if role not in ROLE_MARKER or not mk:
            return ""
        top = mk["dy_mm"] - mk["size_mm"] / 2 + half(st)
        radius = "50%" if mk["shape"] == "circle" else "0"
        return (
            f'<span style="position:absolute;left:{mk["dx_mm"]:.2f}mm;top:{top:.2f}mm;width:{mk["size_mm"]}mm;'
            f'height:{mk["size_mm"]}mm;border-radius:{radius};background:{mk["color"]}"></span>'
        )

    def icon(self, s: Json, kind: str, role: str, st: Json) -> str:
        """Le pictogramme d'une coordonnée."""
        if kind != "contact" or role not in s.get("icons", {}):
            return ""
        ic = s["icons"][role]
        return (
            f'<img src="{ic["file"]}" alt="" style="position:absolute;left:{ic["dx_mm"]:.2f}mm;'
            f'top:{ic["dy_mm"] + half(st):.2f}mm;width:{ic["w_mm"]}mm;height:{ic["h_mm"]}mm">'
        )

    @staticmethod
    def row_ornament(row_decor: dict[str, Json], done: set[str], role: str, sl: Json) -> str:
        """Une pastille de frise centrée sur la ligne qu'elle marque."""
        if role not in row_decor or role in done:
            return ""
        d = row_decor[role]
        done.add(role)
        shift = d["h_mm"] / 2 - d["center_offset_mm"]
        return (
            '<div style="'
            + deco_css(d, d["dx_mm"] - sl["dx_mm"], 0, f"top:calc(50% - {shift:.2f}mm);")
            + '"></div>'
        )

    def joined_row(
        self,
        kind: str,
        sl: Json,
        vals: list[tuple[str, str]],
        *,
        margin: float,
        width: float,
        st0: Json,
    ) -> str:
        """Plusieurs champs sur une ligne (« Entreprise | 2016 »), séparés comme dans le modèle."""
        gapx = sl["gap_mm"] or 3.0
        sep = sl.get("separator")
        parts = []
        for n, (role, v) in enumerate(vals):
            if n:
                if sep:
                    parts.append(
                        f'<span style="display:inline-block;align-self:flex-start;margin:{sep["dy_mm"] + half(st0):.2f}mm '
                        f"{sep['right_mm']:.2f}mm 0 {sep['left_mm']:.2f}mm;height:{sep['h_mm']}mm;"
                        f'border-left:{sep["stroke_pt"]}pt solid {sep["stroke"]}"></span>'
                    )
                else:
                    parts.append(f'<span style="display:inline-block;width:{gapx:.2f}mm"></span>')
            parts.append(
                f'<span class="s-{style_key(self.styles, kind, role).replace(".", "-")}">{esc(v)}</span>'
            )
        return (
            f'<div class="t" style="margin-top:calc({margin:.2f}mm*var(--k));margin-left:{sl["dx_mm"]:.2f}mm;'
            f"width:{width:.2f}mm;display:flex;align-items:baseline;white-space:nowrap;"
            f'line-height:{lh(st0):.3f}mm;position:relative">{"".join(parts)}</div>'
        )

    def header_limit(self, e: Json, st: Json) -> float:
        """Place disponible pour un élément d'en-tête : jusqu'à la photo, au voisin ou à la marge."""
        h = self.spec["header"]
        p_box = h.get("photo") if self.photo else None
        pw = self.spec["page"]["width_mm"]
        margin = min(c["x_mm"] for b in self.spec["bands"] for c in b["columns"])
        limit = pw - margin
        if (
            p_box
            and e["y_mm"] < p_box["y_mm"] + p_box["h_mm"]
            and e["y_mm"] + st["box_h_mm"] > p_box["y_mm"]
            and e["x_mm"] < p_box["x_mm"]
        ):
            limit = p_box["x_mm"] - 4.0
        for other in h["elements"]:
            if (
                other is not e
                and other["x_mm"] > e["x_mm"] + 5
                and abs(other["y_mm"] - e["y_mm"]) < st["box_h_mm"] + 2
            ) and self.view["header"].get(other["field"]):
                limit = min(limit, other["x_mm"] - 4.0)
        return float(limit - e["x_mm"])

    def fit_header_text(self, val: str, st: Json, avail: float) -> str:
        """Texte trop long dans l'en-tête : interlettrage resserré, puis corps réduit, puis retour à la ligne."""
        if self.fonts.width_mm(val, st) <= avail:
            return ""
        ls = float(st["letter_spacing_pt"])
        for k in (0.8, 0.6, 0.45, 0.3):
            if self.fonts.width_mm(val, st, ls_pt=ls * k) <= avail:
                return f";letter-spacing:{ls * k:.2f}pt"
        size = float(st["size_pt"])
        for k in (0.95, 0.9, 0.85, 0.8):
            if self.fonts.width_mm(val, st, size_pt=size * k, ls_pt=ls * 0.3) <= avail:
                return f";letter-spacing:{ls * 0.3:.2f}pt;font-size:{size * k:.2f}pt"
        return f";letter-spacing:{ls * 0.3:.2f}pt;white-space:normal;width:{avail:.2f}mm"

    def header(self) -> str:
        """L'en-tête : ornements d'abord (ordre de peinture de la source), textes, photo."""
        h = self.spec["header"]
        out = [f'<div style="{deco_css(d, d["x_mm"], d["y_mm"])}"></div>' for d in h["decorations"]]
        for e in h["elements"]:
            val = self.view["header"].get(e["field"], "")
            if not val:
                continue
            st = self.styles[e["style"]]
            fit_css = self.fit_header_text(val, st, self.header_limit(e, st))
            icon = ""
            if e.get("icon"):
                ic = e["icon"]
                icon = (
                    f'<img src="{ic["file"]}" alt="" style="position:absolute;left:{ic["dx_mm"]:.2f}mm;'
                    f'top:{ic["dy_mm"] + half(st):.2f}mm;width:{ic["w_mm"]}mm;height:{ic["h_mm"]}mm">'
                )
            left = f"left:{e['x_mm']}mm"
            if e.get("align") == "center":
                left = f"left:{e['cx_mm']}mm;transform:translateX(-50%)"
            out.append(
                f'<div class="s-{e["style"].replace(".", "-")}" style="position:absolute;{left};'
                f'top:{e["y_mm"] - half(st):.2f}mm;white-space:nowrap{fit_css}">{esc(val)}{icon}</div>'
            )
        p = h.get("photo")
        if p and self.photo:
            radius = {"circle": "50%", "rounded": "8%"}.get(p["shape"], "0")
            pos = p["sample_object_position"] if self.view.get("sample") else "50% 30%"
            out.append(
                f'<div style="position:absolute;left:{p["x_mm"]}mm;top:{p["y_mm"]}mm;width:{p["w_mm"]}mm;'
                f'height:{p["h_mm"]}mm;border-radius:{radius};overflow:hidden">'
                f'<img src="{self.photo}" alt="" style="width:100%;height:100%;object-fit:cover;object-position:{pos}"></div>'
            )
        return "".join(out)

    def render(self) -> str:
        """Le document HTML complet."""
        page = self.spec["page"]
        css = [
            self.font_faces(),
            f"@page{{size:{page['width_mm']}mm {page['height_mm']}mm;margin:0}}",
            "html,body{margin:0;padding:0}*{box-sizing:border-box}.t{white-space:normal;overflow-wrap:anywhere}",
            ":root{--k:1;--f:1}",
            f".page{{position:relative;width:{page['width_mm']}mm;height:{page['height_mm']}mm;overflow:hidden;background:{page['background']}}}",
            "section,.item{display:block}",
            *(self.css_style(k, v) for k, v in self.styles.items()),
        ]
        bands_html = []
        for bi, band in enumerate(placement(self.spec, self.view, self.fonts)):
            if not any(band["columns"]):
                continue
            cols = []
            prev_right = 0.0
            for ci, col in enumerate(band["columns"]):
                cspec = band["spec"]["columns"][ci]
                inner = "".join(self.section(s, sec, cspec["w_mm"]) for s, sec, _ in col)
                cols.append(
                    f'<div class="col" style="margin-left:{cspec["x_mm"] - prev_right:.2f}mm;width:{cspec["w_mm"]:.2f}mm;flex:none">{inner}</div>'
                )
                prev_right = cspec["x_mm"] + cspec["w_mm"]
            margin = 0.0 if bi == 0 else self.spec["bands"][bi - 1].get("gap_after_mm", 8.0)
            bands_html.append(
                f'<div class="band" style="display:flex;align-items:flex-start;margin-top:calc({margin:.2f}mm*var(--k))">{"".join(cols)}</div>'
            )
            bands_html.extend(
                f'<div style="position:relative;height:0"><div style="{deco_css(d, d["x_mm"], d["dy_mm"])}"></div></div>'
                for d in self.spec["page_decorations"]
                if d.get("anchor") == "band" and d["band"] == bi
            )
        statics = "".join(
            f'<div style="{deco_css(d, d["x_mm"], d["y_mm"])}"></div>'
            for d in self.spec["page_decorations"]
            if d.get("anchor") == "page"
        )
        tst = self.styles["section_title"]
        first_margin = (
            self.spec["bands"][0]["columns"][0]["sections"][0]["margin_top_mm"]
            if self.spec["bands"][0]["columns"][0]["sections"]
            else 0.0
        )
        top = self.spec["first_band_top_mm"] - half(tst) - (first_margin - half(tst))
        ph = page["height_mm"]
        limit = ph - max((ph - self.spec["content_bottom_mm"]) * 0.6, 6.0)
        k_steps = ",".join(str(k) for k in SPACING_STEPS)
        f_steps = ",".join(str(f) for f in FONT_STEPS)
        fit = (
            "<script>document.fonts.ready.then(function(){var r=document.documentElement,"
            "p=document.querySelector('.page'),mm=96/25.4;"
            f"var lim={limit:.2f}*mm;"
            "function b(){var t=p.getBoundingClientRect().top,m=0;document.querySelectorAll('.t').forEach("
            "function(e){m=Math.max(m,e.getBoundingClientRect().bottom-t)});return m}"
            f"var K=[{k_steps}],F=[{f_steps}],k=1,f=1,ok=false;"
            "for(var i=0;i<K.length&&!ok;i++){k=K[i];r.style.setProperty('--k',k);ok=b()<=lim}"
            "for(var j=1;j<F.length&&!ok;j++){f=F[j];r.style.setProperty('--f',f);ok=b()<=lim}"
            "if(!ok){p.style.overflow='visible';p.style.height='auto'}"
            "document.title='fit k='+k+' f='+f+' pages='+(ok?1:2);window.fitDone=true});</script>"
        )
        return (
            f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>CV</title><style>{"".join(css)}</style></head>'
            f'<body><div class="page">{statics}{self.header()}<div class="flow" style="position:absolute;left:0;top:{top:.2f}mm;width:100%">'
            f"{''.join(bands_html)}</div></div>{fit}</body></html>"
        )


def parse_fit(title: str) -> tuple[float, float, int]:
    """`fit k=0.8 f=1 pages=1` → (0,8 ; 1,0 ; 1). Valeurs neutres si le titre est absent."""
    values = (
        dict(part.split("=", 1) for part in title.split()[1:] if "=" in part)
        if title.startswith("fit")
        else {}
    )
    try:
        return float(values.get("k", 1)), float(values.get("f", 1)), int(values.get("pages", 1))
    except ValueError:
        return 1.0, 1.0, 1
