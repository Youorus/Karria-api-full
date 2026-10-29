"""Polices : lecture fontTools, instanciation, catalogue, bibliothèque, résolution, mesure.

Toutes les polices sont fabriquées en mémoire (`fakes.build_font`) ; le
réseau du catalogue est remplacé par des réponses préparées.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fontTools.ttLib import TTFont

from documents.infrastructure.fakes import FULL_CHARS, build_font, build_variable_font
from karria.modules.documents.domain.fonts import (
    FontClass,
    FontMetrics,
    FontOrigin,
    SourceFont,
)
from karria.modules.documents.infrastructure import fonts as fonts_module
from karria.modules.documents.infrastructure.fonts import (
    CURATED_FAMILIES,
    FamilyRecord,
    FontCatalogue,
    FontFile,
    FontLibrary,
    FontMeasurer,
    FontSet,
    _name,
    _weight_of_filename,
    facts_of,
    glyph_box,
    inspect_font,
    measurer,
    source_font,
    static_instance,
    subset_for_documents,
)
from karria.shared.config import StorageSettings
from karria.shared.storage import Bucket, InMemoryObjectStorage

pytestmark = pytest.mark.unit

REGULAR = build_font()
BOLD = build_font(style="Bold", weight=700)


# ---------------------------------------------------------------------------
# Lecture d'une police
# ---------------------------------------------------------------------------


class TestInspectionDUnePolice:
    def test_lit_famille_metriques_licence_et_classe(self) -> None:
        facts = inspect_font(REGULAR)
        assert facts.family == "Testa"
        assert facts.units_per_em == 1000
        assert (facts.ascent, facts.descent) == (0.9, -0.25)
        assert (facts.x_height, facts.cap_height) == (0.5, 0.7)
        assert "Open Font License" in facts.licence
        assert facts.fs_type == 0
        assert facts.weight == 400
        assert not facts.italic
        assert not facts.monospaced
        assert not facts.variable
        assert "é" in facts.chars
        assert facts.widths["a"] == round((400 + (ord("a") % 7) * 40) / 1000, 4)
        assert facts.font_class is FontClass.SANS

    def test_les_metriques_typo_priment_quand_le_drapeau_le_demande(self) -> None:
        facts = inspect_font(build_font(use_typo=True))
        assert (facts.ascent, facts.descent) == (0.8, -0.2)

    def test_sans_os2_ni_hhea_les_metriques_par_defaut(self) -> None:
        facts = inspect_font(build_font(with_os2=False, with_hhea=False))
        assert (facts.ascent, facts.descent) == (0.95, -0.25)
        assert facts.fs_type is None
        assert facts.panose_serif is None
        assert facts.family_class is None
        assert facts.weight == 400

    def test_os2_ancien_hauteurs_lues_dans_les_glyphes(self) -> None:
        facts = inspect_font(build_font(os2_version=1))
        assert (facts.x_height, facts.cap_height) == (0.5, 0.7)

    def test_sans_x_ni_h_les_hauteurs_restent_inconnues(self) -> None:
        facts = inspect_font(build_font(os2_version=1, chars="abc"))
        assert facts.x_height is None
        assert facts.cap_height is None

    def test_sans_hmtx_aucune_chasse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def without_hmtx(*args: Any, **kwargs: Any) -> TTFont:
            font = TTFont(*args, **kwargs)
            real_get = font.get
            font.get = lambda tag, default=None: None if tag == "hmtx" else real_get(tag, default)  # type: ignore[method-assign]
            return font

        monkeypatch.setattr(fonts_module, "TTFont", without_hmtx)
        assert inspect_font(REGULAR).widths == {}

    def test_italique_par_langle_et_chasse_fixe(self) -> None:
        facts = inspect_font(build_font(italic_angle=-12, fixed_pitch=True))
        assert facts.italic
        assert facts.monospaced
        assert facts.font_class is FontClass.MONO

    def test_italique_par_fs_selection(self) -> None:
        assert inspect_font(build_font(style="Italic")).italic

    def test_une_police_variable_est_reconnue(self) -> None:
        assert inspect_font(build_variable_font()).variable

    def test_les_metriques_comparables(self) -> None:
        metrics = inspect_font(REGULAR).metrics()
        assert metrics.x_height == 0.5
        assert metrics.avg_width is not None
        assert 0.4 <= metrics.avg_width <= 0.64

    def test_sans_caractere_mesure_pas_de_chasse_moyenne(self) -> None:
        assert inspect_font(build_font(chars=" ")).metrics().avg_width is None

    def test_un_nom_absent_vaut_chaine_vide(self) -> None:
        assert _name({}, 1) == ""

    def test_lecture_gardee_en_memoire(self) -> None:
        assert facts_of(REGULAR) is facts_of(REGULAR)
        assert measurer(REGULAR) is measurer(REGULAR)


class TestMesureDeTexte:
    def test_somme_des_avances_et_interlettrage(self) -> None:
        m = FontMeasurer(REGULAR)
        expected = sum(400 + (ord(c) % 7) * 40 for c in "ab") * 10 / 1000
        assert m.width_pt("ab", 10) == pytest.approx(expected)
        assert m.width_pt("ab", 10, 2.0) == pytest.approx(expected + 2.0)

    def test_un_caractere_absent_prend_lavance_du_notdef(self) -> None:
        assert FontMeasurer(REGULAR).width_pt("一", 10) == pytest.approx(5.0)

    def test_sans_notdef_la_moitie_du_cadratin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def without_notdef(*args: Any, **kwargs: Any) -> TTFont:
            font = TTFont(*args, **kwargs)
            del font["hmtx"].metrics[".notdef"]
            return font

        monkeypatch.setattr(fonts_module, "TTFont", without_notdef)
        assert FontMeasurer(REGULAR).width_pt("一", 12) == pytest.approx(6.0)


class TestBoiteDeGlyphe:
    def test_boite_et_avance_en_em(self) -> None:
        box = glyph_box(REGULAR, "H")
        assert box is not None
        x0, y0, x1, y1, advance = box
        assert (x0, y0, y1) == (0.04, 0.0, 0.7)
        assert advance == pytest.approx(x1 + 0.04)

    def test_caractere_absent(self) -> None:
        assert glyph_box(REGULAR, "一") is None

    def test_glyphe_sans_encre(self) -> None:
        assert glyph_box(REGULAR, " ") is None


class TestInstancesEtSousEnsembles:
    def test_instance_statique_a_la_graisse_demandee(self) -> None:
        static = TTFont(io.BytesIO(static_instance(build_variable_font(), 1200)))
        assert "fvar" not in static
        assert static["OS/2"].usWeightClass == 900

    def test_un_axe_italique_est_fixe(self) -> None:
        data = static_instance(build_variable_font(ital_axis=True), 400, italic_axis=True)
        assert "fvar" not in TTFont(io.BytesIO(data))
        data = static_instance(build_variable_font(ital_axis=True), 400)
        assert "fvar" not in TTFont(io.BytesIO(data))

    def test_sans_axe_de_graisse_les_autres_axes_restent_par_defaut(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Axis:
            def __init__(self, tag: str, default: float) -> None:
                self.axisTag, self.defaultValue = tag, default
                self.minValue, self.maxValue = default - 10, default + 10

        class Font:
            def __getitem__(self, tag: str) -> Any:
                assert tag == "fvar"
                return type("Fvar", (), {"axes": [Axis("wdth", 100), Axis("opsz", 12)]})()

        class Static:
            def save(self, out: io.BytesIO) -> None:
                out.write(b"statique")

        seen: dict[str, float] = {}

        def instantiate(font: Any, location: dict[str, float], **_: Any) -> Static:
            seen.update(location)
            return Static()

        monkeypatch.setattr(fonts_module, "TTFont", lambda *_: Font())
        monkeypatch.setattr(fonts_module.instancer, "instantiateVariableFont", instantiate)
        assert static_instance(b"", 700) == b"statique"
        assert seen == {"wdth": 100.0, "opsz": 12.0}

    def test_le_sous_ensemble_garde_le_latin(self) -> None:
        facts = inspect_font(subset_for_documents(REGULAR))
        assert set("aéÉ•") <= facts.chars


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("static/Raleway-Bold.ttf", (700, False)),
        ("Raleway-BoldItalic.ttf", (700, True)),
        ("Raleway-Italic.ttf", (400, True)),
        ("Raleway.ttf", (400, False)),
        ("Raleway[wght].ttf", None),
        ("Raleway-Fancy.ttf", None),
    ],
)
def test_la_graisse_se_lit_dans_le_nom_de_fichier(
    filename: str, expected: tuple[int, bool] | None
) -> None:
    assert _weight_of_filename(filename) == expected


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_http_get_envoie_un_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        seen["ua"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return _Response(b"ok")

    monkeypatch.setattr(fonts_module.urllib.request, "urlopen", fake_urlopen)
    assert fonts_module._http_get("https://example.test/x", timeout=3) == b"ok"
    assert seen == {"ua": fonts_module._USER_AGENT, "timeout": 3}


def _google_manifest(files: list[str], refs: list[tuple[str, str]]) -> bytes:
    manifest = {
        "manifest": {
            "files": [{"filename": f} for f in files],
            "fileRefs": [{"filename": n, "url": u} for n, u in refs],
        }
    }
    return (")]}'\n" + json.dumps(manifest)).encode()


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Les réponses du « réseau » : URL → octets (ou exception)."""
    routes: dict[str, Any] = {}

    def fake_get(url: str, *, timeout: float = 30.0) -> bytes:
        del timeout
        answer = routes[url]
        if isinstance(answer, Exception):
            raise answer
        return bytes(answer)

    monkeypatch.setattr(fonts_module, "_http_get", fake_get)
    return routes


