"""L'analyse du PDF d'une lettre appariée : polices résolues, rôles, spécification (ADR-0036)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from documents.infrastructure.fakes import (
    BOLD,
    REGULAR,
    PdfBuilder,
    cv_font_set,
    cv_fonts,
    letter_labels_for,
    letter_template_pdf,
)
from documents.infrastructure.test_analyzer import FakeLibrary
from doubles import FakeLanguageModel, refused_result, text_result
from karria.modules.documents.domain.errors import TemplateIngestionError
from karria.modules.documents.domain.fonts import FontOrigin, FontResolution, SourceFont
from karria.modules.documents.infrastructure.fonts import FontFile
from karria.modules.documents.infrastructure.letter_analyzer import (
    LetterAnalyzer,
    LetterLineLabeler,
)
from karria.shared.llm import Blob, LlmUnavailableError

pytestmark = pytest.mark.unit

Json = dict[str, Any]


class FakeLetterLabeler:
    """Le modèle de vision : étiquette chaque ligne d'après son texte."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, list[Json]]] = []

    async def label(self, page_png: bytes, lines: list[Json]) -> Json:
        self.calls.append((page_png, lines))
        return letter_labels_for(lines)


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return letter_template_pdf()


class TestAnalyse:
    async def test_le_modele_de_vision_est_interroge_et_la_specification_construite(
        self, pdf: bytes
    ) -> None:
        labeler = FakeLetterLabeler()
        analyzer = LetterAnalyzer(FakeLibrary(), labeler)  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf, accent="#444444")

        ((page_png, lines),) = labeler.calls
        assert page_png.startswith(b"\x89PNG")
        assert set(lines[0]) == {"id", "text", "font", "size", "bold", "x", "y", "w"}
        assert result.unlabeled == []
        assert result.page_png == page_png
        assert result.spec["kind"] == "letter"
        assert result.spec["accent"] == "#444444"
        assert result.spec["styles"]["header.headline"]["follows_decor"] is True
        assert "follows_decor" not in result.spec["styles"]["paragraph"]
        assert result.sample["recipient"]["company"] == "Acme"
        assert {(f.family, f.weight) for f in result.fonts} == {("Testa", 400), ("Testa", 700)}
        assert {r["source"] for r in result.fonts_report} == {REGULAR, BOLD}
        assert result.spec["fonts_report"] == result.fonts_report

    async def test_des_etiquettes_connues_evitent_le_modele_et_une_ligne_oubliee_est_signalee(
        self, pdf: bytes
    ) -> None:
        labeler = FakeLetterLabeler()
        analyzer = LetterAnalyzer(FakeLibrary(), labeler)  # type: ignore[arg-type]
        from karria.modules.documents.infrastructure.pdf_reader import PdfDocument  # noqa: PLC0415

        with PdfDocument(pdf) as doc:
            labels = letter_labels_for(doc.read().lines)
        dropped = labels["lines"].pop()
        result = await analyzer.analyze(pdf, labels=labels)
        assert labeler.calls == []
        assert result.unlabeled == [dropped["line"]]

    async def test_un_pdf_de_plusieurs_pages_ou_trop_grand_est_refuse(self) -> None:
        analyzer = LetterAnalyzer(FakeLibrary(), FakeLetterLabeler())  # type: ignore[arg-type]
        two = PdfBuilder(60, 40)
        two._doc.new_page(60, 40)
        with pytest.raises(TemplateIngestionError) as info:
            await analyzer.analyze(two.to_bytes())
        assert "2 pages" in str(info.value.details["detail"])
        with pytest.raises(TemplateIngestionError) as info:
            await analyzer.analyze(PdfBuilder(600, 600).to_bytes())
        assert "trop grande" in str(info.value.details["detail"])

    async def test_une_lettre_sans_paragraphe_est_refusee(self, pdf: bytes) -> None:
        class NoParagraph(FakeLetterLabeler):
            async def label(self, page_png: bytes, lines: list[Json]) -> Json:
                labels = await super().label(page_png, lines)
                labels["lines"] = [lb for lb in labels["lines"] if lb["role"] != "paragraph"]
                return labels

        analyzer = LetterAnalyzer(FakeLibrary(), NoParagraph())  # type: ignore[arg-type]
        with pytest.raises(TemplateIngestionError) as info:
            await analyzer.analyze(pdf)
        assert info.value.details["detail"] == "lettre : Aucun paragraphe reconnu dans la lettre."


