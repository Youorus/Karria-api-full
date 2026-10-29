"""Les polices : lecture par fontTools, bibliothèque libre gardée dans le stockage objet.

Contrat
-------
- `inspect_font` lit une police (incorporée ou téléchargée) : métriques
  verticales de Chromium (`hhea`, ou `OS/2` typo si `USE_TYPO_METRICS`),
  hauteur d'x et des capitales, chasses, licence (`name` 13/14), `fsType`,
  classe (PANOSE, `sFamilyClass`), caractères couverts.
- `FontLibrary` sert les familles libres : **téléchargées une fois** (Google
  Fonts : instances statiques complètes ; à défaut Fontsource : instance
  statique « latin »), rangées dans le bucket `templates` sous
  `fonts/<famille>/…`, puis relues de là — jamais retéléchargées. Une police
  variable seule disponible est instanciée en statiques (`varLib.instancer`).
- `FontLibrary.resolve` applique l'ordre de `domain/fonts.py` et rend la
  police servie avec sa justification.
- Le réseau sortant passe par `urllib` dans un fil (comme `shared/http`) :
  ces appels n'ont lieu qu'à l'ingestion d'un modèle.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final

from fontTools import subset as ft_subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

from karria.modules.documents.domain.fonts import (
    FontClass,
    FontMetrics,
    FontOrigin,
    FontResolution,
    LibraryFamily,
    SourceFont,
    choose_substitute,
    classify,
    family_key,
    missing_characters,
    nearest_weight,
)
from karria.shared.logging import get_logger
from karria.shared.storage import Bucket, ObjectNotFoundError, ObjectStorage

logger = get_logger(__name__)

#: Les familles parmi lesquelles une remplaçante est choisie : libres (OFL,
#: Apache), statiques disponibles, couvrant le français, et couvrant les
#: classes que les modèles de CV emploient. Les jumelles métriques des polices
#: système (Arimo ↔ Arial, Tinos ↔ Times, Carlito ↔ Calibri, Caladea ↔
#: Cambria) y sont : un modèle exporté en Arial retrouve ses retours à la ligne.
CURATED_FAMILIES: Final[tuple[str, ...]] = (
    "Arimo",
    "Tinos",
    "Carlito",
    "Caladea",
    "Cousine",
    "Inter",
    "Roboto",
    "Open Sans",
    "Lato",
    "Montserrat",
    "Raleway",
    "Poppins",
    "Nunito Sans",
    "Source Sans 3",
    "Work Sans",
    "DM Sans",
    "Manrope",
    "Josefin Sans",
    "Barlow",
    "Mulish",
    "Karla",
    "Rubik",
    "Oswald",
    "League Spartan",
    "Questrial",
    "Red Hat Display",
    "Playfair Display",
    "Lora",
    "Merriweather",
    "EB Garamond",
    "Cormorant Garamond",
    "Libre Baskerville",
    "Roboto Slab",
    "Crimson Pro",
)

#: Les caractères sur lesquels la chasse se compare : ceux d'un CV.
_WIDTH_PROBE: Final = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789éèàç"
_GOOGLE_LIST: Final = "https://fonts.google.com/download/list?family={family}"
_FONTSOURCE_META: Final = "https://api.fontsource.org/v1/fonts/{id}"
_USER_AGENT: Final = "Karria/1.0 (+https://karria.fr)"
_WEIGHT_SUFFIX: Final = {
    "thin": 100,
    "extralight": 200,
    "light": 300,
    "regular": 400,
    "": 400,
    "medium": 500,
    "semibold": 600,
    "bold": 700,
    "extrabold": 800,
    "black": 900,
}


@dataclass(frozen=True, slots=True)
class FontFacts:
    """Ce qu'on lit d'une police avec fontTools."""

    family: str
    units_per_em: int
    ascent: float
    descent: float
    x_height: float | None
    cap_height: float | None
    fs_type: int | None
    licence: str
    chars: frozenset[str]
    widths: dict[str, float]
    panose_serif: int | None
    family_class: int | None
    monospaced: bool
    variable: bool
    weight: int
    italic: bool

    def metrics(self) -> FontMetrics:
        """Les métriques comparables (domaine)."""
        probe = [self.widths[c] for c in _WIDTH_PROBE if c in self.widths]
        return FontMetrics(
            x_height=self.x_height,
            cap_height=self.cap_height,
            avg_width=sum(probe) / len(probe) if probe else None,
            widths=dict(self.widths),
        )

    @property
    def font_class(self) -> FontClass | None:
        """La classe de la police."""
        return classify(
            panose_serif=self.panose_serif,
            family_class=self.family_class,
            monospaced=self.monospaced,
            name=self.family,
        )


def _name(font: Any, name_id: int) -> str:
    table = font.get("name", None)
    if table is None:
        return ""
    record = table.getDebugName(name_id)
    return str(record or "")


def inspect_font(data: bytes) -> FontFacts:
    """Lit une police TrueType/OpenType (ou CFF nu enveloppé) avec fontTools."""
    font = TTFont(io.BytesIO(data), lazy=True)
    upem = int(font["head"].unitsPerEm) if "head" in font else 1000
    os2 = font.get("OS/2", None)
    hhea = font.get("hhea", None)
    use_typo = bool(os2 is not None and int(os2.fsSelection) & (1 << 7))
    if use_typo and os2 is not None:
        ascent, descent = int(os2.sTypoAscender), int(os2.sTypoDescender)
    elif hhea is not None:
        ascent, descent = int(hhea.ascent), int(hhea.descent)
    else:
        ascent, descent = int(0.95 * upem), int(-0.25 * upem)
    cmap = font.getBestCmap() or {}
    chars = frozenset(chr(code) for code in cmap)
    hmtx = font.get("hmtx", None)
    widths: dict[str, float] = {}
    if hmtx is not None:
        for char in set(_WIDTH_PROBE) & chars:
            glyph = cmap[ord(char)]
            widths[char] = round(hmtx[glyph][0] / upem, 4)
    x_height = cap_height = None
    if os2 is not None and int(getattr(os2, "version", 0)) >= 2:  # noqa: PLR2004
        x_height = int(os2.sxHeight) / upem or None
        cap_height = int(os2.sCapHeight) / upem or None
    if (x_height is None or cap_height is None) and "glyf" in font:
        glyf = font["glyf"]
        if x_height is None and ord("x") in cmap:
            g = glyf[cmap[ord("x")]]
            x_height = (getattr(g, "yMax", 0) or 0) / upem or None
        if cap_height is None and ord("H") in cmap:
            g = glyf[cmap[ord("H")]]
            cap_height = (getattr(g, "yMax", 0) or 0) / upem or None
    panose = getattr(os2, "panose", None) if os2 is not None else None
    post = font.get("post", None)
    weight = int(os2.usWeightClass) if os2 is not None else 400
    italic = bool(os2 is not None and int(os2.fsSelection) & 1) or bool(
        post is not None and float(post.italicAngle) != 0
    )
    return FontFacts(
        family=_name(font, 16) or _name(font, 1),
        units_per_em=upem,
        ascent=ascent / upem,
        descent=descent / upem,
        x_height=round(x_height, 4) if x_height else None,
        cap_height=round(cap_height, 4) if cap_height else None,
        fs_type=int(os2.fsType) if os2 is not None else None,
        licence=" ".join(filter(None, (_name(font, 13), _name(font, 14)))),
        chars=chars,
        widths=widths,
        panose_serif=int(panose.bSerifStyle) if panose is not None else None,
        family_class=int(os2.sFamilyClass) if os2 is not None else None,
        monospaced=bool(post is not None and int(post.isFixedPitch)),
        variable="fvar" in font,
        weight=weight,
        italic=italic,
    )


class FontMeasurer:
    """Mesure de texte avec une police réelle, sans navigateur.

    Chasse = somme des avances (`hmtx`) au corps donné, plus l'interlettrage
    entre glyphes. Le crénage n'est pas compté : l'écart reste sous 1 % sur
    un intitulé et la décision (resserrer, réduire, passer à la ligne) garde
    une marge.
    """

    def __init__(self, data: bytes) -> None:
        font = TTFont(io.BytesIO(data), lazy=True)
        self._upem = int(font["head"].unitsPerEm)
        self._cmap: dict[int, str] = font.getBestCmap() or {}
        hmtx = font["hmtx"]
        self._advance: dict[str, int] = {g: int(hmtx[g][0]) for g in set(self._cmap.values())}
        notdef = hmtx.metrics.get(".notdef")
        self._fallback = int(notdef[0]) if notdef else int(self._upem * 0.5)

    def width_pt(self, text: str, size_pt: float, letter_spacing_pt: float = 0.0) -> float:
        """Largeur de `text` en points."""
        total = 0
        for char in text:
            glyph = self._cmap.get(ord(char))
            total += self._advance[glyph] if glyph is not None else self._fallback
        return total * size_pt / self._upem + letter_spacing_pt * max(0, len(text) - 1)


@lru_cache(maxsize=64)
def facts_of(data: bytes) -> FontFacts:
    """`inspect_font`, gardé en mémoire (une police est relue à chaque rendu)."""
    return inspect_font(data)


@lru_cache(maxsize=64)
def measurer(data: bytes) -> FontMeasurer:
    """Un mesureur par police (les octets servent de clé : une police = un objet)."""
    return FontMeasurer(data)


def static_instance(data: bytes, weight: int, *, italic_axis: bool = False) -> bytes:
    """Une instance statique d'une police variable à la graisse donnée."""
    font = TTFont(io.BytesIO(data))
    axes = {a.axisTag: a for a in font["fvar"].axes}
    location: dict[str, float] = {}
    if "wght" in axes:
        axis = axes["wght"]
        location["wght"] = float(min(max(weight, axis.minValue), axis.maxValue))
    for tag, axis in axes.items():
        if tag == "wght":
            continue
        if tag == "ital":
            location[tag] = 1.0 if italic_axis else 0.0
        else:
            location[tag] = float(axis.defaultValue)
    static = instancer.instantiateVariableFont(font, location, updateFontNames=True)
    out = io.BytesIO()
    static.save(out)
    return out.getvalue()