GOOGLE = "https://fonts.google.com/download/list?family="
FONTSOURCE = "https://api.fontsource.org/v1/fonts/"


class TestCatalogueGoogleFonts:
    def test_instances_statiques_et_licence_ofl(self, web: dict[str, Any]) -> None:
        web[GOOGLE + "Testa"] = _google_manifest(
            ["OFL.txt"],
            [
                ("static/Testa-Regular.ttf", "u/r"),
                ("static/Testa-BoldItalic.ttf", "u/bi"),
                ("static/Testa-Fancy.ttf", "u/f"),
                ("README.md", "u/readme"),
                ("Testa[wght].ttf", "u/var"),
            ],
        )
        web["u/r"], web["u/bi"] = b"R", b"BI"
        styles, licence, source = FontCatalogue()._google("Testa") or ({}, "", "")
        assert styles == {"400": b"R", "700i": b"BI"}
        assert (licence, source) == ("OFL-1.1", "google-fonts")

    @pytest.mark.parametrize(
        ("file", "licence"), [("LICENSE.txt", "Apache-2.0"), ("UFL.txt", "UFL-1.0")]
    )
    def test_la_licence_se_lit_dans_les_fichiers(
        self, web: dict[str, Any], file: str, licence: str
    ) -> None:
        web[GOOGLE + "Open%20Sans"] = _google_manifest([file], [("OpenSans-Bold.ttf", "u/b")])
        web["u/b"] = b"B"
        found = FontCatalogue()._google("Open Sans")
        assert found is not None
        assert found[1] == licence

    def test_une_variable_seule_est_instanciee(self, web: dict[str, Any]) -> None:
        web[GOOGLE + "Varia"] = _google_manifest(
            [], [("Varia[wght].ttf", "u/v"), ("Varia-Italic[wght].ttf", "u/vi")]
        )
        web["u/v"] = web["u/vi"] = build_variable_font()
        found = FontCatalogue()._google("Varia")
        assert found is not None
        assert sorted(found[0]) == sorted(
            [str(w) for w in (300, 400, 500, 600, 700, 800)]
            + [f"{w}i" for w in (300, 400, 500, 600, 700, 800)]
        )

    def test_rien_d_utilisable(self, web: dict[str, Any]) -> None:
        web[GOOGLE + "Vide"] = _google_manifest([], [("Vide.otf", "u/o")])
        assert FontCatalogue()._google("Vide") is None


