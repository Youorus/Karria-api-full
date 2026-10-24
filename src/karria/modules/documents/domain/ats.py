"""Score de compatibilité ATS — déterministe ; le modèle de langage n'écrit que l'explication.

Contrat
-------
Le document produit est lu **comme un ATS** (`ExtractedDocument`, produit par
l'infrastructure : texte dans l'ordre du flux pour un PDF, XML brut dans
l'ordre du document pour un DOCX). Chaque contrôle rend ses points, son
maximum, un constat et un conseil (`tip`, `None` s'il n'y a rien à faire).
Barème sur 100 (ADR-0020 § 6) ; sans offre, les 20 points de mots-clés sont
retirés et le total ramené à 100 :

| Contrôle | Points |
|---|---|
| `text_layer` — couche texte | 10 |
| `fonts` — polices exploitables, intitulés lus lettre à lettre | 8 |
| `reading_order` — chaque rubrique lue d'un seul tenant | 10 |
| `contact` — e-mail, téléphone, nom en tête | 9 |
| `layout` — colonnes, tableaux, photo, pages | 8 |
| `sections` — rubriques standard | 25 |
| `content` — missions, résultats chiffrés, longueur, dates | 10 |
| `keywords` — mots-clés de l'offre | 20 |

`level` : `good` (≥ 85), `fair` (70–84), `poor` (< 70). `suggest_ats_safe` :
la variante « optimisée ATS » (interlettrage plafonné) ferait gagner des points.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE: Final = re.compile(r"(?:\+\d{2,3}\s?|0)[1-9](?:[\s.-]?\d{2}){4}")
#: Quatre lettres isolées ou plus (« D A T A ») : un intitulé lu lettre à lettre.
SPACED: Final = re.compile(r"(?:\b\w (?=\w\b)){3,}\w\b")
SECTION_SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "experiences": (
        "experience",
        "experiences professionnelles",
        "parcours professionnel",
        "work experience",
        "emplois",
    ),
    "educations": ("formation", "formations", "education", "etudes", "diplomes", "cursus"),
    "skills": ("competences", "skills", "savoir-faire", "outils"),
    "languages": ("langues", "languages"),
    "summary": ("profil", "resume", "summary", "a propos", "objectif"),
}
REQUIRED: Final = ("experiences", "educations", "skills")
STOP: Final = frozenset(
    [
        "a",
        "au",
        "aux",
        "avec",
        "ce",
        "ces",
        "dans",
        "de",
        "des",
        "du",
        "elle",
        "en",
        "et",
        "eux",
        "il",
        "je",
        "la",
        "le",
        "les",
        "leur",
        "lui",
        "ma",
        "mais",
        "me",
        "même",
        "mes",
        "moi",
        "mon",
        "ne",
        "nos",
        "notre",
        "nous",
        "on",
        "ou",
        "par",
        "pas",
        "pour",
        "qu",
        "que",
        "qui",
        "sa",
        "se",
        "ses",
        "son",
        "sur",
        "ta",
        "te",
        "tes",
        "toi",
        "ton",
        "tu",
        "un",
        "une",
        "vos",
        "votre",
        "vous",
        "c",
        "d",
        "j",
        "l",
        "à",
        "m",
        "n",
        "s",
        "t",
        "y",
        "été",
        "être",
        "avoir",
        "sont",
        "est",
        "sera",
        "seront",
        "ainsi",
        "afin",
        "selon",
        "tout",
        "tous",
        "toute",
        "toutes",
        "plus",
        "moins",
        "très",
        "bien",
        "chez",
        "entre",
        "sous",
        "sans",
        "vers",
        "via",
        "etc",
        "poste",
        "profil",
        "mission",
        "missions",
        "entreprise",
        "équipe",
        "equipe",
        "rejoindre",
        "recherchons",
        "cherchons",
        "idéalement",
        "idealement",
        "souhaité",
        "souhaite",
        "niveau",
        "capacité",
        "capacite",
        "connaissance",
        "connaissances",
        "maîtrise",
        "maitrise",
        "expérience",
        "experience",
        "an",
        "ans",
        "année",
        "années",
        "annee",
        "annees",
        "h",
        "f",
        "hf",
        "cdi",
        "cdd",
        "alternance",
        "stage",
        "contrat",
        "temps",
        "plein",
        "semaine",
        "jour",
        "jours",
        "mois",
        "lieu",
        "salaire",
        "rémunération",
        "remuneration",
        "avantages",
        "candidature",
        "postuler",
        "the",
        "and",
        "of",
        "to",
        "in",
        "for",
        "with",
        "on",
        "as",
        "is",
        "are",
        "you",
        "we",
        "our",
        "your",
        "an",
        "or",
        "be",
        "will",
        "at",
        "by",
        "from",
        "this",
        "that",
    ]
)
LEVEL_GOOD: Final = 85
LEVEL_FAIR: Final = 70


def fold(text: str) -> str:
    """Minuscules sans accents."""
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Ce qu'un ATS lit d'un document."""

    text: str
    format: str
    pages: int | None
    font_types: dict[str, int]
    embedded: bool
    images: int
    tables: int


