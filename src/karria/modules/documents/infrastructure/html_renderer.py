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
  les pages suivantes sont assumées (la feuille prend `n × hauteur`, Chromium
  coupe aux pages). L'état retenu part dans le titre du document
  (`fit k=… f=… pages=…`), relu après conversion pour que le DOCX applique les
  mêmes facteurs, et dans `window.karriaFit` pour l'app. Aucun mot n'est réécrit.
- **Deux usages, un moteur** (ADR-0029) : pour Gotenberg, polices et images
  sont jointes et référencées par leur nom de fichier ; pour l'atelier de
  l'app, `url_for` traduit chaque nom en adresse (police publique, `data:`,
  URL signée) et `anchored=True` écrit sur chaque texte du CV l'attribut
  `data-chemin` que la vue a fourni. Le rendu imprimé n'a pas d'ancres et
  n'a pas de filet de coupure entre les pages ; le rendu de l'atelier a les deux.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Callable
from typing import Any, Final

from karria.modules.documents.domain.view import header_path, item_path
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.footer import footer_html

Json = dict[str, Any]

LIST_ROLE: Final = {"skills": "skill", "languages": "language", "interests": "interest"}
ROLE_MARKER: Final = frozenset({"bullet", "skill", "language", "interest"})
CONTACT_ROLES: Final = ("phone", "email", "address", "website")
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
#: Ce que l'ajustement sait absorber avant d'ouvrir une page (espacements
#: ×0,6, corps ×0,88) : la part de la hauteur estimée qui peut disparaître.
FIT_SLACK: Final = 0.78
#: Couleur du filet qui marque une coupure de page dans l'atelier.
PAGE_BREAK_COLOR: Final = "#cbd5e1"
#: Réductions de corps d'un champ d'en-tête trop long, avant le retour à la ligne.
FIELD_SIZE_STEPS: Final = (0.95, 0.9, 0.85, 0.8)
#: Un nom se réduit davantage : replié, il écraserait ce qui est sous lui.
NAME_SIZE_STEPS: Final = (0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5, 0.45, 0.4, 0.35)
NAME_FIELDS: Final = frozenset({"first_name", "last_name", "full_name"})


def identity_url(name: str) -> str:
    """Le résolveur par défaut : un fichier joint se référence par son nom."""
    return name


_CLASS_UNSAFE: Final = re.compile(r"[^a-z0-9-]+")


def css_class(key: str) -> str:
    """Le nom de classe d'un style : `s-` puis des lettres, chiffres et tirets seulement.

    Une clé de style vient de la spécification (`experiences.bullet`,
    `other:realisations-cles.bullet`) : « . » et « : » ne sont pas des
    caractères de classe (« : » ouvre une pseudo-classe), et rien de ce qui
    vient d'un plan de modèle n'arrive tel quel dans le HTML.
    """
    return "s-" + _CLASS_UNSAFE.sub("-", key.lower()).strip("-")


def anchor(path: str | None) -> str:
    """L'attribut `data-chemin` d'un texte, ou rien."""
    return f' data-chemin="{esc(path)}"' if path else ""


def page_limit_mm(spec: Json) -> float:
    """Jusqu'où le texte peut descendre sur la première page (mm)."""
    ph = float(spec["page"]["height_mm"])
    return ph - max((ph - float(spec["content_bottom_mm"])) * 0.6, 6.0)


def fit_script(spec: Json, *, live: bool) -> str:
    """Le script d'ajustement : espacements, corps, puis pages assumées.

    `live` (l'atelier) ajoute un filet à chaque coupure de page ; l'impression
    n'en a pas. Dans les deux cas, le titre porte l'état et `window.karriaFit`
    le répète pour l'app (ADR-0029).
    """
    ph = float(spec["page"]["height_mm"])
    # Le haut des pages suivantes : une marge de lecture, pas la réserve de
    # l'en-tête de la première (souvent 50 mm et plus).
    top_mm = min(float(spec.get("first_band_top_mm", 12.0)), 16.0)
    k_steps = ",".join(str(k) for k in SPACING_STEPS)
    f_steps = ",".join(str(f) for f in FONT_STEPS)
    guides = (
        "for(var g=1;g<n;g++){var d=document.createElement('div');"
        f"d.className='pb';d.style.cssText='position:absolute;left:0;width:100%;height:0;top:'+(g*{ph})+'mm;"
        f"border-top:2px dashed {PAGE_BREAK_COLOR};pointer-events:none';"
        # L'étiquette « Page 2 » sur le trait : la coupure se voit, elle ne se devine pas.
        "var l=document.createElement('span');l.textContent='Page '+(g+1);"
        f"l.style.cssText='position:absolute;right:4mm;top:1mm;font:600 8pt sans-serif;color:{PAGE_BREAK_COLOR};"
        "background:#fff;padding:1px 6px;border-radius:8px;';d.appendChild(l);p.appendChild(d)}}"
        if live
        else ""
    )
    return (
        "<script>document.fonts.ready.then(function(){var r=document.documentElement,"
        "p=document.querySelector('.page'),mm=96/25.4;"
        f"var lim={page_limit_mm(spec):.2f}*mm,ph={ph}*mm;"
        "function b(){var t=p.getBoundingClientRect().top,m=0;document.querySelectorAll('.t').forEach("
        "function(e){m=Math.max(m,e.getBoundingClientRect().bottom-t)});return m}"
        f"var K=[{k_steps}],F=[{f_steps}],k=1,f=1,ok=false,n=1;"
        "for(var i=0;i<K.length&&!ok;i++){k=K[i];r.style.setProperty('--k',k);ok=b()<=lim}"
        "for(var j=1;j<F.length&&!ok;j++){f=F[j];r.style.setProperty('--f',f);ok=b()<=lim}"
        # Rien ne tient sur une page : les pages suivantes sont assumées, à
        # l'espacement et au corps naturels, plutôt qu'un CV comprimé **et**
        # débordant — et la seconde page est **propre** (Marc, 13/11) : aucun
        # élément ni titre coupé par la pliure, un haut de page à la marge de
        # la première, les ornements pleine hauteur prolongés. La feuille prend
        # n hauteurs de page : Chromium coupe là.
        "if(!ok){k=1;f=1;r.style.setProperty('--k',1);r.style.setProperty('--f',1);"
        f"var top={top_mm}*mm,pt=p.getBoundingClientRect().top;"
        # Un élément (item, ou titre hors item) qui chevauche une pliure descend
        # sous elle, à la marge haute ; un titre collé à la pliure suit son premier item.
        "function sh(e){var q=e.getBoundingClientRect();return{t:q.top-pt,b:q.bottom-pt}}"
        "function push(e,y){var q=sh(e);e.style.marginTop=(parseFloat(getComputedStyle(e).marginTop)||0)+(y-q.t)+'px'}"
        "document.querySelectorAll('.col').forEach(function(c){"
        "var els=Array.prototype.filter.call(c.querySelectorAll('.item,.t'),function(e){return e.classList.contains('item')||!e.closest('.item')});"
        "for(var i=0;i<els.length;i++){var e=els[i],q=sh(e);if(q.b-q.t>ph*0.8)continue;"
        "var g=Math.floor(q.t/ph),cut=(g+1)*ph;if(q.b>cut){"
        "var prev=els[i-1];if(prev&&!prev.classList.contains('item')&&!prev.closest('.item')&&sh(prev).b>cut-12*mm){push(prev,cut+top);i--;continue}"
        "push(e,cut+top)}}});"
        f"n=Math.max(2,Math.ceil(b()/ph));p.style.height=(n*{ph})+'mm';"
        # Les ornements qui couvraient toute la première page couvrent toutes les pages.
        "Array.prototype.forEach.call(p.children,function(e){if(e.classList.contains('band')||e.classList.contains('pb'))return;"
        f"var q=e.getBoundingClientRect();if(q.height>=ph*0.85&&getComputedStyle(e).position==='absolute'){{e.style.height=(n*{ph})+'mm'}}}});"
        f"{guides}}}"
        "document.title='fit k='+k+' f='+f+' pages='+n;"
        "window.karriaFit={k:k,f:f,pages:n};window.fitDone=true});</script>"
    )