class TestCatalogueFontsource:
    def test_instances_latines(self, web: dict[str, Any]) -> None:
        meta = {
            "license": "OFL-1.1",
            "variants": {
                "400": {
                    "normal": {"latin": {"url": {"ttf": "u/400"}}},
                    "italic": {"latin": {"url": {"ttf": "u/400i"}}},
                },
                "700": {"normal": {"cyrillic": {"url": {"ttf": "u/cyr"}}}},
            },
        }
        web[FONTSOURCE + "dm-sans"] = json.dumps(meta).encode()
        web["u/400"], web["u/400i"] = b"N", b"I"
        found = FontCatalogue()._fontsource("DM Sans")
        assert found == ({"400": b"N", "400i": b"I"}, "OFL-1.1", "fontsource")

    def test_aucune_variante(self, web: dict[str, Any]) -> None:
        web[FONTSOURCE + "rien"] = b"{}"
        assert FontCatalogue()._fontsource("Rien") is None


class TestTelechargement:
    async def test_google_d_abord(self, web: dict[str, Any]) -> None:
        web[GOOGLE + "Testa"] = _google_manifest([], [("Testa-Regular.ttf", "u/r")])
        web["u/r"] = b"R"
        found = await FontCatalogue().download("Testa")
        assert found is not None
        assert found[2] == "google-fonts"

    async def test_repli_sur_fontsource_si_google_tombe(self, web: dict[str, Any]) -> None:
        web[GOOGLE + "Testa"] = OSError("hors ligne")
        web[FONTSOURCE + "testa"] = json.dumps(
            {"variants": {"400": {"normal": {"latin": {"url": {"ttf": "u/n"}}}}}}
        ).encode()
        web["u/n"] = b"N"
        found = await FontCatalogue().download("Testa")
        assert found == ({"400": b"N"}, "", "fontsource")

    async def test_repli_sur_fontsource_si_google_est_vide(self, web: dict[str, Any]) -> None:
        web[GOOGLE + "Testa"] = _google_manifest([], [])
        web[FONTSOURCE + "testa"] = OSError("hors ligne")
        assert await FontCatalogue().download("Testa") is None


