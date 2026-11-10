"""Analyse d'un PDF de modèle : polices résolues, rôles des lignes, spécification.

La bibliothèque de polices et le modèle de vision sont des doublures : aucune
requête réseau, aucun appel de modèle de langage.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from documents.infrastructure.fakes import (
    BOLD,
    CV_ROLES,
    REGULAR,
    FakePlanner,
    PdfBuilder,
    cv_font_set,
    cv_fonts,
    cv_template_pdf,
)
from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.fonts import FontOrigin, FontResolution, SourceFont
from karria.modules.documents.infrastructure.analyzer import TemplateAnalyzer
from karria.modules.documents.infrastructure.fonts import FontFile
from karria.modules.documents.infrastructure.layout_planner import LayoutPlan
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument

pytestmark = pytest.mark.unit

Json = dict[str, Any]


class FakeLibrary:
    """Résout toute police vers « Testa » et sert les fichiers du jeu de test.

    `extra_weights` : graisses supplémentaires servies (sinon `None`).
    """

    def __init__(
        self, *, extra_weights: tuple[int, ...] = (), missing: tuple[str, ...] = ()
    ) -> None:
        self._fonts = cv_font_set()
        self._extra = extra_weights
        self._missing = missing
        self.resolved: list[SourceFont] = []

    async def resolve_group(self, sources: list[SourceFont]) -> list[FontResolution]:
        return [await self.resolve(s) for s in sources]

    async def resolve(self, source: SourceFont) -> FontResolution:
        self.resolved.append(source)
        family = "Absente" if source.name in self._missing else "Testa"
        return FontResolution(
            source=source.name,
            family=family,
            weight=700 if source.weight >= 600 else 400,
            italic=source.italic,
            origin=FontOrigin.EMBEDDED if source.reuse_allowed else FontOrigin.SUBSTITUTE,
            licence="OFL-1.1",
            distance=None if source.reuse_allowed else 0.1,
            note="test",
        )

    async def file(
        self, family: str, weight: int, italic: bool, *, download: bool = False
    ) -> FontFile | None:
        if family != "Testa":
            return None
        if weight in self._extra:
            base = self._fonts.get("Testa", 400, False)
            assert base is not None
            return FontFile("Testa", weight, italic, base.data)
        found = self._fonts.get(family, weight, italic)
        if found is None or found.weight != weight:
            return None
        return found


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return cv_template_pdf()


class TestResolutionDesPolices:
    async def test_chaque_police_du_pdf_est_resolue_et_servie(self, pdf: bytes) -> None:
        from karria.modules.documents.infrastructure.pdf_reader import PdfDocument  # noqa: PLC0415

        library = FakeLibrary()
        analyzer = TemplateAnalyzer(library, FakePlanner(CV_ROLES))  # type: ignore[arg-type]
        with PdfDocument(pdf) as doc:
            resolutions, files = await analyzer.resolve_fonts(doc)
        assert set(resolutions) == {REGULAR, BOLD}
        assert files[BOLD].weight == 700
        assert files[REGULAR].weight == 400
        bold = next(s for s in library.resolved if s.name == BOLD)
        assert bold.weight == 700
        assert bold.family == "Testa"
        assert bold.reuse_allowed is True


class TestAnalyse:
    async def test_sans_plan_le_modele_de_vision_est_interroge(self, pdf: bytes) -> None:
        planner = FakePlanner(CV_ROLES)
        analyzer = TemplateAnalyzer(FakeLibrary(), planner)  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf)
        ((kind, page_png, measured),) = planner.calls
        assert kind == "plan"
        assert page_png.startswith(b"\x89PNG")
        assert set(measured) == {"lines", "shapes", "images"}
        assert set(measured["lines"][0]) == {
            "id",
            "text",
            "font",
            "size",
            "color",
            "x",
            "y",
            "w",
            "h",
        }
        assert result.unlabeled == []
        assert result.page_png == page_png
        assert result.labels["title"] == "Azur"
        assert result.labels["plan"]["gallery"]["title"] == "Azur"
        assert result.plan is not None
        assert len(result.spec["bands"]) == 2
        assert result.sample["identity"]["first_name"] == "Jean"
        assert "sample_photo.png" in result.assets

    async def test_le_rapport_de_polices_dit_ce_qui_est_servi(self, pdf: bytes) -> None:
        analyzer = TemplateAnalyzer(FakeLibrary(), FakePlanner(CV_ROLES))  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf)
        report = {r["source"]: r for r in result.fonts_report}
        assert report[BOLD]["served"] == "Testa 700"
        assert report[BOLD]["origin"] == "embedded"
        assert report[BOLD]["licence"] == "OFL-1.1"
        assert result.spec["fonts_report"] == result.fonts_report

    async def test_un_plan_donne_evite_le_modele_de_vision(self, pdf: bytes) -> None:
        planner = FakePlanner(CV_ROLES)
        analyzer = TemplateAnalyzer(FakeLibrary(), planner)  # type: ignore[arg-type]
        first = await analyzer.analyze(pdf)
        assert first.plan is not None
        plan = first.plan.model_dump(mode="json")
        for s in plan["bands"][0]["columns"][0]["sections"]:
            for it in s["items"]:
                it["lines"] = [pl for pl in it["lines"] if pl["line"] not in ("L12",)]
        plan["header"]["lines"] = [h for h in plan["header"]["lines"] if h["line"] != "L1"]
        planner.calls.clear()
        again = await analyzer.analyze(pdf, plan=LayoutPlan.model_validate(plan))
        assert planner.calls == []
        assert again.unlabeled == ["L1", "L12"]

    async def test_la_graisse_du_poste_synthetise_est_ajoutee_aux_polices(self, pdf: bytes) -> None:
        analyzer = TemplateAnalyzer(FakeLibrary(extra_weights=(500,)), FakePlanner(CV_ROLES))  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf)
        assert sorted(f.weight for f in result.fonts) == [400, 500, 700]

    async def test_une_graisse_introuvable_nest_pas_ajoutee(self, pdf: bytes) -> None:
        analyzer = TemplateAnalyzer(FakeLibrary(), FakePlanner(CV_ROLES))  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf)
        assert sorted(f.weight for f in result.fonts) == [400, 700]
        ref = next(r for r in result.spec["fonts"] if r["weight"] == 500)
        assert ref["italic"] is False

    async def test_une_police_non_incorporee_et_non_servie(self) -> None:
        """Helvetica : ni octets ni graisse lisible ; la bibliothèque ne la sert pas."""
        b = PdfBuilder(100, 60, embedded=cv_fonts())
        b.text("NOM", 10, 10, size=14, font=BOLD)
        b.text("PROFIL", 10, 30, size=11, font=BOLD)
        b.text("Texte", 10, 37, size=9, font="Helvetica")
        roles = {
            "NOM": ("full_name", "header", 0),
            "PROFIL": ("section_title", "summary", 0),
            "Texte": ("summary", "summary", 0),
        }
        library = FakeLibrary(missing=("Helvetica",))
        analyzer = TemplateAnalyzer(library, FakePlanner(roles))  # type: ignore[arg-type]
        result = await analyzer.analyze(b.to_bytes())
        helv = next(s for s in library.resolved if s.name == "Helvetica")
        assert helv.weight == 400
        assert helv.reuse_allowed is False
        assert {f.family for f in result.fonts} == {"Testa"}
        report = {r["source"]: r for r in result.fonts_report}
        assert report["Helvetica"]["origin"] == "substitute"
        assert report["Helvetica"]["distance"] == 0.1
        assert result.spec["styles"]["summary.summary"]["family"] == "sans-serif"


class TestPreparation:
    async def test_plusieurs_pages_ou_une_page_trop_grande_sont_refusees(self) -> None:
        from pypdf import PdfWriter  # noqa: PLC0415

        analyzer = TemplateAnalyzer(FakeLibrary(), FakePlanner(CV_ROLES))  # type: ignore[arg-type]
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_blank_page(width=100, height=100)
        buffer = io.BytesIO()
        writer.write(buffer)
        with pytest.raises(TemplateIngestionError) as two:
            await analyzer.prepare(buffer.getvalue())
        assert "2 pages" in str(two.value.details["detail"])
        huge = PdfBuilder(600, 600).to_bytes()
        with pytest.raises(TemplateIngestionError) as big:
            await analyzer.prepare(huge)
        assert "trop grande" in str(big.value.details["detail"])


class TestGraisseDesPolicesIncorporees:
    async def test_la_graisse_vient_de_la_police_pas_de_pdfium(self) -> None:
        """Une police nommée sans graisse (« Testa-Italic ») prend celle de sa table OS/2."""
        from documents.infrastructure.fakes import build_font  # noqa: PLC0415

        fonts = {"Testa-Italic": build_font(style="Italic", weight=400, italic_angle=-12)}
        b = PdfBuilder(100, 60, embedded=fonts)
        b.text("NOM", 10, 10, size=14, font="Testa-Italic")
        b.text("PROFIL", 10, 30, size=11, font="Testa-Italic")
        b.text("Texte", 10, 37, size=9, font="Testa-Italic")
        library = FakeLibrary()
        analyzer = TemplateAnalyzer(library, FakePlanner({}))  # type: ignore[arg-type]
        with PdfDocument(b.to_bytes()) as doc:
            await analyzer.resolve_fonts(doc)
        (source,) = library.resolved
        assert (source.weight, source.italic) == (400, True)

    def test_une_police_illisible_na_pas_de_faits(self) -> None:
        from karria.modules.documents.infrastructure.analyzer import embedded_facts  # noqa: PLC0415

        assert embedded_facts(b"pas une police") is None