@dataclass(slots=True)
class Check:
    """Un contrôle du score."""

    key: str
    label: str
    points: float
    max_points: float
    finding: str
    tip: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Forme du contrat (`ats.checks[]`)."""
        return {
            "key": self.key,
            "label": self.label,
            "points": round(self.points, 1),
            "max_points": self.max_points,
            "finding": self.finding,
            "tip": self.tip,
        }


@dataclass(slots=True)
class AtsReport:
    """Le score et ses contrôles."""

    score: int
    format: str
    checks: list[Check] = field(default_factory=list)
    keywords: dict[str, Any] | None = None

    @property
    def level(self) -> str:
        """`good`, `fair` ou `poor`."""
        if self.score >= LEVEL_GOOD:
            return "good"
        return "fair" if self.score >= LEVEL_FAIR else "poor"

    def spaced_titles(self) -> bool:
        """Des intitulés sont lus lettre à lettre : la variante optimisée aiderait."""
        fonts = next((c for c in self.checks if c.key == "fonts"), None)
        contact = next((c for c in self.checks if c.key == "contact"), None)
        return bool(fonts and "lettre à lettre" in fonts.finding) or bool(
            contact and contact.tip and "lettre à lettre" in contact.tip
        )

    def to_json(self, *, suggest_ats_safe: bool) -> dict[str, Any]:
        """Forme du contrat (`ats`), sans l'explication (ajoutée par le cas d'usage)."""
        return {
            "score": self.score,
            "level": self.level,
            "format": self.format,
            "checks": [c.to_json() for c in self.checks],
            "keywords": self.keywords,
            "suggest_ats_safe": suggest_ats_safe,
        }


def _view_strings(view: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Par rubrique : le titre et les textes de ses éléments."""
    out = []
    for sec in view["sections"]:
        texts: list[str] = []
        for item in sec["items"]:
            for value in item.values():
                if isinstance(value, str):
                    texts.append(value)
                elif isinstance(value, list):
                    texts += [x for x in value if isinstance(x, str)]
        out.append((sec["title"], texts))
    return out


def order_score(text: str, view: dict[str, Any]) -> tuple[float, str]:
    """Chaque rubrique se lit d'un bloc : titre puis ses éléments, sans s'entrelacer."""
    folded = fold(text)
    blocks = []
    for sec, (title, texts) in zip(view["sections"], _view_strings(view), strict=True):
        units = [title] + [t for t in texts if len(t) > 8]  # noqa: PLR2004
        start = folded.find(fold(title)[:30])
        positions = []
        for unit in units:
            probe = fold(unit)[:30]
            # D'abord après le titre de la rubrique : un intitulé peut aussi figurer dans le profil.
            i = folded.find(probe, max(0, start))
            if i < 0:
                i = folded.find(probe)
            if i >= 0:
                positions.append(i)
        if len(positions) >= 2:  # noqa: PLR2004
            blocks.append((sec["kind"], min(positions), max(positions), positions))
    if not blocks:
        return 0.0, "aucune rubrique retrouvée dans le texte extrait"
    bad = 0
    for kind, lo, hi, _ in blocks:
        for other, _, _, positions in blocks:
            if other != kind and any(lo < p < hi for p in positions):
                bad += 1
                break
    return 1 - bad / len(
        blocks
    ), f"{len(blocks) - bad}/{len(blocks)} rubriques lues d'un seul tenant"