# ---------------------------------------------------------------------------
# Bibliothèque
# ---------------------------------------------------------------------------


class FakeCatalogue(FontCatalogue):
    """Catalogue sans réseau : des familles préparées, et le compte des téléchargements."""

    def __init__(self, families: dict[str, tuple[dict[str, bytes], str, str]]) -> None:
        self.families = families
        self.calls: list[str] = []

    async def download(self, family: str) -> tuple[dict[str, bytes], str, str] | None:
        self.calls.append(family)
        return self.families.get(family)


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage(StorageSettings(), "test")


def _testa_catalogue() -> FakeCatalogue:
    return FakeCatalogue(
        {
            "Testa": (
                {"400": REGULAR, "700": BOLD, "400i": build_font(style="Italic")},
                "OFL-1.1",
                "google-fonts",
            )
        }
    )


class TestFicheDeFamille:
    def test_graisses_disponibles_par_style(self) -> None:
        record = FamilyRecord(
            "X", "x", "OFL", "google-fonts", None, styles={"400": "a", "700": "b", "400i": "c"}
        )
        assert record.available(False) == (400, 700)
        assert record.available(True) == (400,)

    def test_aller_retour_json(self) -> None:
        record = FamilyRecord("X", "x", "OFL", "src", "serif", {"400": "k"}, {"x_height": 0.5})
        assert FamilyRecord.from_json(record.to_json()) == record
        bare = FamilyRecord.from_json(
            {"family": "X", "key": "x", "licence": "", "source": "s", "styles": None}
        )
        assert bare.styles == {}
        assert bare.metrics == {}
        assert bare.font_class is None

    def test_vue_candidate_classe_par_defaut_lineale(self) -> None:
        record = FamilyRecord("X", "x", "OFL", "s", None, {"400": "k", "700i": "k2"})
        family = record.library_family()
        assert family.font_class is FontClass.SANS
        assert family.weights == (400,)
        assert family.italics
        assert FamilyRecord("X", "x", "", "s", "serif").library_family().font_class is (
            FontClass.SERIF
        )


def test_le_nom_de_fichier_servi() -> None:
    assert FontFile("Open Sans", 700, True, b"").file_name == "OpenSans-700i.ttf"
    assert FontFile("Lato", 400, False, b"").file_name == "Lato-400.ttf"