def subset_for_documents(data: bytes) -> bytes:
    """Réduit une police aux écritures latines (pour l'incorporer à un DOCX).

    On garde tout le latin (de base, supplément, étendu A et B), la ponctuation
    générale et les symboles monétaires : la personne peut modifier son CV
    sans tomber sur un glyphe absent.
    """
    options = ft_subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.glyph_names = False
    font = TTFont(io.BytesIO(data))
    subsetter = ft_subset.Subsetter(options)
    ranges = [
        *range(0x20, 0x250),
        *range(0x2000, 0x2070),
        *range(0x20A0, 0x20C0),
        0x2122,
        0x2190,
        0x2192,
        0x25CF,
    ]
    subsetter.populate(unicodes=ranges)
    subsetter.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _http_get(url: str, *, timeout: float = 30.0) -> bytes:
    if not url.startswith("https://"):
        msg = f"adresse de police refusée (https attendu) : {url[:80]}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310 - URL de catalogue fixe
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload: bytes = response.read()
        return payload


def _weight_of_filename(filename: str) -> tuple[int, bool] | None:
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if "[" in stem:
        return None
    suffix = stem.split("-", 1)[1] if "-" in stem else ""
    italic = suffix.lower().endswith("italic")
    core = suffix.lower().removesuffix("italic")
    weight = _WEIGHT_SUFFIX.get(core)
    if weight is None:
        return None
    return weight, italic