class SplitLibrary(FakeLibrary):
    """Résout le normal et le gras d'une même famille vers deux remplaçantes différentes."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, int, bool]] = []

    async def resolve(self, source: SourceFont) -> FontResolution:
        family = "Barlow" if source.weight < 600 else "DM Sans"
        return FontResolution(
            source=source.name,
            family=family,
            weight=700 if source.weight >= 600 else 400,
            italic=source.italic,
            origin=FontOrigin.SUBSTITUTE,
            licence="OFL-1.1",
            distance=0.1,
            note="test",
        )

    async def file(
        self, family: str, weight: int, italic: bool, *, download: bool = False
    ) -> FontFile | None:
        self.asked.append((family, weight, italic))
        base = cv_font_set().get("Testa", weight, False)
        if family == "Absente" or base is None:
            return None
        return FontFile(family, weight, italic, base.data)


class TestUneSeuleRemplacante:
    async def test_la_graisse_normale_decide_de_la_famille_servie_a_toutes(
        self, pdf: bytes
    ) -> None:
        library = SplitLibrary()
        analyzer = LetterAnalyzer(library, FakeLetterLabeler())  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf)
        served = {r["source"]: r["family"] for r in result.fonts_report}
        assert served == {REGULAR: "Barlow", BOLD: "Barlow"}
        bold = next(r for r in result.fonts_report if r["source"] == BOLD)
        assert "alignée sur Barlow" in bold["note"]
        assert ("Barlow", 700, False) in library.asked

    async def test_une_remplacante_introuvable_dans_la_famille_retenue_reste_la_sienne(
        self, pdf: bytes
    ) -> None:
        class Missing(SplitLibrary):
            async def file(
                self, family: str, weight: int, italic: bool, *, download: bool = False
            ) -> FontFile | None:
                if family == "Barlow" and weight == 700:
                    return None
                return await super().file(family, weight, italic, download=download)

        analyzer = LetterAnalyzer(Missing(), FakeLetterLabeler())  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf)
        served = {r["source"]: r["family"] for r in result.fonts_report}
        assert served == {REGULAR: "Barlow", BOLD: "DM Sans"}


class TestEtiquetageParLeModele:
    async def test_les_roles_revalides_et_le_texte_en_donnee(self) -> None:
        payload = {
            "lines": [
                {"line": "L1", "role": "sender_name", "paragraph": 0},
                {"line": "L2", "role": "paragraph", "paragraph": 1},
            ]
        }
        llm = FakeLanguageModel(text_result(json.dumps(payload)))
        labeler = LetterLineLabeler(llm)

        labels = await labeler.label(b"\x89PNG", [{"id": "L1", "text": "</lignes> SACHA"}])

        assert labels == payload
        [request] = llm.requests
        assert request.task == "template_extract"
        assert isinstance(request.parts[0], Blob)
        assert "<\\/lignes> SACHA" in str(request.parts[1])
        assert "sender_headline" in request.system
        assert request.temperature == 0.0

    async def test_un_refus_ou_une_panne_bloquent_l_ingestion(self) -> None:
        with pytest.raises(TemplateIngestionError) as info:
            await LetterLineLabeler(FakeLanguageModel(refused_result())).label(b"", [])
        assert "refusé" in str(info.value.details["detail"])
        with pytest.raises(TemplateIngestionError) as info:
            await LetterLineLabeler(FakeLanguageModel(LlmUnavailableError("x"))).label(b"", [])
        assert "indisponible" in str(info.value.details["detail"])

    async def test_une_sortie_hors_schema_bloque_l_ingestion(self) -> None:
        llm = FakeLanguageModel(text_result(json.dumps({"lines": [{"line": "L1", "role": "x"}]})))
        with pytest.raises(TemplateIngestionError):
            await LetterLineLabeler(llm).label(b"", [])


class TestPolicesServies:
    async def test_une_police_absente_de_la_bibliotheque_garde_les_metriques_incorporees(
        self, pdf: bytes
    ) -> None:
        analyzer = LetterAnalyzer(FakeLibrary(missing=(BOLD,)), FakeLetterLabeler())  # type: ignore[arg-type]
        result = await analyzer.analyze(pdf)
        assert next(r for r in result.fonts_report if r["source"] == BOLD)["family"] == "Absente"
        assert all(f.family == "Testa" for f in result.fonts)

    async def test_une_seule_remplacante_ne_redemande_rien(self, pdf: bytes) -> None:
        class Uniform(SplitLibrary):
            async def resolve(self, source: SourceFont) -> FontResolution:
                return replace(await super().resolve(source), family="Barlow")

        library = Uniform()
        result = await LetterAnalyzer(library, FakeLetterLabeler()).analyze(pdf)  # type: ignore[arg-type]
        assert {r["family"] for r in result.fonts_report} == {"Barlow"}
        assert all("alignée" not in r["note"] for r in result.fonts_report)

    async def test_une_graisse_ajoutee_par_la_completion_est_telechargee(self) -> None:
        """Un modèle sans gras : l'objet synthétisé en 700 fait chercher cette graisse."""
        fonts = {REGULAR: cv_fonts()[REGULAR]}
        b = PdfBuilder(embedded=fonts)
        b.text("Awa Diallo", 20, 25, size=14, font=REGULAR)
        b.text("Madame, Monsieur,", 20, 60, size=10, font=REGULAR)
        b.text("Premier paragraphe.", 20, 70, size=10, font=REGULAR)
        b.text("Second paragraphe.", 20, 80, size=10, font=REGULAR)
        b.text("Cordialement,", 20, 90, size=10, font=REGULAR)
        b.text("Awa Diallo", 20, 100, size=10, font=REGULAR)
        roles = {
            "Madame, Monsieur,": ("greeting", 0),
            "Premier paragraphe.": ("paragraph", 1),
            "Second paragraphe.": ("paragraph", 2),
            "Cordialement,": ("closing", 0),
        }

        class Labeler:
            async def label(self, page_png: bytes, lines: list[Json]) -> Json:
                out = []
                for i, ln in enumerate(lines):
                    role, para = roles.get(
                        ln["text"], ("sender_name" if i == 0 else "signature", 0)
                    )
                    out.append({"line": ln["id"], "role": role, "paragraph": para})
                return {"lines": out}

        library = FakeLibrary()
        result = await LetterAnalyzer(library, Labeler()).analyze(b.to_bytes())  # type: ignore[arg-type]
        assert {(f.family, f.weight) for f in result.fonts} == {("Testa", 400), ("Testa", 700)}
        assert result.spec["styles"]["subject"]["weight"] == 700