def estimate_pages(spec: Json, view: Json, fonts: FontSet) -> int:
    """Le nombre de pages **estimé** sans navigateur (ADR-0029 § 4).

    Même placement et mêmes métriques que la pré-mise en page des colonnes ;
    l'ajustement (espacements, corps) est compté comme une marge de 22 %. La
    vérité vient du script d'ajustement dans la WebView (`window.karriaFit`).
    """
    styles = spec["styles"]
    total = float(spec["first_band_top_mm"])
    for bi, band in enumerate(placement(spec, view, fonts)):
        if not any(band["columns"]):
            continue
        heights = [
            sum(
                estimate(styles, s, sec, band["spec"]["columns"][ci]["w_mm"], fonts)
                for s, sec, _ in col
            )
            for ci, col in enumerate(band["columns"])
        ]
        total += max(heights, default=0.0)
        if bi:
            total += float(spec["bands"][bi - 1].get("gap_after_mm", 8.0))
    limit = page_limit_mm(spec)
    if total * FIT_SLACK <= limit:
        return 1
    usable = max(limit - float(spec["first_band_top_mm"]), 1.0)
    return max(2, math.ceil((total - float(spec["first_band_top_mm"])) / usable))


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
    if role in CONTACT_ROLES:
        # Une coordonnée sans emplacement (le site, LinkedIn) prend le style
        # d'une coordonnée voisine, jamais celui d'un titre.
        for prefix in (kind, "contact", "header"):
            for other in CONTACT_ROLES:
                if f"{prefix}.{other}" in styles:
                    return f"{prefix}.{other}"
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