def _is_tech(token: str) -> bool:
    return (
        (token[:1].isupper() and any(c.isupper() for c in token[1:]))
        or (token.isupper() and 2 <= len(token) <= 6)  # noqa: PLR2004
        or bool(re.search(r"[+#]", token))
    )


#: Noms en -er/-re qu'une offre emploie souvent et qui ne sont pas des verbes.
_NOT_VERBS: Final = frozenset(
    {
        "power",
        "docker",
        "manager",
        "designer",
        "developer",
        "leader",
        "server",
        "chiffre",
        "centre",
        "cadre",
        "titre",
        "offre",
        "ordre",
        "partner",
    }
)


def _is_verb(folded: str) -> bool:
    return len(folded) > 4 and folded.endswith(("er", "ir", "re")) and folded not in _NOT_VERBS  # noqa: PLR2004


def _is_good(folded: str) -> bool:
    return len(folded) > 2 and folded not in STOP and not folded.isdigit()  # noqa: PLR2004


def keywords(offer: str, top: int = 15) -> list[str]:
    """Mots-clés d'une offre, sans modèle de langage.

    Termes techniques (sigles, outils), groupes nominaux de deux mots ou
    « X de Y » (« tableaux de bord »), puis mots seuls fréquents. Un mot seul
    déjà contenu dans un groupe retenu n'est pas repris.
    """
    grams: defaultdict[str, float] = defaultdict(float)
    unis: defaultdict[str, float] = defaultdict(float)
    for clause in re.split(r"[;,.:!?()\n]|\s[—–-]\s", offer):
        toks = re.findall(r"[A-Za-zÀ-ÿ0-9+#]+(?:[-'’][A-Za-zÀ-ÿ0-9+#]+)*", clause)
        folded = [fold(t) for t in toks]
        for i, (t, f) in enumerate(zip(toks, folded, strict=True)):
            if _is_good(f) or _is_tech(t):
                unis[f] += 3.0 if _is_tech(t) else 1.0
            if (
                i + 1 < len(toks)
                and (_is_good(f) or _is_tech(t))
                and (_is_good(folded[i + 1]) or _is_tech(toks[i + 1]))
                and not _is_verb(f)
                and not _is_verb(folded[i + 1])
            ):
                grams[f"{f} {folded[i + 1]}"] += (
                    2.0 if (_is_tech(t) or _is_tech(toks[i + 1])) else 1.0
                )
            if (
                i + 2 < len(toks)
                and folded[i + 1] in ("de", "d", "des", "du")
                and _is_good(f)
                and _is_good(folded[i + 2])
                and not _is_verb(f)
            ):
                grams[f"{f} {folded[i + 1]} {folded[i + 2]}"] += 1.5
    out: list[str] = []
    for gram, count in _ranked(grams):
        if count >= 1.5 and not any(gram in o or o in gram for o in out):  # noqa: PLR2004
            out.append(gram)
    for uni, _ in _ranked(unis):
        if len(out) >= top:
            break
        if any(uni in o.split() for o in out) or _is_verb(uni) or uni in STOP:
            continue
        out.append(uni)
    return out[:top]


def _ranked(weights: dict[str, float]) -> list[tuple[str, float]]:
    """Du plus lourd au plus léger ; à poids égal, l'ordre d'apparition."""
    return sorted(weights.items(), key=lambda kv: -kv[1])


def _word_pattern(part: str) -> str:
    """Un mot entier, au singulier ou au pluriel en « s » ; « c++ » et « c# » compris."""
    stem = part[:-1] if part.endswith("s") and not part.endswith("ss") and len(part) > 3 else part  # noqa: PLR2004
    return rf"(?<![\w+#]){re.escape(stem)}s?(?![\w+#])"


def covered(term: str, text_folded: str) -> bool:
    """Le terme (ou son pluriel en « s ») figure dans le texte plié, mot pour mot."""
    return all(re.search(_word_pattern(p), text_folded) for p in term.split())