class TestBibliotheque:
    async def test_telecharge_une_fois_range_et_relit(self, storage: InMemoryObjectStorage) -> None:
        catalogue = _testa_catalogue()
        library = FontLibrary(storage, catalogue, shared_cache=False)
        record = await library.family("Testa")
        assert record is not None
        assert record.family == "Testa"
        assert record.font_class == "sans-serif"
        assert record.metrics["ascent"] == 0.9
        assert set(record.styles) == {"400", "700", "400i"}
        assert await storage.get(Bucket.TEMPLATES, "fonts/testa/700.ttf") == BOLD
        assert await library.family("Testa-Bold") is record  # même clé, gardée en mémoire
        # Une autre instance relit la fiche du stockage, sans retélécharger.
        again = FontLibrary(storage, catalogue, shared_cache=False)
        assert (await again.family("Testa")) == record
        assert catalogue.calls == ["Testa"]
        served = await again.file("Testa", 700, False)
        assert served is not None
        assert served.data == BOLD

    async def test_famille_sans_regular_prend_le_premier_style(
        self, storage: InMemoryObjectStorage
    ) -> None:
        catalogue = FakeCatalogue({"Testa": ({"700": BOLD}, "OFL", "fontsource")})
        record = await FontLibrary(storage, catalogue, shared_cache=False).family("Testa")
        assert record is not None
        assert record.styles == {"700": "fonts/testa/700.ttf"}

    async def test_famille_inconnue_ou_vide(self, storage: InMemoryObjectStorage) -> None:
        library = FontLibrary(storage, FakeCatalogue({}), shared_cache=False)
        assert await library.family("") is None
        assert await library.family("Inconnue") is None
        assert await library.file("Inconnue", 400, False) is None

    async def test_sans_telechargement(self, storage: InMemoryObjectStorage) -> None:
        catalogue = _testa_catalogue()
        assert (
            await FontLibrary(storage, catalogue, shared_cache=False).family(
                "Testa", download=False
            )
            is None
        )
        assert catalogue.calls == []

    async def test_le_catalogue_par_defaut(self, storage: InMemoryObjectStorage) -> None:
        assert isinstance(FontLibrary(storage, shared_cache=False)._catalogue, FontCatalogue)

    async def test_graisse_la_plus_proche_et_italique_de_repli(
        self, storage: InMemoryObjectStorage
    ) -> None:
        library = FontLibrary(storage, _testa_catalogue(), shared_cache=False)
        # Un rendu ne télécharge jamais ; l'ingestion, si.
        assert await library.file("Testa", 600, False) is None
        bold = await library.file("Testa", 600, False, download=True)
        assert bold is not None
        assert (bold.weight, bold.italic) == (700, False)
        italic = await library.file("Testa", 700, True)
        assert italic is not None
        assert (italic.weight, italic.italic) == (400, True)
        catalogue = FakeCatalogue({"Rom": ({"400": REGULAR}, "OFL", "s")})
        roman = await FontLibrary(storage, catalogue, shared_cache=False).file(
            "Rom", 400, True, download=True
        )
        assert roman is not None
        assert roman.italic is False

    async def test_candidates_parmi_les_familles_retenues(
        self, storage: InMemoryObjectStorage
    ) -> None:
        catalogue = FakeCatalogue({"Arimo": ({"400": REGULAR}, "Apache-2.0", "google-fonts")})
        out = await FontLibrary(storage, catalogue, shared_cache=False).candidates()
        assert [f.family for f in out] == ["Testa"]  # la famille lue dans la police
        assert catalogue.calls == list(CURATED_FAMILIES)


def _source(**kw: Any) -> SourceFont:
    base: dict[str, Any] = {
        "name": "Testa-Bold",
        "family": "Testa",
        "weight": 700,
        "italic": False,
        "font_class": FontClass.SANS,
        "metrics": FontMetrics(x_height=0.5, cap_height=0.7, avg_width=0.5),
    }
    return SourceFont(**{**base, **kw})


class TestResolution:
    async def test_police_incorporee_libre_et_complete(
        self, storage: InMemoryObjectStorage
    ) -> None:
        catalogue = FakeCatalogue({})
        out = await FontLibrary(storage, catalogue, shared_cache=False).resolve(
            _source(reuse_allowed=True)
        )
        assert out.origin is FontOrigin.EMBEDDED
        assert out.weight == 700
        assert catalogue.calls == []

    async def test_meme_famille_dans_la_bibliotheque(self, storage: InMemoryObjectStorage) -> None:
        out = await FontLibrary(storage, _testa_catalogue()).resolve(
            _source(weight=600, italic=True)
        )
        assert out.origin is FontOrigin.LIBRARY
        assert (out.family, out.weight, out.italic) == ("Testa", 400, True)
        assert "google-fonts" in out.note
        assert out.licence == "OFL-1.1"

    async def test_sans_italique_la_graisse_se_choisit_parmi_les_romains(
        self, storage: InMemoryObjectStorage
    ) -> None:
        catalogue = FakeCatalogue({"Testa": ({"400": REGULAR, "700": BOLD}, "OFL", "s")})
        out = await FontLibrary(storage, catalogue, shared_cache=False).resolve(
            _source(italic=True)
        )
        assert (out.weight, out.italic) == (700, False)

    @pytest.mark.parametrize(
        ("missing", "reason"),
        [(frozenset("é"), "sous-ensemble incomplet"), (frozenset(), "absente des bibliothèques")],
    )
    async def test_substitution_par_la_plus_proche(
        self, storage: InMemoryObjectStorage, missing: frozenset[str], reason: str
    ) -> None:
        catalogue = FakeCatalogue(
            {"Arimo": ({"400": build_font(family="Arimo"), "700": BOLD}, "Apache-2.0", "g")}
        )
        out = await FontLibrary(storage, catalogue, shared_cache=False).resolve(
            _source(
                family="Rare", name="Rare-Bold", missing_chars=missing, reuse_allowed=bool(missing)
            )
        )
        assert out.origin is FontOrigin.SUBSTITUTE
        assert (out.family, out.weight) == ("Arimo", 700)
        assert out.distance is not None
        assert reason in out.note

    async def test_aucune_remplacante(self, storage: InMemoryObjectStorage) -> None:
        with pytest.raises(LookupError, match="Rare-Bold"):
            await FontLibrary(storage, FakeCatalogue({})).resolve(
                _source(family="Rare", name="Rare-Bold")
            )