@dataclass(slots=True)
class FamilyRecord:
    """Une famille gardée dans la bibliothèque : ses fichiers et ce qu'on en sait."""

    family: str
    key: str
    licence: str
    source: str
    font_class: str | None
    styles: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def available(self, italic: bool) -> tuple[int, ...]:
        """Les graisses disponibles dans ce style."""
        suffix = "i" if italic else ""
        return tuple(
            sorted(
                int(s[:-1] if s.endswith("i") else s)
                for s in self.styles
                if s.endswith("i") == bool(suffix)
            )
        )

    def to_json(self) -> dict[str, Any]:
        """Forme gardée dans `fonts/<famille>/family.json`."""
        return {
            "family": self.family,
            "key": self.key,
            "licence": self.licence,
            "source": self.source,
            "font_class": self.font_class,
            "styles": self.styles,
            "metrics": self.metrics,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FamilyRecord:
        """Relit la fiche gardée."""
        return cls(
            family=data["family"],
            key=data["key"],
            licence=data["licence"],
            source=data["source"],
            font_class=data.get("font_class"),
            styles=dict(data.get("styles") or {}),
            metrics=dict(data.get("metrics") or {}),
        )

    def library_family(self) -> LibraryFamily:
        """La famille vue comme candidate à la substitution."""
        metrics = self.metrics
        return LibraryFamily(
            family=self.family,
            font_class=FontClass(self.font_class) if self.font_class else FontClass.SANS,
            licence=self.licence,
            metrics=FontMetrics(
                x_height=metrics.get("x_height"),
                cap_height=metrics.get("cap_height"),
                avg_width=metrics.get("avg_width"),
                widths=dict(metrics.get("widths") or {}),
            ),
            weights=self.available(False),
            italics=bool(self.available(True)),
        )


@dataclass(frozen=True, slots=True)
class FontFile:
    """Une police servie : octets d'une instance statique et son nom CSS."""

    family: str
    weight: int
    italic: bool
    data: bytes

    @property
    def file_name(self) -> str:
        """Nom de fichier passé à Gotenberg et référencé par `@font-face`."""
        slug = re.sub(r"[^A-Za-z0-9]", "", self.family)
        return f"{slug}-{self.weight}{'i' if self.italic else ''}.ttf"


class FontCatalogue:
    """Téléchargement des familles libres (Google Fonts, puis Fontsource)."""

    async def download(self, family: str) -> tuple[dict[str, bytes], str, str] | None:
        """Les instances statiques d'une famille : `{"700i": octets}`, licence, source."""
        return await asyncio.to_thread(self._download, family)

    def _download(self, family: str) -> tuple[dict[str, bytes], str, str] | None:
        try:
            found = self._google(family)
        except Exception as exc:
            logger.info("documents.font_google_unavailable", family=family, error=str(exc)[:200])
            found = None
        if found:
            return found
        try:
            return self._fontsource(family)
        except Exception as exc:
            logger.info(
                "documents.font_fontsource_unavailable", family=family, error=str(exc)[:200]
            )
            return None

    def _google(self, family: str) -> tuple[dict[str, bytes], str, str] | None:
        raw = _http_get(_GOOGLE_LIST.format(family=urllib.parse.quote(family)))
        text = raw.decode("utf-8")
        manifest = json.loads(text[text.index("{") :])["manifest"]
        licence = "OFL-1.1"
        for file in manifest.get("files", []):
            name = str(file.get("filename", "")).upper()
            if name.startswith("LICENSE"):
                licence = "Apache-2.0"
            elif name.startswith("UFL"):
                licence = "UFL-1.0"
        styles: dict[str, bytes] = {}
        variable: list[tuple[str, str]] = []
        for ref in manifest.get("fileRefs", []):
            filename, url = str(ref["filename"]), str(ref["url"])
            if not filename.endswith(".ttf"):
                continue
            if "[" in filename:
                variable.append((filename, url))
                continue
            parsed = _weight_of_filename(filename)
            if parsed is None:
                continue
            weight, italic = parsed
            styles.setdefault(f"{weight}{'i' if italic else ''}", _http_get(url))
        if not styles and variable:
            for filename, url in variable:
                data = _http_get(url)
                italic = "italic" in filename.lower()
                for weight in (300, 400, 500, 600, 700, 800):
                    styles.setdefault(
                        f"{weight}{'i' if italic else ''}", static_instance(data, weight)
                    )
        return (styles, licence, "google-fonts") if styles else None

    def _fontsource(self, family: str) -> tuple[dict[str, bytes], str, str] | None:
        font_id = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")
        meta = json.loads(_http_get(_FONTSOURCE_META.format(id=font_id)).decode("utf-8"))
        licence = str(meta.get("license") or "")
        styles: dict[str, bytes] = {}
        for weight, by_style in (meta.get("variants") or {}).items():
            for style, subsets in by_style.items():
                url = ((subsets.get("latin") or {}).get("url") or {}).get("ttf")
                if url:
                    styles[f"{int(weight)}{'i' if style == 'italic' else ''}"] = _http_get(str(url))
        return (styles, licence, "fontsource") if styles else None


#: Les caches de la bibliothèque sont **par processus**, pas par instance : une
#: bibliothèque est construite à chaque requête, et sans cela chaque export
#: relisait `family.json` et chaque `.ttf` dans le bucket (deux fois : PDF puis
#: DOCX). Une police est immuable sous sa clé ; un enregistrement absent (`None`)
#: n'est pas gardé, pour qu'une ingestion ultérieure soit vue.
_SHARED_RECORDS: dict[str, FamilyRecord | None] = {}
_SHARED_FILES: dict[str, bytes] = {}


class FontLibrary:
    """La bibliothèque de polices libres, gardée dans le bucket `templates`."""

    PREFIX: Final = "fonts"

    def __init__(
        self,
        storage: ObjectStorage,
        catalogue: FontCatalogue | None = None,
        *,
        shared_cache: bool = True,
    ) -> None:
        self._storage = storage
        self._catalogue = catalogue or FontCatalogue()
        self._records: dict[str, FamilyRecord | None] = _SHARED_RECORDS if shared_cache else {}
        self._files: dict[str, bytes] = _SHARED_FILES if shared_cache else {}

    def _record_key(self, key: str) -> str:
        return f"{self.PREFIX}/{key}/family.json"

    async def family(self, family: str, *, download: bool = True) -> FamilyRecord | None:
        """La fiche d'une famille ; la télécharge et la range la première fois."""
        key = family_key(family)
        if not key:
            return None
        if key in self._records:
            return self._records[key]
        record: FamilyRecord | None
        try:
            record = FamilyRecord.from_json(
                json.loads(await self._storage.get(Bucket.TEMPLATES, self._record_key(key)))
            )
        except ObjectNotFoundError:
            if not download:
                # Un rendu qui ne trouve pas la fiche ne fige rien : une
                # ingestion ultérieure (qui télécharge) doit encore la voir.
                return None
            record = await self._fetch(family, key)
        self._records[key] = record
        return record

    async def _fetch(self, family: str, key: str) -> FamilyRecord | None:
        found = await self._catalogue.download(family)
        if found is None:
            logger.info("documents.font_not_in_catalogue", family=family)
            return None
        styles, licence, source = found
        regular = styles.get("400") or next(iter(styles.values()))
        facts = inspect_font(regular)
        metrics = facts.metrics()
        record = FamilyRecord(
            family=facts.family or family,
            key=key,
            licence=licence,
            source=source,
            font_class=(facts.font_class or FontClass.SANS).value,
            metrics={
                "x_height": metrics.x_height,
                "cap_height": metrics.cap_height,
                "avg_width": metrics.avg_width,
                "widths": metrics.widths,
                "ascent": facts.ascent,
                "descent": facts.descent,
            },
        )
        for style, data in styles.items():
            file_key = f"{self.PREFIX}/{key}/{style}.ttf"
            await self._storage.put(Bucket.TEMPLATES, file_key, data, content_type="font/ttf")
            record.styles[style] = file_key
            self._files[file_key] = data
        await self._storage.put(
            Bucket.TEMPLATES,
            self._record_key(key),
            json.dumps(record.to_json()).encode("utf-8"),
            content_type="application/json",
        )
        logger.info(
            "documents.font_stored", family=record.family, styles=len(styles), source=source
        )
        return record

    async def file(
        self, family: str, weight: int, italic: bool, *, download: bool = False
    ) -> FontFile | None:
        """L'instance statique la plus proche demandée (graisse, style).

        Sans téléchargement par défaut : un **rendu** ne va jamais sur Internet.
        Seule l'ingestion (`download=True`) range une famille qui manque.
        """
        record = await self.family(family, download=download)
        if record is None or not record.styles:
            return None
        wanted_italic = italic and bool(record.available(True))
        weight_served = nearest_weight(weight, record.available(wanted_italic))
        style = f"{weight_served}{'i' if wanted_italic else ''}"
        key = record.styles[style]
        if key not in self._files:
            self._files[key] = await self._storage.get(Bucket.TEMPLATES, key)
        return FontFile(
            family=record.family, weight=weight_served, italic=wanted_italic, data=self._files[key]
        )

    async def candidates(self) -> list[LibraryFamily]:
        """Les familles de substitution, avec leurs métriques."""
        out: list[LibraryFamily] = []
        for family in CURATED_FAMILIES:
            record = await self.family(family)
            if record is not None and record.styles:
                out.append(record.library_family())
        return out

    async def resolve(self, source: SourceFont) -> FontResolution:
        """La police servie pour une police du modèle (ordre de `domain/fonts.py`)."""
        if source.reuse_allowed and not source.missing_chars:
            return FontResolution(
                source=source.name,
                family=source.family,
                weight=source.weight,
                italic=source.italic,
                origin=FontOrigin.EMBEDDED,
                licence="incorporée, réutilisation permise",
                note="police incorporée au PDF, complète pour le français",
            )
        record = await self.family(source.family)
        if record is not None and record.styles:
            return FontResolution(
                source=source.name,
                family=record.family,
                weight=nearest_weight(
                    source.weight, record.available(source.italic) or record.available(False)
                ),
                italic=source.italic and bool(record.available(True)),
                origin=FontOrigin.LIBRARY,
                licence=record.licence,
                note=f"même famille, bibliothèque libre ({record.source})",
            )
        choice = choose_substitute(source, await self.candidates())
        if choice is None:
            msg = f"Aucune police de substitution disponible pour {source.name}."
            raise LookupError(msg)
        family, distance = choice
        reason = (
            "incorporée en sous-ensemble incomplet"
            if source.missing_chars
            else "absente des bibliothèques libres"
        )
        resolution = FontResolution(
            source=source.name,
            family=family.family,
            weight=nearest_weight(source.weight, family.weights),
            italic=source.italic and family.italics,
            origin=FontOrigin.SUBSTITUTE,
            licence=family.licence,
            distance=distance,
            note=f"{source.family} {reason} ; remplacée par {family.family} (classe {family.font_class.value}, écart de métriques {distance:.1%})",
        )
        logger.warning(
            "documents.font_substituted",
            source=source.name,
            family=family.family,
            distance=distance,
        )
        return resolution


def source_font(
    name: str, embedded: bytes | None, *, weight: int, italic: bool, family: str
) -> SourceFont:
    """Décrit une police du modèle à partir de sa version incorporée (si lisible)."""
    from karria.modules.documents.domain.fonts import licence_allows_reuse  # noqa: PLC0415

    if embedded:
        try:
            facts = inspect_font(embedded)
        except Exception:
            facts = None
        if facts is not None:
            return SourceFont(
                name=name,
                family=family,
                weight=weight,
                italic=italic,
                font_class=facts.font_class,
                metrics=facts.metrics(),
                reuse_allowed=licence_allows_reuse(facts.licence, facts.fs_type),
                missing_chars=missing_characters(set(facts.chars)),
            )
    return SourceFont(
        name=name,
        family=family,
        weight=weight,
        italic=italic,
        font_class=classify(panose_serif=None, family_class=None, monospaced=False, name=family),
        metrics=FontMetrics(),
        missing_chars=missing_characters(set()),
    )


MM_PER_PT: Final = 25.4 / 72


class FontSet:
    """Les polices servies pour un rendu, et la mesure de texte qui va avec.

    Clé : (famille, graisse, italique). Une graisse absente prend la plus
    proche, comme le ferait le navigateur, pour que la mesure et le rendu
    parlent de la même police.
    """

    def __init__(self, files: list[FontFile]) -> None:
        self._files = {(f.family, f.weight, f.italic): f for f in files}

    @property
    def files(self) -> list[FontFile]:
        """Les fichiers servis."""
        return list(self._files.values())

    def get(self, family: str, weight: int, italic: bool) -> FontFile | None:
        """Le fichier le plus proche du style demandé."""
        exact = self._files.get((family, weight, italic))
        if exact is not None:
            return exact
        same = [f for f in self._files.values() if f.family == family and f.italic == italic] or [
            f for f in self._files.values() if f.family == family
        ]
        if not same:
            return None
        weight_served = nearest_weight(weight, tuple(f.weight for f in same))
        return next(f for f in same if f.weight == weight_served)

    def box_ratio(self, family: str, weight: int, italic: bool) -> float:
        """Hauteur de la boîte de texte (ascendante − descendante) en fraction du corps."""
        font = self.get(family, weight, italic)
        if font is None:
            return 1.17
        facts = facts_of(font.data)
        return facts.ascent - facts.descent

    def width_mm(
        self,
        text: str,
        style: dict[str, Any],
        *,
        size_pt: float | None = None,
        ls_pt: float | None = None,
    ) -> float:
        """Largeur rendue d'un texte dans un style de la spécification, en mm."""
        size = size_pt or float(style["size_pt"])
        spacing = float(style["letter_spacing_pt"]) if ls_pt is None else ls_pt
        shown = text.upper() if style.get("uppercase") else text
        font = self.get(str(style["family"]), int(style["weight"]), bool(style.get("italic")))
        if font is None:
            return (len(shown) * size * 0.55 + spacing * max(0, len(shown) - 1)) * MM_PER_PT
        return measurer(font.data).width_pt(shown, size, spacing) * MM_PER_PT


@lru_cache(maxsize=256)
def glyph_box(data: bytes, char: str) -> tuple[float, float, float, float, float] | None:
    """Boîte d'encre d'un caractère (xMin, yMin, xMax, yMax) et son avance, en em."""
    from fontTools.pens.boundsPen import BoundsPen  # noqa: PLC0415

    font = TTFont(io.BytesIO(data), lazy=True)
    glyph = (font.getBestCmap() or {}).get(ord(char))
    if glyph is None:
        return None
    upem = float(font["head"].unitsPerEm)
    glyphs = font.getGlyphSet()
    pen = BoundsPen(glyphs)
    glyphs[glyph].draw(pen)
    if pen.bounds is None:
        return None
    x0, y0, x1, y1 = pen.bounds
    return x0 / upem, y0 / upem, x1 / upem, y1 / upem, glyphs[glyph].width / upem