def _text_layer(doc: ExtractedDocument, view: dict[str, Any]) -> Check:
    expected = [t for _, texts in _view_strings(view) for t in texts]
    flat = re.sub(r"\s+", " ", fold(doc.text))
    found = sum(1 for e in expected if fold(e)[:25] in flat)
    ratio = found / max(1, len(expected))
    return Check(
        "text_layer",
        "Texte lisible par une machine",
        round(10 * ratio, 1),
        10,
        f"{found}/{len(expected)} blocs du CV retrouvés tels quels dans le texte extrait",
        None
        if ratio > 0.97  # noqa: PLR2004
        else "Certains blocs ne sont pas extraits à l'identique (césure, ligature ou texte en image) : évite les effets typographiques sur le contenu.",
    )


def _fonts(doc: ExtractedDocument) -> Check:
    t3 = doc.font_types.get("Type3", 0)
    bad_chars = len(re.findall("�", doc.text))
    pts = 8.0 - (5 if t3 else 0) - (2 if not doc.embedded else 0) - min(3, bad_chars)
    check = Check(
        "fonts",
        "Polices exploitables",
        max(0.0, pts),
        8,
        f"{t3} police(s) Type3, {bad_chars} caractère(s) illisible(s), incorporation {'complète' if doc.embedded else 'partielle'}",
        "Les polices Type3 (dessins de glyphes) trompent les ATS : exporte avec des polices TrueType/OpenType statiques."
        if t3
        else None,
    )
    spaced = SPACED.findall(doc.text)
    if spaced:
        check.points = max(0.0, check.points - min(4, 2 * len(spaced)))
        check.finding += (
            f" ; {len(spaced)} intitulé(s) lu(s) lettre à lettre (« {spaced[0][:24]}… »)"
        )
        check.tip = (
            "Des intitulés très espacés se lisent lettre à lettre par les ATS (ton poste visé n'est alors pas reconnu) : "
            "active la variante « optimisée ATS » du modèle, qui réduit cet espacement."
        )
    return check