class TestDescriptionDUnePoliceDuModele:
    def test_depuis_la_police_incorporee(self) -> None:
        out = source_font("ABCDEF+Testa", REGULAR, weight=400, italic=False, family="Testa")
        assert out.reuse_allowed
        assert out.missing_chars == frozenset()
        assert out.font_class is FontClass.SANS

    def test_sous_ensemble_et_licence_restreinte(self) -> None:
        data = build_font(chars="abc", licence="All rights reserved", fs_type=0x0004)
        out = source_font("X", data, weight=400, italic=False, family="X")
        assert not out.reuse_allowed
        assert "é" in out.missing_chars

    @pytest.mark.parametrize("embedded", [None, b"pas une police"])
    def test_sans_police_lisible(self, embedded: bytes | None) -> None:
        out = source_font("Georgia", embedded, weight=400, italic=False, family="Georgia")
        assert out.metrics == FontMetrics()
        assert not out.reuse_allowed
        assert out.missing_chars  # tout le français manque


# ---------------------------------------------------------------------------
# Jeu de polices d'un rendu
# ---------------------------------------------------------------------------


class TestJeuDePolices:
    @pytest.fixture
    def font_set(self) -> FontSet:
        return FontSet(
            [
                FontFile("Testa", 400, False, REGULAR),
                FontFile("Testa", 700, False, BOLD),
                FontFile("Autre", 400, True, REGULAR),
            ]
        )

    def test_exacte_puis_la_plus_proche(self, font_set: FontSet) -> None:
        assert len(font_set.files) == 3
        assert font_set.get("Testa", 700, False).data == BOLD  # type: ignore[union-attr]
        assert font_set.get("Testa", 600, False).weight == 700  # type: ignore[union-attr]
        assert font_set.get("Testa", 400, True).italic is False  # type: ignore[union-attr]
        assert font_set.get("Autre", 700, False).italic is True  # type: ignore[union-attr]
        assert font_set.get("Inconnue", 400, False) is None

    def test_boite_de_texte(self, font_set: FontSet) -> None:
        assert font_set.box_ratio("Testa", 400, False) == pytest.approx(1.15)
        assert font_set.box_ratio("Inconnue", 400, False) == 1.17

    def test_largeur_avec_la_police_reelle(self, font_set: FontSet) -> None:
        style = {"family": "Testa", "weight": 400, "size_pt": 10, "letter_spacing_pt": 0}
        natural = FontMeasurer(REGULAR).width_pt("AB", 10) * 25.4 / 72
        assert font_set.width_mm("AB", style) == pytest.approx(natural)
        assert font_set.width_mm("ab", {**style, "uppercase": True}) == pytest.approx(natural)
        assert font_set.width_mm("AB", style, size_pt=20, ls_pt=1) == pytest.approx(
            (FontMeasurer(REGULAR).width_pt("AB", 20) + 1) * 25.4 / 72
        )

    def test_largeur_estimee_sans_police(self, font_set: FontSet) -> None:
        style = {"family": "Inconnue", "weight": 400, "size_pt": 10, "letter_spacing_pt": 1}
        assert font_set.width_mm("abc", style) == pytest.approx((3 * 5.5 + 2) * 25.4 / 72)


def test_la_police_de_test_couvre_le_francais() -> None:
    assert "œ" in FULL_CHARS
    assert "•" in FULL_CHARS