def fit_text_css(
    fonts: FontSet,
    val: str,
    st: Json,
    avail: float,
    *,
    steps: tuple[float, ...] = FIELD_SIZE_STEPS,
    shrink_wrapped: bool = False,
) -> str:
    """Texte trop long pour sa place : interlettrage resserré, puis corps réduit, puis retour à la ligne.

    Partagé par l'en-tête du CV et les blocs d'en-tête d'une lettre (ADR-0036) :
    décidé sans navigateur, avec les métriques réelles de la police servie.
    `steps` : les corps essayés (un nom de CV descend jusqu'à 35 %, ADR-0037) ;
    `shrink_wrapped` : replié, le texte garde le dernier corps essayé (le CV) plutôt
    que le sien (la lettre).
    """
    if fonts.width_mm(val, st) <= avail:
        return ""
    ls = float(st["letter_spacing_pt"])
    for k in (0.8, 0.6, 0.45, 0.3):
        if fonts.width_mm(val, st, ls_pt=ls * k) <= avail:
            return f";letter-spacing:{ls * k:.2f}pt"
    size = float(st["size_pt"])
    for k in steps:
        if fonts.width_mm(val, st, size_pt=size * k, ls_pt=ls * 0.3) <= avail:
            return f";letter-spacing:{ls * 0.3:.2f}pt;font-size:{size * k:.2f}pt"
    shrunk = f"font-size:{size * steps[-1]:.2f}pt;" if shrink_wrapped else ""
    return f";letter-spacing:{ls * 0.3:.2f}pt;{shrunk}white-space:normal;width:{avail:.2f}mm"


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
    # Des coordonnées déjà dans l'en-tête ne se répètent pas dans une rubrique empruntée.
    header_contact = any(e["field"] in CONTACT_ROLES for e in spec["header"]["elements"])
    for sec in view["sections"]:
        if sec["kind"] in spec_secs:
            bi, ci, s = spec_secs[sec["kind"]]
            bands[bi]["columns"][ci].append((s, sec, False))
            continue
        if sec["kind"] == "contact" and header_contact:
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
    tst = styles[s.get("title_style") or "section_title"]
    title_lines = max(1, -(-fonts.width_mm(sec["title"], tst) // (s.get("title_w_mm") or col_w)))
    h = s["margin_top_mm"] + (0.0 if s.get("untitled") else lh(tst) * title_lines)
    label_st = styles.get(f"{kind}.label")
    slots = {sl["key"]: sl for sl in s["slots"]}
    side = s.get("side")
    side_slots = [(sl, side["w_mm"]) for sl in side["slots"]] if side else []
    box_w = min(col_w, float(s["width_mm"])) if s.get("width_mm") else col_w
    for i, item in enumerate(sec["items"]):
        h += s["first_gap_mm"] if i == 0 else max(s["item_gap_mm"], 1.0)
        rows = [(slots[key], box_w - slots[key]["dx_mm"]) for key in s["slot_order"]]
        # La colonne latérale d'une frise est comptée en plus : une estimation
        # haute, qui garde la page estimée du bon côté.
        for sl, width in rows + side_slots:
            for role in sl["roles"]:
                val = item.get(role)
                vals = val if isinstance(val, list) else ([val] if val else [])
                st = styles[style_key(styles, kind, role)]
                for v in vals:
                    lines = max(1, -(-fonts.width_mm(v, st) // max(width, 10)))
                    h += sl["margin_top_mm"] + lh(st) * lines
                    label = sl.get("label")
                    if label and not label["inline"] and label_st is not None:
                        h += lh(label_st)
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
                # Sans étiquette : celle du dernier emplacement (« Address ») ne vaut pas
                # pour le site.
                slots[role] = {
                    **last,
                    "key": role,
                    "roles": [role],
                    "repeat_margin_mm": None,
                    "label": None,
                }
                order.append(role)
    return slots, order


class HtmlRenderer:
    """Rend une vue dans un modèle, en HTML imprimable."""

    def __init__(
        self,
        spec: Json,
        view: Json,
        fonts: FontSet,
        *,
        photo: str | None,
        url_for: Callable[[str], str] = identity_url,
        anchored: bool = False,
    ) -> None:
        """
        Args:
            photo: Nom du fichier de la photo joint à la requête, ou `None`.
            url_for: Traduit un nom de fichier (police, pictogramme, photo) en
                adresse ; par défaut le nom lui-même, pour les fichiers joints.
            anchored: Écrit `data-chemin` sur chaque texte du CV (atelier).
        """
        self.spec, self.view, self.fonts, self.photo = spec, view, fonts, photo
        self.url, self.anchored = url_for, anchored
        self.styles: dict[str, Json] = spec["styles"]
        counts: dict[str, int] = {}
        for v in self.styles.values():
            counts[v["family"]] = counts.get(v["family"], 0) + 1
        self.main_family = max(counts, key=lambda k: counts[k]) if counts else "sans-serif"

    def css_style(self, name: str, st: Json) -> str:
        """Une classe CSS par style de la spécification."""
        f = "" if name.startswith("header.") else "*var(--f)"
        return (
            f".{css_class(name)}{{font-family:'{st['family']}','{self.main_family}',sans-serif;"
            f"font-weight:{st['weight']};font-style:{'italic' if st['italic'] else 'normal'};"
            f"font-size:calc({st['size_pt']}pt{f});color:{st['color']};"
            f"letter-spacing:{st['letter_spacing_pt']}pt;line-height:calc({lh(st):.3f}mm{f});"
            f"text-transform:{'uppercase' if st['uppercase'] else 'none'}"
            + (f";word-spacing:{st['word_spacing_mm']}mm" if st.get("word_spacing_mm") else "")
            + "}"
        )

    def font_faces(self) -> str:
        """`@font-face` des instances statiques servies (une variable sortirait en Type3)."""
        rules = [
            f"@font-face{{font-family:'{f.family}';src:url('{self.url(f.file_name)}');"
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
        path: str | None = None,
    ) -> str:
        """Un bloc de texte positionné (ouvert : l'appelant ajoute puces et ornements puis ferme)."""
        # L'exemple du modèle garde les retours à la ligne de la source (un
        # intitulé sur deux lignes) ; le texte d'une personne n'en a pas.
        breaks = ";white-space:pre-line" if "\n" in value and self.view.get("sample") else ""
        return (
            f'<div class="t {esc(css_class(key))}"{self.anchor(path)} style="margin-top:calc({margin:.2f}mm*var(--k));'
            f'margin-left:{dx:.2f}mm;width:{width:.2f}mm;text-align:{align};position:relative{extra}{breaks}">{esc(value)}'
        )

    def anchor(self, path: str | None) -> str:
        """`data-chemin` en mode ancré, rien sinon."""
        return anchor(path) if self.anchored else ""

    def section(  # noqa: PLR0912, PLR0915 - le gabarit d'une rubrique, dans l'ordre du modèle
        self, s: Json, sec: Json, col_w: float, *, reserve_mm: float = 0.0
    ) -> str:
        """Une rubrique : titre, éléments, puces, ornements ancrés.

        `reserve_mm` : la place que l'en-tête occupe au-dessus de la rubrique
        dans sa colonne (des coordonnées en absolu) ; l'ajustement ne resserre
        pas la marge sous cette réserve.
        """
        kind = sec["kind"]
        tst = self.styles[s.get("title_style") or "section_title"]
        untitled = bool(s.get("untitled"))
        # Sans titre (un profil, des coordonnées sous la photo) : la rubrique
        # commence à sa première ligne, sans demi-blanc de titre à reprendre.
        lead = 0.0 if untitled else half(tst)
        margin = s["margin_top_mm"] - lead
        top_css = f"calc({margin:.2f}mm*var(--k))"
        if reserve_mm > 0:
            top_css = f"max({top_css},{reserve_mm - lead:.2f}mm)"
        # La boîte mesurée d'un titre sur deux lignes vaut pour l'exemple ; le
        # titre d'une personne (« Expériences professionnelles ») prend la colonne.
        tw = (s.get("title_w_mm") if self.view.get("sample") else None) or col_w
        out = [f'<section style="position:relative;margin-top:{top_css}">']
        # Le titre d'une personne (« Centres d'intérêt » interlettré) se replie
        # dans sa colonne ; celui de l'exemple tient sur sa ligne mesurée.
        nowrap = "" if s.get("title_w_mm") or not self.view.get("sample") else ";white-space:nowrap"
        if not self.view.get("sample") and not s.get("title_w_mm"):
            # Un titre trop large pour sa colonne (« Compétences » interlettré en 17 pt
            # dans 38 mm) resserre son interlettrage, puis son corps, et ne se coupe
            # jamais au milieu d'un mot.
            nowrap += ";overflow-wrap:normal" + self.fit_header_text(sec["title"], tst, tw)
        if s.get("title_pitch_mm"):
            # Interligne mesuré du titre sur plusieurs lignes. Le demi-blanc qu'il
            # ajoute au-dessus de la première ligne est repris par un décalage
            # visuel (`top`), et le blanc sous la dernière par une marge négative :
            # la première ligne et la suite de la rubrique restent à leur place.
            extra_lead = (s["title_pitch_mm"] - lh(tst)) / 2
            nowrap += f";line-height:{s['title_pitch_mm']:.3f}mm;top:{-extra_lead:.2f}mm;margin-bottom:{-2 * extra_lead:.2f}mm"
        if not untitled:
            out.append(
                self.text(
                    s.get("title_style") or "section_title",
                    sec["title"],
                    margin=0,
                    dx=s["title_dx_mm"],
                    width=tw,
                    extra=nowrap,
                )
                + "</div>"
            )
        prev_half = 0.0 if untitled else half(tst)
        side = s.get("side")
        # La boîte de texte de la rubrique, si ses paragraphes repliés l'ont dite.
        box_w = min(col_w, float(s["width_mm"])) if s.get("width_mm") else col_w
        slots, order = extra_contact_slots(s, sec)
        # Des éléments côte à côte (deux références) : une grille au pas mesuré,
        # chaque élément dans sa colonne, les rangées à l'écart des éléments.
        grid_items = int(s.get("item_columns") or 0)
        item_box = min(box_w, float(s["item_pitch_mm"])) if grid_items else box_w
        if grid_items:
            # Le conteneur tient `item_columns` éléments au pas mesuré ; le dernier
            # peut dépasser la colonne de quelques millimètres (comme la source).
            grid_w = max(col_w, float(s["item_pitch_mm"]) * grid_items)
            out.append(
                f'<div style="display:flex;flex-wrap:wrap;align-items:flex-start;width:{grid_w:.2f}mm">'
            )
        row_prev_half = 0.0
        for i, item in enumerate(sec["items"]):
            gap = s["first_gap_mm"] if i < max(1, grid_items) else s["item_gap_mm"]
            if i and kind in LIST_KINDS and gap < 1.0:
                gap = s["first_gap_mm"]  # deux catégories de liste : l'écart titre → liste
            rows: list[tuple[Json, list[tuple[str, str, str | None]], float]] = []
            emitted: set[str] = set()
            for key in order:
                sl = slots[key]
                roles = sl["roles"]
                # Un rôle déjà posé par un emplacement composé (« Poste | 2016 »)
                # ne se répète pas par son emplacement seul (un élément sans dates).
                if all(r in emitted for r in roles):
                    continue
                emitted.update(r for r in roles if item.get(r))
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
                            (
                                sl,
                                [(roles[0], v, item_path(sec, i, roles[0], j))],
                                sl["margin_top_mm"] if j == 0 else repeat,
                            )
                        )
                else:
                    pairs = [
                        (r, v, item_path(sec, i, r))
                        for r in roles
                        if isinstance(v := item.get(r), str) and v
                    ]
                    if pairs:
                        rows.append((sl, pairs, sl["margin_top_mm"]))
            if not rows:
                continue
            first_st = self.styles[style_key(self.styles, kind, rows[0][1][0][0])]
            if not grid_items or i % grid_items == 0:
                row_prev_half = prev_half
            item_margin = gap - row_prev_half - half(first_st)
            # Avec une colonne latérale (frise), l'élément est une rangée : la
            # colonne latérale, une gouttière, le contenu ; sa hauteur est celle
            # du plus haut des deux, comme dans la source.
            origin = side["dx_mm"] if side else 0.0
            # Le contenu commence à `main_dx_mm` de la colonne (le titre de la
            # rubrique peut être plus à gauche que la colonne latérale).
            main_dx = float(side.get("main_dx_mm", 0.0)) if side else 0.0
            flex = (
                f"display:flex;align-items:flex-start;margin-left:{origin:.2f}mm;" if side else ""
            )
            cell = f"flex:none;width:{s['item_pitch_mm']:.2f}mm;" if grid_items else ""
            out.append(
                f'<div class="item" style="position:relative;{flex}{cell}margin-top:calc({item_margin:.2f}mm*var(--k))">'
            )
            last_item = i == len(sec["items"]) - 1
            for d in s["decorations"]:
                if d["anchor"] != "item" or d.get("attach") == "row" or not self.shown(d):
                    continue
                inset = d.get("bottom_inset_mm", 0.0)
                if d.get("connector") and last_item:
                    # Le dernier élément : son propre trait (plus court) s'il en a un.
                    if d.get("last_bottom_inset_mm") is None:
                        continue
                    inset = d["last_bottom_inset_mm"]
                extra = f"height:auto;bottom:{inset:.2f}mm;" if d.get("stretch") else ""
                out.append(
                    f'<div style="{deco_css(d, d["dx_mm"] - origin, d["dy_mm"] + half(first_st), extra)}"></div>'
                )
            # Toutes les formes posées sur une même ligne, dans l'ordre de peinture
            # de la source (un rond sombre puis son cœur blanc font un anneau).
            row_decor: dict[str, list[Json]] = {}
            for d in s["decorations"]:
                if d.get("attach") == "row":
                    row_decor.setdefault(d["ref_role"], []).append(d)
            done_roles: set[str] = set()
            if side:
                out.append(
                    self.side_block(
                        sec,
                        i,
                        item,
                        side=side,
                        first_st=first_st,
                        row_decor=row_decor,
                        done=done_roles,
                    )
                )
                out.append(
                    f'<div style="flex:none;width:{main_dx - origin - side["w_mm"]:.2f}mm"></div>'
                    '<div class="main" style="flex:1 1 0;min-width:0;position:relative">'
                )
            last_half = half(first_st)
            grid_open: Json | None = None
            grid_count = 0
            for r_i, (sl, vals, m) in enumerate(rows):
                key0 = style_key(self.styles, kind, vals[0][0])
                st0 = self.styles[key0]
                margin_row = 0.0 if r_i == 0 else m - last_half - half(st0)
                width = item_box - sl["dx_mm"]
                if side:
                    # Les emplacements se mesurent depuis la colonne ; le bloc de
                    # contenu commence à `main_dx_mm` : on lui retire ce décalage.
                    sl = {**sl, "dx_mm": sl["dx_mm"] - main_dx}  # noqa: PLW2901
                if grid_open is not None and grid_open is not sl:
                    out.append("</div>")
                    grid_open = None
                if sl.get("columns") and len(vals) == 1:
                    # Une liste en colonnes : les entrées se rangent côte à côte, au
                    # pas mesuré, et passent à la ligne quand la colonne est pleine.
                    role, v, path = vals[0]
                    if grid_open is None:
                        # Le conteneur tient `columns` entrées au pas mesuré ; la
                        # dernière peut dépasser la colonne de quelques millimètres.
                        grid_w = max(width, sl["column_pitch_mm"] * sl["columns"])
                        out.append(
                            f'<div style="display:flex;flex-wrap:wrap;margin-left:{sl["dx_mm"]:.2f}mm;'
                            f'width:{grid_w:.2f}mm;margin-top:calc({margin_row:.2f}mm*var(--k))">'
                        )
                        grid_open = sl
                        grid_count = 0
                        margin_row = 0.0
                    elif grid_count % int(sl["columns"]):
                        # Une entrée de la même rangée : pas de marge (la rangée en a une).
                        margin_row = 0.0
                    grid_count += 1
                    out.append(
                        self.text(
                            key0,
                            v,
                            margin=margin_row,
                            dx=0.0,
                            width=sl["column_pitch_mm"],
                            align=sl["align"],
                            extra=";flex:none",
                            path=path,
                        )
                        + self.marker(s, role, st0)
                        + "</div>"
                    )
                    last_half = half(st0)
                    continue
                if len(vals) == 1 and sl.get("label"):
                    role, v, path = vals[0]
                    out.append(
                        self.labelled_row(
                            kind, sl, role, v, path, margin=margin_row, width=width, st0=st0
                        )
                        + self.marker(s, role, st0)
                        + self.icon(s, kind, role, st0)
                        + self.row_ornament(row_decor, done_roles, role, sl["dx_mm"])
                        + "</div>"
                    )
                elif len(vals) == 1:
                    role, v, path = vals[0]
                    extra = ";white-space:nowrap" if role == "item_dates" else ""
                    size = (item.get("sizes") or {}).get(role)
                    if size and self.view.get("sample"):
                        # L'exemple garde le corps propre de cet élément (un modèle
                        # dont une expérience est écrite plus petit que les autres).
                        extra += f";font-size:{float(size)}pt"
                    if role in CONTACT_ROLES and not self.view.get("sample"):
                        # Une coordonnée d'une personne (un long courriel) se
                        # réduit avant de se couper au milieu d'un mot.
                        extra = self.fit_header_text(v, st0, width)
                    out.append(
                        self.text(
                            key0,
                            v,
                            margin=margin_row,
                            dx=sl["dx_mm"],
                            width=width,
                            align=sl["align"],
                            extra=extra,
                            path=path,
                        )
                        + self.marker(s, role, st0)
                        + self.icon(s, kind, role, st0)
                        + self.row_ornament(row_decor, done_roles, role, sl["dx_mm"])
                        + "</div>"
                    )
                else:
                    # Une pastille de frise peut marquer un champ d'une ligne
                    # composée (« Poste | 2016 ») : elle se pose dans cette ligne.
                    ornament = "".join(
                        self.row_ornament(row_decor, done_roles, role, sl["dx_mm"])
                        for role, _, _ in vals
                    )
                    row = self.joined_row(kind, sl, vals, margin=margin_row, width=width, st0=st0)
                    marker = self.marker(s, vals[0][0], st0)
                    out.append(row[: -len("</div>")] + marker + ornament + "</div>")
                last_half = half(st0)
            if grid_open is not None:
                out.append("</div>")
            if side:
                out.append("</div>")
            out.append("</div>")
            prev_half = last_half
        if grid_items:
            out.append("</div>")
        for d in s["decorations"]:
            if d["anchor"] == "section" and self.shown(d):
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

    def page_shape(self, d: Json) -> str:
        """Un ornement fixe de la page : une forme, ou l'image d'un tracé rastérisé."""
        if d.get("asset"):
            return (
                f'<img src="{esc(self.url(d["asset"]))}" alt="" style="position:absolute;'
                f'left:{d["x_mm"]}mm;top:{d["y_mm"]}mm;width:{d["w_mm"]}mm;height:{d["h_mm"]}mm">'
            )
        return f'<div style="{deco_css(d, d["x_mm"], d["y_mm"])}"></div>'

    def shown(self, d: Json) -> bool:
        """Un ornement propre à l'exemple (barre de niveau d'une langue) ne se dessine que pour lui."""
        return not d.get("sample_only") or bool(self.view.get("sample"))

    def marker(self, s: Json, role: str, st: Json) -> str:
        """La puce d'une ligne de liste."""
        mk = s.get("marker")
        if not mk or role not in (mk.get("roles") or ROLE_MARKER):
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
            f'<img src="{esc(self.url(ic["file"]))}" alt="" style="position:absolute;left:{ic["dx_mm"]:.2f}mm;'
            f'top:{ic["dy_mm"] + half(st):.2f}mm;width:{ic["w_mm"]}mm;height:{ic["h_mm"]}mm">'
        )

    @staticmethod
    def row_ornament(
        row_decor: dict[str, list[Json]], done: set[str], role: str, origin: float
    ) -> str:
        """Les pastilles de frise centrées sur la ligne qu'elles marquent.

        `origin` : l'abscisse (depuis la colonne) du bloc de texte qui les porte.
        """
        if role not in row_decor or role in done:
            return ""
        done.add(role)
        out = []
        for d in row_decor[role]:
            shift = d["h_mm"] / 2 - d["center_offset_mm"]
            out.append(
                '<div style="'
                + deco_css(d, d["dx_mm"] - origin, 0, f"top:calc(50% - {shift:.2f}mm);")
                + '"></div>'
            )
        return "".join(out)

    def side_block(
        self,
        sec: Json,
        index: int,
        item: Json,
        *,
        side: Json,
        first_st: Json,
        row_decor: dict[str, list[Json]],
        done: set[str],
    ) -> str:
        """La colonne latérale d'un élément de frise (entreprise, dates), empilée et alignée."""
        kind = sec["kind"]
        rows: list[tuple[str, str, str | None, float]] = []
        for sl in side["slots"]:
            for role in sl["roles"]:
                val = item.get(role)
                vals = val if isinstance(val, list) else ([val] if val else [])
                repeat = sl.get("repeat_margin_mm")
                rows.extend(
                    (
                        role,
                        v,
                        item_path(sec, index, role, j),
                        sl["margin_top_mm"] if j == 0 or repeat is None else repeat,
                    )
                    for j, v in enumerate(vals)
                )
        if not rows:
            return f'<div class="side" style="flex:none;width:{side["w_mm"]:.2f}mm"></div>'
        st0 = self.styles[style_key(self.styles, kind, rows[0][0])]
        top = side["top_offset_mm"] + half(first_st) - half(st0)
        out = [
            f'<div class="side" style="flex:none;width:{side["w_mm"]:.2f}mm;margin-top:{top:.2f}mm;'
            f'text-align:{side["align"]}">'
        ]
        last_half = half(st0)
        for r_i, (role, v, path, m) in enumerate(rows):
            key = style_key(self.styles, kind, role)
            st = self.styles[key]
            margin_row = 0.0 if r_i == 0 else m - last_half - half(st)
            out.append(
                self.text(
                    key,
                    v,
                    margin=margin_row,
                    dx=0.0,
                    width=side["w_mm"],
                    align=side["align"],
                    path=path,
                )
                + self.row_ornament(row_decor, done, role, side["dx_mm"])
                + "</div>"
            )
            last_half = half(st)
        out.append("</div>")
        return "".join(out)

    def labelled_row(
        self,
        kind: str,
        sl: Json,
        role: str,
        value: str,
        path: str | None,
        *,
        margin: float,
        width: float,
        st0: Json,
    ) -> str:
        """Un champ annoncé par une étiquette fixe du modèle : au-dessus (deux lignes) ou devant (une ligne).

        Le bloc rendu reste ouvert, comme `text` : l'appelant y ajoute puce,
        pictogramme et ornement, puis le ferme.
        """
        label = sl["label"]
        label_key = style_key(self.styles, kind, "label")
        label_st = self.styles[label_key]
        if label["inline"]:
            gap = f'<span style="display:inline-block;width:{label["gap_mm"]:.2f}mm"></span>'
            return (
                f'<div class="t" style="margin-top:calc({margin:.2f}mm*var(--k));margin-left:{sl["dx_mm"]:.2f}mm;'
                f"width:{width:.2f}mm;display:flex;align-items:baseline;"
                f'line-height:{lh(st0):.3f}mm;position:relative">'
                f'<span class="{esc(css_class(label_key))}">{esc(label["text"])}</span>{gap}'
                f'<span class="{esc(css_class(style_key(self.styles, kind, role)))}"{self.anchor(path)}>{esc(value)}</span>'
            )
        # Au-dessus : l'étiquette à la marge mesurée, la valeur au pas mesuré sous elle.
        # Le pas est mesuré de haut en haut : la boîte de l'étiquette s'en retire
        # (la valeur se posait à son pas **sous** l'étiquette, 4 mm trop bas).
        above = (
            self.text(
                label_key,
                label["text"],
                margin=margin + half(st0) - half(label_st),
                dx=label["dx_mm"],
                width=width,
            )
            + "</div>"
        )
        value_margin = label["pitch_mm"] - float(label_st["box_h_mm"]) - half(label_st) - half(st0)
        return above + self.text(
            style_key(self.styles, kind, role),
            value,
            margin=value_margin,
            dx=sl["dx_mm"],
            width=width,
            align=sl["align"],
            extra=";white-space:nowrap" if role == "item_dates" else "",
            path=path,
        )

    def joined_row(
        self,
        kind: str,
        sl: Json,
        vals: list[tuple[str, str, str | None]],
        *,
        margin: float,
        width: float,
        st0: Json,
    ) -> str:
        """Plusieurs champs sur une ligne (« Entreprise | 2016 »), séparés comme dans le modèle."""
        gapx = sl["gap_mm"] or 3.0
        sep = sl.get("separator")
        flush = bool(sl.get("flush_right")) and len(vals) == 2  # noqa: PLR2004
        parts = []
        for n, (role, v, path) in enumerate(vals):
            if n and flush:
                parts.append('<span style="flex:1 1 auto;min-width:3mm"></span>')
            elif n:
                if sep:
                    parts.append(
                        f'<span style="display:inline-block;align-self:flex-start;margin:{sep["dy_mm"] + half(st0):.2f}mm '
                        f"{sep['right_mm']:.2f}mm 0 {sep['left_mm']:.2f}mm;height:{sep['h_mm']}mm;"
                        f'border-left:{sep["stroke_pt"]}pt solid {sep["stroke"]}"></span>'
                    )
                else:
                    parts.append(f'<span style="display:inline-block;width:{gapx:.2f}mm"></span>')
            parts.append(
                f'<span class="{esc(css_class(style_key(self.styles, kind, role)))}"{self.anchor(path)}>{esc(v)}</span>'
            )
        return (
            f'<div class="t" style="margin-top:calc({margin:.2f}mm*var(--k));margin-left:{sl["dx_mm"]:.2f}mm;'
            f"width:{width:.2f}mm;display:flex;align-items:baseline;white-space:nowrap;"
            f'line-height:{lh(st0):.3f}mm;position:relative">{"".join(parts)}</div>'
        )

    def tight_contact(self, elements: list[Json]) -> dict[str, float]:
        """Les coordonnées de l'en-tête resserrées quand la ligne de plus d'une personne
        (le site, synthétisé) entrerait dans la rubrique qui commence sous elles.

        Rend `{champ: y_mm}` ; vide quand tout tient (toujours pour l'exemple,
        qui n'a pas de site).
        """
        contact = sorted(
            (
                e
                for e in elements
                if e["field"] in CONTACT_ROLES and self.view["header"].get(e["field"])
            ),
            key=lambda e: e["y_mm"],
        )
        if len(contact) < 2 or not any(e.get("synthesized") for e in contact):  # noqa: PLR2004
            return {}
        last = contact[-1]
        box = lh(self.styles[last["style"]])
        limit = self.column_top_under(float(last["x_mm"])) - 1.5
        if last["y_mm"] + box <= limit:
            return {}
        top = float(contact[0]["y_mm"])
        pitch = max(box, (limit - box - top) / (len(contact) - 1))
        return {e["field"]: round(top + i * pitch, 2) for i, e in enumerate(contact)}

    def header_reserve(self, column: Json, band_top: float) -> float:
        """La hauteur que les textes de l'en-tête occupent au-dessus d'une colonne de la
        première bande, depuis le haut de la bande : la marge de sa première rubrique ne
        descend pas en dessous, même resserrée par l'ajustement (mesuré : les
        coordonnées d'une personne, une ligne de plus que l'exemple, sous le titre
        « Expériences » remonté de 40 %)."""
        tight = self.tight_contact(self.spec["header"]["elements"])
        sections = column.get("sections") or []
        section_top = band_top + (float(sections[0]["margin_top_mm"]) if sections else 0.0)
        bottom = 0.0
        for e in self.spec["header"]["elements"]:
            if not self.view["header"].get(e["field"]) or e.get("flow"):
                continue
            if not column["x_mm"] - 2 <= float(e["x_mm"]) <= column["x_mm"] + column["w_mm"]:
                continue
            y = tight.get(e["field"], float(e["y_mm"]))
            # Seuls comptent les textes qui occupent l'écart entre le haut de la
            # bande et la rubrique : pas un pied de page (plus bas), pas une
            # coordonnée sur la ligne du titre (cv10), pas ce qui finit au-dessus.
            if y >= section_top - 2.0:
                continue
            low = y + lh(self.styles[e["style"]])
            if low <= band_top:
                continue
            bottom = max(bottom, low)
        return max(0.0, bottom + 1.5 - band_top) if bottom else 0.0

    def band_floor(self, index: int) -> float:
        """La hauteur minimale d'une bande : ce que l'en-tête occupe encore à côté d'elle.

        Un profil plus court que l'exemple remontait la bande suivante dans le poste
        visé posé en absolu (cv2) : la bande garde au moins la hauteur que l'en-tête
        exige, l'ajustement n'y touche pas.
        """
        band = self.spec["bands"][index]
        top = float(band["y_sample"])
        bottom = 0.0
        for e in self.spec["header"]["elements"]:
            if not self.view["header"].get(e["field"]) or e.get("flow"):
                continue
            y = float(e["y_mm"])
            if y < top - 1.0 and y + lh(self.styles[e["style"]]) <= top:
                continue
            if index + 1 < len(self.spec["bands"]) and y >= float(
                self.spec["bands"][index + 1]["y_sample"]
            ):
                continue  # un pied de page, plus bas que la bande suivante
            bottom = max(bottom, y + lh(self.styles[e["style"]]))
        if not bottom:
            return 0.0
        return max(0.0, bottom + 1.5 - top - float(band.get("gap_after_mm", 0.0)))

    def column_top_under(self, x: float) -> float:
        """Le haut mesuré de la première rubrique de la colonne de la première bande à cette abscisse."""
        band = self.spec["bands"][0]
        for c in band["columns"]:
            if c["x_mm"] - 2 <= x <= c["x_mm"] + c["w_mm"] and c["sections"]:
                return float(band["y_sample"]) + float(c["sections"][0]["margin_top_mm"])
        return float(self.spec["first_band_top_mm"])

    def header_limit(self, e: Json, st: Json) -> float:
        """Place disponible pour un élément d'en-tête : jusqu'à la photo, au voisin ou à la marge.

        Un voisin est un élément **sur la même ligne** (les boîtes se recouvrent
        d'au moins la moitié de la plus petite : un nom en grand corps et un
        téléphone sur sa ligne de base, oui ; deux coordonnées empilées, non) ;
        un élément aligné à droite s'étend vers la gauche, un élément centré des
        deux côtés.
        """
        h = self.spec["header"]
        p_box = h.get("photo") if self.photo else None
        pw = self.spec["page"]["width_mm"]
        margin = min(c["x_mm"] for b in self.spec["bands"] for c in b["columns"])
        left_bound, right_bound = margin, pw - margin
        box = float(st["box_h_mm"])
        if p_box and e["y_mm"] < p_box["y_mm"] + p_box["h_mm"] and e["y_mm"] + box > p_box["y_mm"]:
            if e["x_mm"] < p_box["x_mm"]:
                right_bound = min(right_bound, p_box["x_mm"] - 4.0)
            else:
                left_bound = max(left_bound, p_box["x_mm"] + p_box["w_mm"] + 4.0)
        for other in h["elements"]:
            if other is e or not self.view["header"].get(other["field"]):
                continue
            other_box = float(self.styles[other["style"]]["box_h_mm"])
            overlap = min(e["y_mm"] + box, other["y_mm"] + other_box) - max(
                e["y_mm"], other["y_mm"]
            )
            if overlap < 0.5 * min(box, other_box):
                continue
            if other["x_mm"] > e["x_mm"] + 5:
                right_bound = min(right_bound, other["x_mm"] - 4.0)
            elif other["x_mm"] < e["x_mm"] - 5:
                left_bound = max(left_bound, other["x_mm"] + other.get("w_mm", 0.0) + 4.0)
        left_bound, right_bound = self.rule_bounds(e, box, left_bound, right_bound)
        left_bound, right_bound = self.column_bounds(e, box, left_bound, right_bound)
        if e.get("align") == "right":
            return float(e["rx_mm"] - left_bound)
        if e.get("align") == "center":
            return float(2 * min(e["cx_mm"] - left_bound, right_bound - e["cx_mm"]))
        return float(right_bound - e["x_mm"])

    def column_bounds(
        self, e: Json, box: float, left_bound: float, right_bound: float
    ) -> tuple[float, float]:
        """Une colonne de la première bande à la hauteur de l'élément (le profil à droite
        d'un nom en grand corps, cv2) borne aussi."""
        band = self.spec["bands"][0]
        for c in band["columns"]:
            if not (band["y_sample"] <= e["y_mm"] + box and band["bottom_sample"] >= e["y_mm"]):
                continue
            if c["x_mm"] > e["x_mm"] + 5:
                right_bound = min(right_bound, c["x_mm"] - 4.0)
            elif c["x_mm"] + c["w_mm"] < e["x_mm"] - 5:
                left_bound = max(left_bound, c["x_mm"] + c["w_mm"] + 4.0)
        return left_bound, right_bound

    def rule_bounds(
        self, e: Json, box: float, left_bound: float, right_bound: float
    ) -> tuple[float, float]:
        """Un filet vertical (d'en-tête ou de page) à la hauteur de l'élément borne aussi :
        un poste visé au fer à droite ne le traverse pas."""
        for d in [*self.spec["header"]["decorations"], *self.spec["page_decorations"]]:
            if d["kind"] != "vline":
                continue
            top = float(
                d["y_mm"]
                if "y_mm" in d
                else self.spec["first_band_top_mm"] + float(d.get("dy_mm", 0.0))
            )
            if not (top <= e["y_mm"] + box and top + float(d["h_mm"]) >= e["y_mm"]):
                continue
            if d["x_mm"] > e["x_mm"] + 5:
                right_bound = min(right_bound, d["x_mm"] - 2.0)
            elif d["x_mm"] < e["x_mm"] - 5:
                left_bound = max(left_bound, d["x_mm"] + 2.0)
        return left_bound, right_bound

    def fit_header_text(self, val: str, st: Json, avail: float, *, name: bool = False) -> str:
        """Texte trop long dans l'en-tête : interlettrage resserré, puis corps réduit, puis retour à la ligne.

        Un **nom** se réduit jusqu'à 35 % de son corps avant de se replier :
        replié, il descendrait sur le poste visé et les coordonnées, posés en
        absolu sous lui (mesuré avec « Moreau-Lefebvre » en 48 pt).
        """
        return fit_text_css(
            self.fonts,
            val,
            st,
            avail,
            steps=NAME_SIZE_STEPS if name else FIELD_SIZE_STEPS,
            shrink_wrapped=True,
        )

    def header(self) -> str:
        """L'en-tête : ornements d'abord (ordre de peinture de la source), textes, photo."""
        h = self.spec["header"]
        out = [f'<div style="{deco_css(d, d["x_mm"], d["y_mm"])}"></div>' for d in h["decorations"]]
        pw = float(self.spec["page"]["width_mm"])
        tight = self.tight_contact(h["elements"])
        for e in h["elements"]:
            val = self.view["header"].get(e["field"], "")
            if not val or e.get("flow"):
                continue
            if e["field"] in tight:
                e = {**e, "y_mm": tight[e["field"]]}  # noqa: PLW2901
            st = self.styles[e["style"]]
            cls = f'class="{esc(css_class(e["style"]))}"'
            if self.view.get("sample") and e.get("sample_lines"):
                # L'exemple du modèle : chaque ligne d'un champ écrit sur
                # plusieurs lignes reprend sa place exacte dans la source.
                out.extend(
                    f'<div {cls} style="position:absolute;left:{sl["x_mm"]}mm;'
                    f'top:{sl["y_mm"] - half(st):.2f}mm;white-space:nowrap">{esc(sl["text"])}</div>'
                    for sl in e["sample_lines"]
                )
                continue
            box = e.get("box_w_mm")
            if box and not self.view.get("sample"):
                # Le CV d'une personne replie dans toute la place disponible,
                # pas dans la boîte de la ligne la plus longue de l'exemple.
                box = max(float(box), self.header_limit(e, st))
            fit_css = (
                ""
                if box
                else self.fit_header_text(
                    val, st, self.header_limit(e, st), name=e["field"] in NAME_FIELDS
                )
            )
            icon = ""
            if e.get("icon"):
                ic = e["icon"]
                icon = (
                    f'<img src="{esc(self.url(ic["file"]))}" alt="" style="position:absolute;left:{ic["dx_mm"]:.2f}mm;'
                    f'top:{ic["dy_mm"] + half(st):.2f}mm;width:{ic["w_mm"]}mm;height:{ic["h_mm"]}mm">'
                )
            align = e.get("align") or "left"
            left = f"left:{e['x_mm']}mm"
            if align == "center":
                left = (
                    f"left:{e['cx_mm'] - box / 2:.2f}mm"
                    if box
                    else f"left:{e['cx_mm']}mm;transform:translateX(-50%)"
                )
            elif align == "right":
                left = f"right:{pw - e['rx_mm']:.2f}mm"
            wrap = (
                f";width:{box:.2f}mm;white-space:normal;line-height:{e['pitch_mm']:.3f}mm;text-align:{align}"
                if box
                else ";white-space:nowrap"
            )
            out.append(
                f'<div {cls}{self.anchor(header_path(self.view, e["field"]))} style="position:absolute;{left};'
                f'top:{e["y_mm"] - half(st):.2f}mm{wrap}{fit_css}">{esc(val)}{icon}</div>'
            )
        p = h.get("photo")
        if p and self.photo:
            radius = {"circle": "50%", "rounded": "8%"}.get(p["shape"], "0")
            sample = p.get("sample_image") if self.view.get("sample") else None
            if sample:
                # L'exemple du modèle : l'image à son échelle et à son décalage réels.
                image = (
                    f"position:absolute;left:{sample['x_mm']}mm;top:{sample['y_mm']}mm;"
                    f"width:{sample['w_mm']}mm;height:{sample['h_mm']}mm"
                )
            else:
                pos = p["sample_object_position"] if self.view.get("sample") else "50% 30%"
                image = f"width:100%;height:100%;object-fit:cover;object-position:{pos}"
            out.append(
                f'<div style="position:absolute;left:{p["x_mm"]}mm;top:{p["y_mm"]}mm;width:{p["w_mm"]}mm;'
                f'height:{p["h_mm"]}mm;border-radius:{radius};overflow:hidden">'
                f'<img src="{esc(self.url(self.photo))}" alt="" style="{image}"></div>'
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
            # Ni ligatures ni alternances contextuelles : Chromium écrit un glyphe
            # substitué (le trait d'union d'Inter entre deux chiffres) sous un code
            # privé, et un ATS lit « 123456 » sans trait d'union (mesuré le 2026-11-06).
            f".page{{position:relative;width:{page['width_mm']}mm;height:{page['height_mm']}mm;overflow:hidden;background:{page['background']};"
            'font-variant-ligatures:none;font-feature-settings:"calt" 0,"liga" 0,"clig" 0}',
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
                reserve = (
                    self.header_reserve(cspec, float(band["spec"]["y_sample"])) if bi == 0 else 0.0
                )
                inner = "".join(
                    self.section(s, sec, cspec["w_mm"], reserve_mm=reserve if i == 0 else 0.0)
                    for i, (s, sec, _) in enumerate(col)
                )
                cols.append(
                    f'<div class="col" style="margin-left:{cspec["x_mm"] - prev_right:.2f}mm;width:{cspec["w_mm"]:.2f}mm;flex:none">{inner}</div>'
                )
                prev_right = cspec["x_mm"] + cspec["w_mm"]
            margin = 0.0 if bi == 0 else self.spec["bands"][bi - 1].get("gap_after_mm", 8.0)
            # Un filet qui court le long de la bande (la ligne d'une frise) s'étire avec elle.
            band_decor = "".join(
                f'<div style="{deco_css(d, d["x_mm"], d["dy_mm"], self.stretch_css(d))}"></div>'
                for d in band["spec"].get("decorations", [])
            )
            floor = f"min-height:{self.band_floor(bi):.2f}mm;" if bi == 0 else ""
            bands_html.append(
                f'<div class="band" style="position:relative;display:flex;align-items:flex-start;{floor}margin-top:calc({margin:.2f}mm*var(--k))">{band_decor}{"".join(cols)}</div>'
            )
            bands_html.extend(
                f'<div style="position:relative;height:0"><div style="{deco_css(d, d["x_mm"], d["dy_mm"])}"></div></div>'
                for d in self.spec["page_decorations"]
                if d.get("anchor") == "band" and d["band"] == bi
            )
        statics = "".join(
            self.page_shape(d)
            for d in self.spec["page_decorations"]
            if d.get("anchor") == "page" and self.shown(d)
        )
        tst = self.styles["section_title"]
        # Le haut du flux : la première bande commence à sa rubrique la plus
        # haute, quelle que soit sa colonne (les autres gardent leur écart mesuré).
        first_margin = min(
            (
                c["sections"][0]["margin_top_mm"]
                for c in self.spec["bands"][0]["columns"]
                if c["sections"]
            ),
            default=0.0,
        )
        top = self.spec["first_band_top_mm"] - half(tst) - (first_margin - half(tst))
        # Un filet qui court le long de tout le contenu s'étire avec lui.
        shift = self.spec["first_band_top_mm"] - top
        flows = "".join(
            f'<div style="{deco_css(d, d["x_mm"], d["dy_mm"] + shift, self.stretch_css(d))}"></div>'
            for d in self.spec["page_decorations"]
            if d.get("anchor") == "flow"
        )
        fit = fit_script(self.spec, live=self.anchored)
        return (
            f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>CV</title><style>{"".join(css)}</style></head>'
            f'<body><div class="page">{statics}{self.header()}<div class="flow" style="position:absolute;left:0;top:{top:.2f}mm;width:100%">'
            f"{flows}{self.flow_headline()}{''.join(bands_html)}</div>{footer_html('cv')}</div>{fit}</body></html>"
        )

    def flow_headline(self) -> str:
        """Le poste visé d'une personne quand le modèle ne lui laisse pas de place sous le nom :
        il ouvre le flux et pousse la première bande (rien pour l'exemple, qui n'en a pas)."""
        e = next((e for e in self.spec["header"]["elements"] if e.get("flow")), None)
        if e is None:
            return ""
        val = self.view["header"].get(e["field"], "")
        if not val:
            return ""
        pw = float(self.spec["page"]["width_mm"])
        gap = float(e.get("gap_mm") or 2.0)
        return (
            f'<div class="t {esc(css_class(e["style"]))}"{self.anchor(header_path(self.view, e["field"]))} '
            f'style="position:relative;margin-left:{e["x_mm"]}mm;width:{pw - float(e["x_mm"]) - 8:.2f}mm;'
            f'margin-bottom:{gap:.2f}mm">{esc(val)}</div>'
        )

    @staticmethod
    def stretch_css(d: Json) -> str:
        """Le CSS qui étire un ornement jusqu'au bas de son conteneur, au retrait mesuré."""
        if not d.get("stretch"):
            return ""
        return f"height:auto;bottom:{d.get('bottom_inset_mm', 0.0):.2f}mm;"


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