def _contact(doc: ExtractedDocument, cv: dict[str, Any]) -> Check:
    contact = cv.get("contact") or {}
    ident = cv.get("identity") or {}
    folded = fold(doc.text)
    head = EMAIL.sub(" ", folded[: max(400, len(folded) // 5)])
    email_ok = bool(contact.get("email")) and fold(contact["email"]) in folded
    phone_ok = bool(contact.get("phone")) and bool(PHONE.search(doc.text.replace(" ", " ")))
    names = [fold(ident.get(k) or "") for k in ("first_name", "last_name")]
    name_ok = all(n and re.search(rf"\b{re.escape(n)}\b", head) for n in names)
    missing = [
        n
        for n, ok in (("e-mail", email_ok), ("téléphone", phone_ok), ("nom en tête", name_ok))
        if not ok
    ]
    if not contact.get("phone") and "téléphone" in missing:
        missing.remove("téléphone")
        phone_ok = True
    if not contact.get("email") and "e-mail" in missing:
        missing.remove("e-mail")
        email_ok = True
    tip = None
    if missing == ["nom en tête"]:
        tip = "Ton nom est lu lettre à lettre (espacement du modèle) : la variante « optimisée ATS » le corrige."
    elif missing:
        tip = "Écris tes coordonnées en texte simple (pas en image), le téléphone au format français, et ton nom en haut du CV."
    return Check(
        "contact",
        "Coordonnées détectées",
        3.0 * email_ok + 3.0 * phone_ok + 3.0 * bool(name_ok),
        9,
        "toutes détectées" if not missing else "non détecté : " + ", ".join(missing),
        tip,
    )


def _layout(doc: ExtractedDocument, columns: int, order_ok: float) -> Check:
    pts = 8.0
    notes = []
    if columns > 1:
        notes.append(f"{columns} colonnes")
        pts -= 0 if order_ok == 1 else 3
    if doc.tables:
        notes.append(f"{doc.tables} tableau(x) de mise en page")
        pts -= 1
    if doc.images:
        notes.append("photo")
    if doc.pages and doc.pages > 2:  # noqa: PLR2004
        notes.append(f"{doc.pages} pages")
        pts -= 2
    return Check(
        "layout",
        "Mise en page",
        max(0.0, pts),
        8,
        ", ".join(notes) or "une colonne, sans tableau",
        "La photo n'est pas lue par les ATS : c'est sans risque en France, mais retire-la pour une candidature à l'international."
        if doc.images
        else None,
    )


def _sections(view: dict[str, Any]) -> Check:
    titles = [fold(s["title"]) for s in view["sections"]]
    present = {
        k for k, syns in SECTION_SYNONYMS.items() if any(any(s in t for s in syns) for t in titles)
    }
    required = [k for k in REQUIRED if k in present]
    pts = (
        25 * (len(required) / len(REQUIRED)) * 0.8
        + 5 * (("summary" in present) + ("languages" in present)) / 2
    )
    missing = [k for k in REQUIRED if k not in present]
    names = {"experiences": "Expérience", "educations": "Formation", "skills": "Compétences"}
    return Check(
        "sections",
        "Rubriques standard",
        round(pts, 1),
        25,
        "rubriques reconnues : " + (", ".join(sorted(present)) or "aucune"),
        ("Ajoute : " + ", ".join(names[k] for k in missing) + ".") if missing else None,
    )


def _content(doc: ExtractedDocument, cv: dict[str, Any]) -> Check:
    exps = cv.get("experiences") or []
    with_bullets = sum(1 for e in exps if len(e.get("missions") or []) >= 2)  # noqa: PLR2004
    quantified = sum(1 for e in exps for m in (e.get("missions") or []) if re.search(r"\d", m))
    words = len(re.findall(r"\w+", doc.text))
    dated = sum(1 for e in exps if e.get("start_date"))
    pts = (
        3 * (with_bullets / max(1, len(exps)))
        + 3 * min(1, quantified / 3)
        + 2 * (dated / max(1, len(exps)))
        + 2 * (250 <= words <= 900)  # noqa: PLR2004
    )
    tips = []
    if quantified < 3:  # noqa: PLR2004
        tips.append("chiffre tes résultats (volumes, pourcentages, délais)")
    if with_bullets < len(exps):
        tips.append("donne au moins deux missions par expérience")
    if not 250 <= words <= 900:  # noqa: PLR2004
        tips.append(f"vise 250 à 900 mots (actuellement {words})")
    return Check(
        "content",
        "Contenu exploitable",
        round(pts, 1),
        10,
        f"{with_bullets}/{len(exps)} expériences avec ≥ 2 missions, {quantified} résultat(s) chiffré(s), {words} mots",
        ("Pour aller plus loin : " + " ; ".join(tips) + ".") if tips else None,
    )


def analyse(
    doc: ExtractedDocument,
    view: dict[str, Any],
    cv: dict[str, Any],
    *,
    columns: int,
    offer: str | None = None,
) -> AtsReport:
    """Le score ATS d'un document produit."""
    order_ok, order_finding = order_score(doc.text, view)
    checks = [
        _text_layer(doc, view),
        _fonts(doc),
        Check(
            "reading_order",
            "Ordre de lecture",
            round(10 * order_ok, 1),
            10,
            order_finding,
            None
            if order_ok == 1
            else "Une colonne se lit en travers de l'autre : garde une rubrique entière dans une seule colonne.",
        ),
        _contact(doc, cv),
        _layout(doc, columns, order_ok),
        _sections(view),
        _content(doc, cv),
    ]
    kw = None
    if offer:
        terms = keywords(offer)
        folded = fold(doc.text)
        hit = [t for t in terms if covered(t, folded)]
        miss = [t for t in terms if t not in hit]
        cov = len(hit) / max(1, len(terms))
        kw = {"coverage": round(cov, 2), "covered": hit, "missing": miss}
        checks.append(
            Check(
                "keywords",
                "Mots-clés de l'offre",
                round(20 * cov, 1),
                20,
                f"{len(hit)}/{len(terms)} mots-clés de l'offre présents",
                ("Si c'est vrai pour toi, fais apparaître : " + ", ".join(miss[:6]) + ".")
                if miss
                else None,
            )
        )
    total = sum(c.points for c in checks)
    maximum = sum(c.max_points for c in checks)
    return AtsReport(
        score=round(100 * total / maximum), format=doc.format, checks=checks, keywords=kw
    )


#: Signature du lecteur de document (infrastructure) : octets → ce qu'un ATS en lit.
Reader = Callable[[bytes], ExtractedDocument]
