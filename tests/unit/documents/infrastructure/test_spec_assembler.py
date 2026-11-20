"""Plan de mise en page + mesures → spécification (ADR-0037), sur le modèle « frise ».

Ce que le constructeur d'avant perdait et que l'assembleur rend : une rubrique
sans titre, une rubrique libre avec son titre, une frise à colonne latérale,
un poste visé sur deux lignes, des coordonnées alignées à droite, un filet de
bande qui s'étire, des séparateurs de bandes, un fond de page fixe. Les deux
moteurs rendent ensuite cette spécification sans erreur.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from documents.infrastructure.fakes import (
    BOLD,
    REGULAR,
    SERVED,
    cv_font_set,
    timeline_plan,
    timeline_template_pdf,
)
from karria.modules.documents.domain.view import from_cv, from_sample
from karria.modules.documents.infrastructure.docx_renderer import DocxRenderer
from karria.modules.documents.infrastructure.fonts import FontSet
from karria.modules.documents.infrastructure.html_renderer import (
    HtmlRenderer,
    estimate,
    estimate_pages,
)
from karria.modules.documents.infrastructure.layout_planner import LayoutPlan
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument
from karria.modules.documents.infrastructure.spec_assembler import SpecAssembler

pytestmark = pytest.mark.unit

Json = dict[str, Any]

DECORATIONS: list[Json] = [
    {"shape": "D1", "anchor": "band_separator", "band": 0, "section": None, "item": None},
    {"shape": "D6", "anchor": "band_separator", "band": 1, "section": None, "item": None},
    {"shape": "D7", "anchor": "band", "band": 2, "section": None, "item": None},
    {"shape": "D14", "anchor": "band_separator", "band": 2, "section": None, "item": None},
    {"shape": "D15", "anchor": "page", "band": None, "section": None, "item": None},
]


def build(plan_changes: Any = None, **plan_kw: Any) -> tuple[Json, Json, dict[str, bytes], Any]:
    """La spécification du modèle « frise », avec un plan éventuellement retouché."""
    pdf = timeline_template_pdf()
    with PdfDocument(pdf) as doc:
        page = doc.read()
        plan = timeline_plan(page.lines)
        plan["decorations"] = plan_kw.pop("decorations", DECORATIONS)
        plan.update(plan_kw)
        if plan_changes:
            plan_changes(plan)
        assembler = SpecAssembler(doc, page, LayoutPlan.model_validate(plan), cv_font_set(), SERVED)
        spec, sample, assets = assembler.build()
        return spec, sample, assets, assembler


@pytest.fixture(scope="module")
def built() -> tuple[Json, Json, dict[str, bytes], Any]:
    return build()


@pytest.fixture
def spec(built: tuple) -> Json:
    return copy.deepcopy(built[0])


@pytest.fixture
def sample(built: tuple) -> Json:
    return copy.deepcopy(built[1])


@pytest.fixture(scope="module")
def fonts() -> FontSet:
    return cv_font_set()


def sections(spec: Json) -> dict[str, Json]:
    return {s["kind"]: s for b in spec["bands"] for c in b["columns"] for s in c["sections"]}


class TestBandesEtColonnes:
    def test_trois_bandes_comme_le_plan(self, spec: Json) -> None:
        kinds = [[[s["kind"] for s in c["sections"]] for c in b["columns"]] for b in spec["bands"]]
        assert kinds == [
            [["summary"]],
            [["other:realisations-cles"], ["skills"]],
            [["experiences", "educations"]],
        ]

    def test_la_premiere_bande_commence_au_profil_pas_a_un_titre(self, spec: Json) -> None:
        assert spec["first_band_top_mm"] == pytest.approx(41.8, abs=0.3)
        assert spec["bands"][0]["y_sample"] == spec["first_band_top_mm"]

    def test_les_colonnes_sont_mesurees_par_leurs_lignes(self, spec: Json) -> None:
        left, right = spec["bands"][1]["columns"]
        assert left["x_mm"] == pytest.approx(16.0, abs=0.2)
        assert left["x_mm"] + left["w_mm"] <= right["x_mm"] - 2
        assert right["x_mm"] == pytest.approx(105.0, abs=0.2)
        (col,) = spec["bands"][2]["columns"]
        # Le contenu commence au titre ; la colonne latérale est à gauche, en négatif.
        assert col["x_mm"] == pytest.approx(54.0, abs=0.2)

    def test_les_ecarts_entre_bandes(self, spec: Json) -> None:
        first, second, third = spec["bands"]
        assert first["gap_after_mm"] == pytest.approx(second["y_sample"] - first["bottom_sample"])
        assert "gap_after_mm" not in third


class TestRubriques:
    def test_le_profil_sans_titre(self, spec: Json, sample: Json) -> None:
        summary = sections(spec)["summary"]
        assert summary["untitled"] is True
        assert summary["title_sample"] == ""
        assert summary["margin_top_mm"] == 0.0
        assert summary["first_gap_mm"] == 0.0
        assert sample["summary"].startswith("Commercial dynamique")

    def test_la_rubrique_libre_garde_son_titre_et_ses_elements(
        self, spec: Json, sample: Json
    ) -> None:
        other = sections(spec)["other:realisations-cles"]
        assert other["title_sample"] == "REALISATIONS CLES"
        assert other["marker"]["shape"] == "circle"
        assert sample["others"]["other:realisations-cles"]["title"] == "REALISATIONS CLES"
        items = sample["others"]["other:realisations-cles"]["items"]
        assert [it["bullets"] for it in items] == [
            ["Chiffre en hausse"],
            ["Prix de la performance"],
        ]

    def test_la_frise_a_sa_colonne_laterale(self, spec: Json, sample: Json) -> None:
        exp = sections(spec)["experiences"]
        side = exp["side"]
        assert side["dx_mm"] < -10
        assert side["dx_mm"] + side["w_mm"] == pytest.approx(41.0 - 54.0 + 0.5, abs=0.3)
        assert side["align"] == "right"
        assert side["slot_order"] == ["item_subtitle", "item_dates"]
        assert side["slots"][1]["margin_top_mm"] > 0
        assert side["top_offset_mm"] == pytest.approx(0.0, abs=0.2)
        # Les emplacements du contenu ne mêlent plus la colonne latérale.
        assert exp["slot_order"] == ["item_title", "bullet"]
        assert sample["experiences"][0]["subtitle_raw"] == "Bio Sentech"
        assert sample["experiences"][0]["dates_raw"] == "Depuis 2021"
        assert sample["experiences"][1]["bullets"] == ["Gestion de secteur"]
        edu = sections(spec)["educations"]
        assert edu["side"]["align"] == "right"
        assert sample["educations"][0]["location"] == "Lyon"

    def test_la_pastille_de_frise_sattache_a_lintitule_du_contenu(self, spec: Json) -> None:
        dot = next(
            d for d in sections(spec)["experiences"]["decorations"] if d.get("attach") == "row"
        )
        assert dot["ref_role"] == "item_title"
        assert dot["dx_mm"] == pytest.approx(46.35 - 54.0, abs=0.3)

    def test_une_rubrique_dont_les_lignes_sont_prises_ailleurs_disparait(self) -> None:
        def change(plan: Json) -> None:
            first = plan["bands"][1]["columns"][0]["sections"][0]
            plan["bands"][1]["columns"][1]["sections"].append({**first, "ref": "S8"})

        spec, _, _, _ = build(change)
        assert [s["kind"] for s in spec["bands"][1]["columns"][1]["sections"]] == ["skills"]

    def test_deux_rubriques_libres_du_meme_titre_se_distinguent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from karria.modules.documents.infrastructure import spec_assembler  # noqa: PLC0415

        monkeypatch.setattr(spec_assembler, "slugify", lambda _text: "meme-titre")

        def change(plan: Json) -> None:
            plan["bands"][1]["columns"][1]["sections"][0].update(kind="other")

        spec, _, _, _ = build(change)
        assert set(sections(spec)) >= {"other:meme-titre", "other:meme-titre-2"}

    def test_une_rubrique_libre_sans_titre_prend_un_nom_sur_sans_son_identifiant(self) -> None:
        def change(plan: Json) -> None:
            plan["bands"][1]["columns"][0]["sections"][0]["title_lines"] = []

        spec, _, _, assembler = build(change)
        # Le deuxième de la page ; jamais le `ref` du plan.
        assert "other:section-2" in sections(spec)
        assert "other:s2" not in sections(spec)
        assert "L9" in assembler.check.unplaced

    def test_une_seconde_rubrique_libre_sans_titre_prend_son_rang(self) -> None:
        def change(plan: Json) -> None:
            first = plan["bands"][1]["columns"][0]["sections"][0]
            plan["bands"][1]["columns"][1]["sections"][0] = {
                **first,
                "ref": "S9",
                "items": [{"lines": [{"line": "L12", "role": "bullet"}]}],
                "title_lines": [],
            }
            plan["bands"][1]["columns"][1]["sections"][0]["kind"] = "other"

        spec, _, _, _ = build(change)
        assert "other:section-3" in sections(spec)
        assert "other:s9" not in sections(spec)


class TestDecorations:
    def test_le_filet_de_frise_court_le_long_de_la_bande(self, spec: Json) -> None:
        (rule,) = spec["bands"][2]["decorations"]
        assert rule["kind"] == "vline"
        assert rule["stretch"] is True
        assert rule["dy_mm"] == pytest.approx(100.0 - spec["bands"][2]["y_sample"], abs=0.3)
        assert rule["bottom_inset_mm"] == pytest.approx(
            spec["bands"][2]["bottom_sample"] - 190.0, abs=0.3
        )

    def test_les_separateurs_sont_ancres_a_leur_bande(self, spec: Json) -> None:
        seps = [d for d in spec["page_decorations"] if d["anchor"] == "band"]
        assert [d["band"] for d in seps] == [0, 1, 2]
        assert seps[0]["dy_mm"] == pytest.approx(60.0 - spec["bands"][0]["bottom_sample"], abs=0.3)

    def test_le_fond_fixe_reste_sur_la_page(self, spec: Json) -> None:
        page = [d for d in spec["page_decorations"] if d["anchor"] == "page"]
        assert [d["fill"] for d in page] == ["#EEEEEE"]

    def test_un_ancrage_de_flux_setire_avec_le_contenu(self) -> None:
        decorations = [
            {**DECORATIONS[2], "anchor": "flow", "band": None},
            *DECORATIONS[:2],
            *DECORATIONS[3:],
        ]
        spec, _, _, _ = build(decorations=decorations)
        (flow,) = [d for d in spec["page_decorations"] if d["anchor"] == "flow"]
        assert flow["stretch"] is True
        assert flow["dy_mm"] == pytest.approx(100.0 - spec["first_band_top_mm"], abs=0.3)
        assert flow["bottom_inset_mm"] == pytest.approx(spec["content_bottom_mm"] - 190.0, abs=0.3)
        assert spec["bands"][2]["decorations"] == []

    def test_un_filet_ancre_a_une_rubrique_ou_sous_elle(self) -> None:
        decorations = [
            {"shape": "D7", "anchor": "section", "band": None, "section": "S4", "item": None},
            {"shape": "D6", "anchor": "after_section", "band": None, "section": "S2", "item": None},
            {"shape": "D14", "anchor": "item", "band": None, "section": "S5", "item": 0},
        ]
        spec, _, _, _ = build(decorations=decorations)
        rule = next(d for d in sections(spec)["experiences"]["decorations"] if d["kind"] == "vline")
        assert rule["anchor"] == "section"
        assert rule["stretch"] is True
        after = next(
            d
            for d in sections(spec)["other:realisations-cles"]["decorations"]
            if d["kind"] == "hline"
        )
        assert after["anchor"] == "after"
        assert after["dy_mm"] > 0
        # Une forme « d'élément » que la mesure n'a pas reconnue se range avec la rubrique.
        hline = next(d for d in sections(spec)["educations"]["decorations"] if d["kind"] == "hline")
        assert hline["anchor"] == "section"

    def test_une_forme_inconnue_une_bande_hors_champ_ou_un_doublon_sont_ignores(self) -> None:
        decorations = [
            {"shape": "D99", "anchor": "page", "band": None, "section": None, "item": None},
            {"shape": "D7", "anchor": "band", "band": 7, "section": None, "item": None},
            {"shape": "D7", "anchor": "flow", "band": None, "section": None, "item": None},
            {
                "shape": "D15",
                "anchor": "band_separator",
                "band": None,
                "section": None,
                "item": None,
            },
        ]
        spec, _, _, _ = build(decorations=decorations)
        anchors = [(d["kind"], d["anchor"]) for d in spec["page_decorations"]]
        assert ("vline", "page") in anchors
        assert ("rect", "page") in anchors
        assert all(d["anchor"] != "flow" for d in spec["page_decorations"])

    def test_sans_plan_de_decoration_les_filets_retombent_sur_les_regles(self) -> None:
        spec, _, _, _ = build(decorations=[])
        kinds = {(d["kind"], d["anchor"]) for d in spec["page_decorations"]}
        assert ("rect", "page") in kinds
        assert any(k == "hline" for k, _ in kinds)


class TestEnTete:
    def test_le_poste_vise_sur_deux_lignes_est_un_seul_champ(
        self, spec: Json, sample: Json
    ) -> None:
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        assert headline["box_w_mm"] == pytest.approx(headline["w_mm"] + 1.5, abs=0.01)
        assert headline["pitch_mm"] == pytest.approx(5.0, abs=0.1)
        assert headline["align"] == "left"
        assert [sl["text"] for sl in headline["sample_lines"]] == ["CHARGE DE", "PROJET"]
        assert sample["identity"]["headline"] == "CHARGE DE PROJET"
        assert [e["field"] for e in spec["header"]["elements"]].count("headline") == 1
        assert spec["styles"]["header.headline"]["pitch_mm"] == pytest.approx(5.0, abs=0.1)

    def test_les_coordonnees_alignees_a_droite_le_restent(self, spec: Json, sample: Json) -> None:
        by_field = {e["field"]: e for e in spec["header"]["elements"]}
        for field in ("phone", "email", "address"):
            assert by_field[field]["align"] == "right"
            assert by_field[field]["rx_mm"] == pytest.approx(191.0, abs=0.5)
        assert "align" not in by_field["full_name"]
        assert sample["contact"] == {
            "phone": "+123-456-7890",
            "email": "hello@site.com",
            "city": "Any City",
        }
        assert sample["identity"]["first_name"] == "Romain"

    def test_un_site_prend_la_ligne_sous_la_derniere_coordonnee(self, spec: Json) -> None:
        by_field = {e["field"]: e for e in spec["header"]["elements"]}
        website = by_field["website"]
        assert website["synthesized"] is True
        assert website["style"] == by_field["address"]["style"]
        assert website["align"] == "right"
        assert website["rx_mm"] == by_field["address"]["rx_mm"]
        assert website["y_mm"] == pytest.approx(by_field["address"]["y_mm"] + 5.0, abs=0.3)
        assert website["pitch_mm"] == pytest.approx(5.0, abs=0.3)

    def test_sans_coordonnee_dans_lentete_aucun_site_synthetise(self) -> None:
        def change(plan: Json) -> None:
            plan["header"]["lines"] = [
                h for h in plan["header"]["lines"] if h["field"] in ("full_name", "headline")
            ]

        spec, _, _, _ = build(change)
        assert "website" not in {e["field"] for e in spec["header"]["elements"]}
        assert SpecAssembler.synthesize_website(
            [{"field": "phone", "style": "s", "x_mm": 1.0, "y_mm": 2.0, "w_mm": 3.0}]
        ) == {
            "field": "website",
            "style": "s",
            "x_mm": 1.0,
            "y_mm": 2.0,
            "w_mm": 3.0,
            "synthesized": True,
            "pitch_mm": None,
        }

    def test_la_photo_est_celle_que_le_plan_designe(self, spec: Json) -> None:
        photo = spec["header"]["photo"]
        assert (photo["x_mm"], photo["y_mm"], photo["w_mm"]) == (15.0, 15.0, 30.0)
        assert photo["shape"] == "rect"

    def test_sans_photo_dans_le_plan_aucune_photo(self) -> None:
        for pid in (None, "I9", "X"):
            spec, _, assets, _ = (
                build(header_photo=pid)
                if False
                else build(lambda plan, pid=pid: plan["header"].__setitem__("photo", pid))
            )
            assert spec["header"]["photo"] is None
            assert "sample_photo.png" not in assets

    def test_un_nom_centre_sur_son_poste(self) -> None:
        def change(plan: Json) -> None:
            plan["header"]["lines"] = [h for h in plan["header"]["lines"] if h["line"] != "L4"]

        spec, _, _, _ = build(change)
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        assert "box_w_mm" not in headline

    def test_des_lignes_de_champ_centrees_ou_a_droite(self) -> None:
        from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

        def line(x: float, w: float) -> Line:
            raw = {"id": "L", "text": "t", "x": x, "y": 0.0, "w": w, "h": 3.0}
            return Line(raw, {"role": "headline", "section": "header", "item": 0})

        assert SpecAssembler.lines_alignment([line(10, 30), line(20, 20)]) == "right"
        assert SpecAssembler.lines_alignment([line(10, 30), line(15, 20)]) == "center"
        assert SpecAssembler.lines_alignment([line(10, 30), line(10, 10)]) == "left"
        elements = [
            {"field": "a", "x_mm": 10.0, "y_mm": 10.0, "w_mm": 30.0},
            {"field": "b", "x_mm": 15.0, "y_mm": 20.0, "w_mm": 20.0},
        ]
        SpecAssembler.align_header_elements(elements)
        assert elements[0]["align"] == "center"
        assert elements[0]["cx_mm"] == 25.0

    def test_un_champ_sur_deux_lignes_centre_ou_a_droite(self, built: tuple) -> None:
        from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

        assembler = built[3]

        def line(x: float, w: float, y: float) -> Line:
            raw = {
                "id": "L",
                "text": "AB",
                "font": "Testa-Regular",
                "size": 10.0,
                "color": "#000000",
                "x": x,
                "y": y,
                "w": w,
                "h": 4.0,
                "caps": True,
                "chars": [],
            }
            return Line(raw, {"role": "headline", "section": "header", "item": 0})

        sample: Json = {"identity": {}, "contact": {}}
        centered = assembler.header_element(
            "headline", [line(10, 30, 0), line(15, 20, 5)], {}, sample
        )
        assert centered["align"] == "center"
        assert centered["cx_mm"] == 25.0
        right = assembler.header_element(
            "first_name", [line(10, 30, 0), line(20, 20, 5)], {}, sample
        )
        assert right["align"] == "right"
        assert right["rx_mm"] == 40.0
        assert sample["identity"]["first_name"] == "Ab Ab"

    def test_une_ligne_d_en_tete_inconnue_est_ignoree(self) -> None:
        def change(plan: Json) -> None:
            plan["header"]["lines"].append({"line": "L99", "field": "website"})

        spec, _, _, assembler = build(change)
        assert assembler.check.unknown == ["L99"]
        # Le seul « site » de l'en-tête est la ligne synthétisée, pas la ligne inconnue.
        websites = [e for e in spec["header"]["elements"] if e["field"] == "website"]
        assert [e.get("synthesized") for e in websites] == [True]


class TestVerification:
    def test_une_ligne_non_placee_est_relevee(self) -> None:
        def change(plan: Json) -> None:
            plan["bands"][1]["columns"][1]["sections"][0]["items"][0]["lines"].pop()

        _, _, _, assembler = build(change)
        assert assembler.check.unplaced == ["L14"]
        assert assembler.labels["plan"]["gallery"]["title"] == "Frise"

    def test_une_ligne_en_double_reste_a_sa_premiere_place(self) -> None:
        def change(plan: Json) -> None:
            plan["bands"][1]["columns"][1]["sections"][0]["items"][0]["lines"].append(
                {"line": "L11", "role": "skill"}
            )

        _, sample, _, assembler = build(change)
        assert assembler.check.duplicated == ["L11"]
        assert [s["name"] for s in sample["skills"]] == ["Negociation", "CRM"]

    def test_un_plan_sans_rubrique_echoue(self) -> None:
        def change(plan: Json) -> None:
            plan["bands"] = [{"columns": [{"sections": []}]}]

        with pytest.raises(ValueError, match="rubrique"):
            build(change)

    def test_sans_rubrique_titree_un_style_de_titre_est_derive(self) -> None:
        def change(plan: Json) -> None:
            plan["bands"] = plan["bands"][:1]

        spec, _, _, _ = build(change)
        assert spec["styles"]["section_title"]["uppercase"] is True
        assert spec["styles"]["section_title"]["family"] == "Testa"

    def test_le_plan_est_garde_dans_les_etiquettes(self, built: tuple) -> None:
        labels = built[3].labels
        assert labels["columns"] == 2
        assert json.dumps(labels["plan"])


class TestRendus:
    def test_le_titre_qui_chevauche_la_coupure_garde_sa_marge_dans_le_pdf(
        self,
        spec: Json,
        sample: Json,
        fonts: FontSet,
        tmp_path: Path,
    ) -> None:
        chromium = shutil.which("chromium") or shutil.which("google-chrome")
        if chromium is None:
            pytest.skip("Chromium n'est pas disponible pour l'impression du PDF")

        view = from_sample(spec, sample)
        experiences = next(s for s in view["sections"] if s["kind"] == "experiences")
        experiences["items"] *= 4
        page = tmp_path / "cv.html"
        page.write_text(HtmlRenderer(spec, view, fonts, photo=None).render(), encoding="utf-8")
        for font in fonts.files:
            (tmp_path / font.file_name).write_bytes(font.data)
        output = tmp_path / "cv.pdf"
        # Le profil est séparé du profil personnel et son chemin reste court
        # pour les sockets Unix que Chromium y crée.
        with tempfile.TemporaryDirectory(prefix="cv-print-") as profile:
            printed = subprocess.run(  # noqa: S603 - moteur et document locaux
                [
                    str(Path(chromium).resolve()),
                    "--headless",
                    "--no-first-run",
                    "--disable-background-networking",
                    f"--user-data-dir={profile}",
                    "--virtual-time-budget=3000",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={output}",
                    page.as_uri(),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        assert printed.returncode == 0, printed.stderr

        with PdfDocument(output.read_bytes()) as pdf:
            assert pdf.page_count == 2
            lines = pdf.text_lines(1)
            title = next(line for line in lines if line["text"] == "FORMATION")
            assert title["y"] >= 10  # mm, avec la tolérance de rendu des glyphes
            assert any("Master en commerce" in line["text"] for line in lines)

    def test_le_html_de_lexemple_rend_frise_colonne_laterale_et_filets(
        self, spec: Json, sample: Json, fonts: FontSet
    ) -> None:
        html = HtmlRenderer(spec, from_sample(spec, sample), fonts, photo="p.png").render()
        assert 'class="side"' in html
        # Une rubrique libre a une classe de style valide (pas de « : », pas de « . »).
        assert ".s-other-realisations-cles-bullet{" in html
        assert 'class="t s-other-realisations-cles-bullet"' in html
        assert "other:" not in html
        side = sections(spec)["experiences"]["side"]
        assert f"display:flex;align-items:flex-start;margin-left:{side['dx_mm']:.2f}mm" in html
        assert "REALISATIONS CLES" in html
        assert html.count("CHARGE DE") == 1
        assert "PROJET" in html
        assert "right:" in html  # coordonnées alignées à droite
        assert "bottom:" in html  # filet de bande étiré
        assert "Commercial dynamique" in html
        # Le profil sans titre ne reçoit aucun titre, ni « Profil ».
        assert ">Profil<" not in html

    def test_le_html_dun_vrai_cv_passe_par_la_boite_du_poste_vise(
        self, spec: Json, sample: Json, fonts: FontSet
    ) -> None:
        cv = {
            "identity": {"first_name": "Ada", "last_name": "Lovelace", "headline": "Ingénieure"},
            "contact": {
                "phone": "01",
                "email": "a@b.fr",
                "city": "Londres",
                "links": [{"url": "https://www.linkedin.com/in/ada/"}],
            },
            "summary": "Un profil.",
            "experiences": [
                {
                    "position": "Analyste",
                    "company": "Babbage",
                    "start_date": "2020-01",
                    "missions": ["Machine analytique"],
                }
            ],
            "educations": [{"degree": "Maths", "school": "Maison", "start_date": "2015"}],
            "skills": [{"name": "Python"}],
            "languages": [{"language": "Français", "level": "Natif"}],
        }
        view = from_cv(spec, cv)
        html = HtmlRenderer(spec, view, fonts, photo=None).render()
        # Un poste visé court tient sur une ligne dans sa boîte (14/11 : il ne se
        # replie qu'en dernier recours) ; la boîte garde l'interligne de l'exemple.
        assert re.search(
            r'class="s-header-title-headline"[^>]*white-space:nowrap">Ingénieure<', html
        )
        # Le poste visé d'une personne s'étend à toute la place disponible, pas
        # à la boîte de l'exemple ; le titre d'une rubrique prend la colonne.
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        assert f"width:{headline['box_w_mm']:.2f}mm" not in html
        sample_html = HtmlRenderer(spec, from_sample(spec, sample), fonts, photo=None).render()
        assert f"width:{headline['box_w_mm']:.2f}mm" not in sample_html  # lignes exactes
        assert "Ingénieure" in html
        assert "Babbage" in html
        assert "Machine analytique" in html
        # Les coordonnées sont dans l'en-tête : aucune rubrique « Contact » empruntée,
        # et le site prend la ligne synthétisée sous l'adresse.
        assert ">Contact<" not in html
        assert "linkedin.com/in/ada" in html
        assert estimate_pages(spec, view, fonts) == 1
        exp = sections(spec)["experiences"]
        sec = next(s for s in view["sections"] if s["kind"] == "experiences")
        assert estimate(spec["styles"], exp, sec, 130.0, fonts) > 0

    def test_le_docx_rend_la_meme_specification(
        self, spec: Json, sample: Json, fonts: FontSet
    ) -> None:
        docx = DocxRenderer(spec, from_sample(spec, sample), fonts, photo=None).render({})
        assert docx.startswith(b"PK")

    def test_un_filet_court_de_bande_et_un_filet_de_rubrique_sans_titre(
        self, fonts: FontSet
    ) -> None:
        decorations = [
            {"shape": "D14", "anchor": "band", "band": 2, "section": None, "item": None},
            {"shape": "D1", "anchor": "section", "band": None, "section": "S1", "item": None},
        ]
        spec, sample, _, _ = build(decorations=decorations)
        (short,) = spec["bands"][2]["decorations"]
        assert short["stretch"] is False
        (rule,) = sections(spec)["summary"]["decorations"]
        assert rule["anchor"] == "section"
        view = from_sample(spec, sample)
        html = HtmlRenderer(spec, view, fonts, photo=None).render()
        assert "border-top" in html
        docx = DocxRenderer(spec, view, fonts, photo=None).render({})
        assert docx.startswith(b"PK")
        # Une rubrique sans titre et sans contenu ne laisse rien derrière elle.
        empty = {**view, "sections": [{"kind": "summary", "title": "", "items": [{}]}]}
        assert DocxRenderer(spec, empty, fonts, photo=None).render({}).startswith(b"PK")

    def test_un_element_de_frise_sans_champ_lateral(self, spec: Json, fonts: FontSet) -> None:
        cv = {
            "identity": {"first_name": "Ada", "last_name": "Lovelace"},
            "contact": {},
            "experiences": [{"position": "Analyste", "company": "Babbage"}],
            "educations": [{"degree": "Maths", "school": "Maison"}],
        }
        view = from_cv(spec, cv)
        exp = next(s for s in view["sections"] if s["kind"] == "experiences")
        # L'entreprise a son emplacement dans la colonne latérale : l'intitulé reste nu.
        assert exp["items"][0]["item_title"] == "Analyste"
        assert exp["items"][0]["item_subtitle"] == "Babbage"
        html = HtmlRenderer(spec, view, fonts, photo=None).render()
        assert '<div class="side" style="flex:none;width:' in html


def test_une_colonne_laterale_alignee_a_gauche(built: tuple) -> None:
    assembler = built[3]

    def elem(role: str, x: float, right: float, top: float) -> Json:
        return {"role": role, "x": x, "right": right, "top": top, "bottom": top + 4, "lines": []}

    side = assembler.side_spec(
        {1: [elem("item_dates", 20.0, 30.0, 100.0), elem("item_location", 20.0, 36.0, 106.0)]},
        {1: [elem("item_title", 54.0, 90.0, 99.0)]},
        54.0,
    )
    assert side["align"] == "left"
    assert side["top_offset_mm"] == 1.0
    assert side["slot_order"] == ["item_dates", "item_location"]


def test_le_plan_prime_sur_la_regle_pour_un_filet_de_rubrique() -> None:
    """Un filet vertical dans l'élément : connecteur d'élément par la règle, filet de rubrique par le plan."""
    from documents.infrastructure.test_spec_builder import FakeDoc, drawing, raw_line  # noqa: PLC0415
    from karria.modules.documents.infrastructure.pdf_reader import PdfPage  # noqa: PLC0415

    lines = [
        raw_line("L1", "JEAN", 20, 10, h=8, size=20),
        raw_line("L2", "EXPERIENCES", 20, 50, size=11, caps=True),
        raw_line("L3", "Poste", 25, 57),
        raw_line("L4", "Mission", 25, 62),
    ]
    rule = drawing("vline", 21, 56, 0, 9, stroke="#000000")
    plan: Json = {
        "gallery": {
            "title": "T",
            "category": "modern",
            "description_fr": "D.",
            "tags": ["t"],
            "photo_shape": "none",
        },
        "header": {"lines": [{"line": "L1", "field": "full_name"}], "photo": None},
        "bands": [
            {
                "columns": [
                    {
                        "sections": [
                            {
                                "ref": "S1",
                                "kind": "experiences",
                                "title_lines": ["L2"],
                                "items": [
                                    {
                                        "lines": [
                                            {"line": "L3", "role": "item_title"},
                                            {"line": "L4", "role": "bullet"},
                                        ]
                                    }
                                ],
                                "side_roles": [],
                            }
                        ]
                    }
                ]
            }
        ],
        "decorations": [],
    }

    def assemble(decorations: list[Json]) -> Json:
        page = PdfPage(210.0, 297.0, 1, [dict(ln) for ln in lines], [dict(rule)], [])
        plan["decorations"] = decorations
        spec, _, _ = SpecAssembler(
            FakeDoc(), page, LayoutPlan.model_validate(plan), cv_font_set(), SERVED
        ).build()
        return sections(spec)["experiences"]

    by_rule = assemble([])
    (deco,) = by_rule["decorations"]
    assert (deco["anchor"], deco.get("stretch")) == ("item", True)
    by_plan = assemble(
        [{"shape": "D1", "anchor": "section", "band": None, "section": "S1", "item": None}]
    )
    (deco,) = by_plan["decorations"]
    assert deco["anchor"] == "section"
    assert "connector" not in deco
    assert deco["stretch"] is True


def test_le_bord_droit_dune_colonne_compte_le_rendu_de_sa_ligne_la_plus_longue(
    built: tuple,
) -> None:
    spec, _, _, assembler = built
    (col,) = spec["bands"][2]["columns"]
    secs = assembler.sections_from_plan()[2][0]
    longest = max(ln.right for s in secs for ln in s["all_lines"])
    assert col["x_mm"] + col["w_mm"] >= longest + 0.3
    # Un style absent : la ligne compte pour sa largeur mesurée.
    assert assembler.column_right(secs, col["sections"], {}) == pytest.approx(longest + 0.3)


def test_un_role_pose_par_un_emplacement_compose_ne_se_repete_pas(
    spec: Json, sample: Json, fonts: FontSet
) -> None:
    exp = sections(spec)["experiences"]
    exp["slots"].append(
        {
            **exp["slots"][0],
            "key": "item_title+item_dates",
            "roles": ["item_title", "item_dates"],
            "gap_mm": 3.0,
            "separator": None,
        }
    )
    exp["slot_order"] = ["item_title+item_dates", *exp["slot_order"]]
    view = from_sample(spec, sample)
    item = next(s for s in view["sections"] if s["kind"] == "experiences")["items"][0]
    item["item_dates"] = "2021"
    html = HtmlRenderer(spec, view, fonts, photo=None).render()
    assert html.count(">Responsable<") == 1
    docx = DocxRenderer(spec, view, fonts, photo=None).render({})
    assert docx.startswith(b"PK")


def test_un_champ_repete_dans_un_autre_style_fait_deux_elements(built: tuple) -> None:
    """Un rappel du poste en petit en haut de page et le poste en grand sous le nom."""
    from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415
    from karria.modules.documents.infrastructure.spec_assembler import style_runs  # noqa: PLC0415
    from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

    def line(lid: str, text: str, y: float, **kw: Any) -> Line:
        return Line(
            raw_line(lid, text, 20, y, **kw), {"role": "headline", "section": "header", "item": 0}
        )

    small = line("L1", "Graphiste", 5, size=10.0)
    big_1 = line("L2", "GRAPHISTE", 30, size=16.0)
    big_2 = line("L3", "SENIOR", 36, size=16.0)
    assert [[ln.id for ln in run] for run in style_runs([small, big_1, big_2])] == [
        ["L1"],
        ["L2", "L3"],
    ]
    assembler = built[3]
    styles: Json = {}
    sample: Json = {"identity": {}, "contact": {}}
    first = assembler.header_element("headline", [small], styles, sample)
    second = assembler.header_element("headline", [big_1, big_2], styles, sample)
    assert (first["style"], second["style"]) == ("header.headline", "header.headline.2")
    assert "sample_lines" in second
    assert "sample_lines" not in first


def test_un_nom_en_deux_polices_sur_une_ligne_fait_prenom_et_nom() -> None:
    from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415
    from karria.modules.documents.infrastructure.spec_assembler import split_mixed_name  # noqa: PLC0415
    from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

    chars = [(c, 10.0 + i * 2, 12.0 + i * 2) for i, c in enumerate("CéliaNAUDIN")]
    spans = [
        {"text": c, "font": "Muli-Regular" if i < 5 else "Muli-ExtraBold", "size": 48.0}
        for i, c in enumerate("CéliaNAUDIN")
    ]
    raw = raw_line(
        "L1",
        "Célia NAUDIN",
        10.0,
        10.0,
        w=24.0,
        font="Muli-ExtraBold",
        size=48.0,
        spans=spans,
        chars=chars,
    )
    line = Line(raw, {"role": "full_name", "section": "header", "item": 0})
    halves = split_mixed_name(line)
    assert halves is not None
    first, last = halves
    assert (first.text, first.raw["font"], first.x, first.w) == (
        "Célia",
        "Muli-Regular",
        10.0,
        10.0,
    )
    assert (last.text, last.raw["font"], last.x) == ("NAUDIN", "Muli-ExtraBold", 20.0)
    assert last.raw["caps"] is True
    # Une seule police, ou une coupe qui n'est pas entre deux mots : rien ne change.
    assert (
        split_mixed_name(
            Line(
                {**raw, "spans": None},
                line.__dict__ and {"role": "full_name", "section": "header", "item": 0},
            )
        )
        is None
    )
    odd = [
        {**sp, "font": "Muli-Regular" if i < 3 else "Muli-ExtraBold"} for i, sp in enumerate(spans)
    ]
    assert (
        split_mixed_name(
            Line({**raw, "spans": odd}, {"role": "full_name", "section": "header", "item": 0})
        )
        is None
    )
    assert (
        split_mixed_name(
            Line(
                {**raw, "text": "NAUDIN", "spans": spans[5:], "chars": chars[5:]},
                {"role": "full_name", "section": "header", "item": 0},
            )
        )
        is None
    )


def test_un_paragraphe_justifie_dit_le_bord_de_sa_boite(built: tuple) -> None:
    from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415
    from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

    assembler = built[3]
    # Chaque ligne se mesure à son propre corps (9 pt, celui de `raw_line`).
    styles = {
        "summary.summary": {
            **assembler.style_of([assembler.lines[0]]),
            "letter_spacing_pt": 0.0,
            "size_pt": 9.0,
        }
    }

    def line(lid: str, text: str, y: float, w: float) -> Line:
        return Line(
            raw_line(lid, text, 20.0, y, w=w), {"role": "summary", "section": "summary", "item": 0}
        )

    long = "aaaa bbbb cccc dddd eeee ffff gggg"
    lines = [line("L1", long, 10, 60.0), line("L2", long, 15, 60.0), line("L3", "hhhh", 20, 8.0)]
    section = {"kind": "summary", "elements": [{"role": "summary", "lines": lines}]}
    width = assembler.wrapped_box_width(section, {}, styles, 20.0)
    natural = assembler.fonts.width_mm(long, styles["summary.summary"])
    with_next = assembler.fonts.width_mm(long + " hhhh", styles["summary.summary"])
    assert width is not None
    # Justifié : le bord mesuré (60 mm), borné par la ligne la plus large et le mot suivant.
    assert width >= natural
    assert width <= max(60.0, natural) + 0.01
    assert width <= max(with_next, natural)
    # Une ligne seule compte pour la largeur minimale ; sans repli, pas de boîte.
    single = {"kind": "summary", "elements": [{"role": "summary", "lines": [lines[0]]}]}
    assert assembler.wrapped_box_width(single, {}, styles, 20.0) is None
    wide = line("L9", long + " " + long, 30, 120.0)
    both = {
        "kind": "summary",
        "elements": [{"role": "summary", "lines": [wide]}, {"role": "summary", "lines": lines}],
    }
    width_both = assembler.wrapped_box_width(both, {}, styles, 20.0)
    assert width_both is not None
    assert width_both >= assembler.fonts.width_mm(wide.text, styles["summary.summary"]) - 0.01


def _mini_plan(sections: list[Json], header_lines: list[Json]) -> Json:
    return {
        "gallery": {
            "title": "T",
            "category": "modern",
            "description_fr": "D.",
            "tags": ["t"],
            "photo_shape": "none",
        },
        "header": {"lines": header_lines, "photo": None},
        "bands": [{"columns": [{"sections": sections}]}],
        "decorations": [],
    }


def _assemble(lines: list[Json], plan: Json, **kw: Any) -> tuple[Json, Json, Any]:
    from documents.infrastructure.test_spec_builder import FakeDoc  # noqa: PLC0415
    from karria.modules.documents.infrastructure.pdf_reader import PdfPage  # noqa: PLC0415

    page = PdfPage(210.0, 297.0, 1, [dict(ln) for ln in lines], kw.pop("drawings", []), [])
    assembler = SpecAssembler(
        FakeDoc(), page, LayoutPlan.model_validate(plan), cv_font_set(), SERVED, **kw
    )
    spec, sample, _ = assembler.build()
    return spec, sample, assembler


class TestCoordonneesEtPosteViseDunVraiCv:
    """cv9/Sacha : des coordonnées déclarées dans l'en-tête sous une rubrique Contact titrée,
    un poste visé sans place sous le nom."""

    def _lines(self) -> list[Json]:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        return [
            raw_line("L1", "JEAN DUPONT", 80, 10, h=12, size=34),
            raw_line("L2", "Un profil qui suit le nom de près", 80, 24, w=80),
            raw_line("L3", "et continue sur une seconde ligne", 80, 29, w=80),
            raw_line("L4", "COORDONNEES", 10, 60, size=11, caps=True),
            raw_line("L5", "0612", 20, 68, w=12),
            raw_line("L6", "a@b.fr", 20, 74, w=14),
            raw_line("L7", "LANGUES", 10, 90, size=11, caps=True),
            raw_line("L8", "Anglais", 10, 97),
        ]

    def _plan(self, *, contact_in_header: bool) -> Json:
        contact_items = (
            []
            if contact_in_header
            else [{"lines": [{"line": "L5", "role": "phone"}, {"line": "L6", "role": "email"}]}]
        )
        header = [{"line": "L1", "field": "full_name"}]
        if contact_in_header:
            header += [{"line": "L5", "field": "phone"}, {"line": "L6", "field": "email"}]
        plan = _mini_plan(
            [
                {
                    "ref": "S2",
                    "kind": "contact",
                    "title_lines": ["L4"],
                    "items": contact_items,
                    "side_roles": [],
                },
                {
                    "ref": "S3",
                    "kind": "languages",
                    "title_lines": ["L7"],
                    "items": [{"lines": [{"line": "L8", "role": "language"}]}],
                    "side_roles": [],
                },
            ],
            header,
        )
        plan["bands"] = [
            {
                "columns": [
                    {
                        "sections": [
                            {
                                "ref": "S1",
                                "kind": "summary",
                                "title_lines": [],
                                "items": [
                                    {
                                        "lines": [
                                            {"line": "L2", "role": "summary"},
                                            {"line": "L3", "role": "summary"},
                                        ]
                                    }
                                ],
                                "side_roles": [],
                            }
                        ]
                    }
                ]
            },
            *plan["bands"],
        ]
        return plan

    def test_les_coordonnees_de_lentete_retournent_dans_la_rubrique_contact_titree(self) -> None:
        spec, sample, assembler = _assemble(self._lines(), self._plan(contact_in_header=True))
        contact = sections(spec)["contact"]
        assert [sl["roles"] for sl in contact["slots"]] == [["phone"], ["email"]]
        assert [
            e["field"] for e in spec["header"]["elements"] if e["field"] in ("phone", "email")
        ] == []
        assert sample["contact"] == {"phone": "0612", "email": "a@b.fr"}
        # Le plan gardé dans les étiquettes porte le déplacement.
        plan = assembler.labels["plan"]
        contact_plan = next(
            s
            for b in plan["bands"]
            for c in b["columns"]
            for s in c["sections"]
            if s["kind"] == "contact"
        )
        assert [pl["line"] for it in contact_plan["items"] for pl in it["lines"]] == ["L5", "L6"]

    def test_une_rubrique_contact_deja_garnie_laisse_lentete_tranquille(self) -> None:
        spec, _, _ = _assemble(self._lines(), self._plan(contact_in_header=False))
        assert [sl["roles"] for sl in sections(spec)["contact"]["slots"]] == [["phone"], ["email"]]

    def test_une_coordonnee_au_dessus_du_titre_contact_reste_dans_lentete(self) -> None:
        lines = self._lines()
        lines[4]["y"] = 40.0  # le téléphone au-dessus de « COORDONNEES »
        plan = self._plan(contact_in_header=True)
        spec, _, _ = _assemble(lines, plan)
        fields = [e["field"] for e in spec["header"]["elements"]]
        assert "phone" in fields
        assert [sl["roles"] for sl in sections(spec)["contact"]["slots"]] == [["email"]]
        # Les deux au-dessus : rien ne bouge, la rubrique vide disparaît.
        lines[5]["y"] = 45.0
        spec, _, _ = _assemble(lines, self._plan(contact_in_header=True))
        assert {"phone", "email"} <= {e["field"] for e in spec["header"]["elements"]}
        assert sections(spec)["contact"]["slots"] == []

    def test_sans_place_sous_le_nom_le_poste_vise_ouvre_le_flux(self) -> None:
        spec, _, _ = _assemble(self._lines(), self._plan(contact_in_header=False))
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        assert headline["flow"] is True
        assert headline["y_mm"] == spec["first_band_top_mm"]
        assert headline["gap_mm"] >= 2.0

    def test_avec_de_la_place_le_poste_vise_reste_en_absolu(self, spec: Json) -> None:
        headline = next(e for e in spec["header"]["elements"] if e["field"] == "headline")
        assert "flow" not in headline


class TestBoitesEtColonnes:
    def test_un_paragraphe_au_fer_a_droite_a_sa_boite_au_bord_mesure(self) -> None:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        fonts = cv_font_set()
        st = {
            "family": "Testa",
            "weight": 400,
            "italic": False,
            "size_pt": 9.0,
            "letter_spacing_pt": 0.0,
            "uppercase": False,
        }

        def flush_right(lid: str, text: str, y: float) -> Json:
            w = fonts.width_mm(text, st)
            return raw_line(lid, text, 100.0 - w, y, w=w)

        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20),
            flush_right("L2", "aaaa bbbb cccc", 30),
            flush_right("L3", "dddd eeee", 35),
            flush_right("L4", "ffff", 40),
            raw_line("L5", "COMPETENCES", 20, 60, size=11, caps=True),
            raw_line("L6", "Python", 20, 67),
        ]
        plan = _mini_plan(
            [
                {
                    "ref": "S1",
                    "kind": "summary",
                    "title_lines": [],
                    "items": [
                        {
                            "lines": [
                                {"line": "L2", "role": "summary"},
                                {"line": "L3", "role": "summary"},
                                {"line": "L4", "role": "summary"},
                            ]
                        }
                    ],
                    "side_roles": [],
                },
                {
                    "ref": "S2",
                    "kind": "skills",
                    "title_lines": ["L5"],
                    "items": [{"lines": [{"line": "L6", "role": "skill"}]}],
                    "side_roles": [],
                },
            ],
            [{"line": "L1", "field": "full_name"}],
        )
        spec, _, _ = _assemble(lines, plan)
        summary = sections(spec)["summary"]
        assert summary["slots"][0]["align"] == "right"
        # La colonne commence à la première ligne ; le bord droit du paragraphe est à 100,
        # plus la marge au-dessus de la ligne la plus large.
        col_x = spec["bands"][0]["columns"][0]["x_mm"]
        assert summary["width_mm"] == pytest.approx(100.0 - col_x + 0.3, abs=0.05)

    def test_une_colonne_sarrete_avant_le_bord_de_la_page(self) -> None:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20),
            raw_line("L2", "COMPETENCES", 20, 60, size=11, caps=True),
            raw_line("L3", "Un long texte qui touche le bord", 20, 67, w=189.5),
        ]
        plan = _mini_plan(
            [
                {
                    "ref": "S1",
                    "kind": "skills",
                    "title_lines": ["L2"],
                    "items": [{"lines": [{"line": "L3", "role": "skill"}]}],
                    "side_roles": [],
                }
            ],
            [{"line": "L1", "field": "full_name"}],
        )
        spec, _, _ = _assemble(lines, plan)
        column = spec["bands"][0]["columns"][0]
        assert column["x_mm"] + column["w_mm"] <= 210.0 - 1.0 + 0.01

    def test_la_boite_garde_une_marge_au_dessus_de_la_ligne_la_plus_large(
        self, built: tuple
    ) -> None:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415
        from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

        assembler = built[3]
        styles = {
            "educations.item_title": {
                **assembler.style_of([assembler.lines[0]]),
                "letter_spacing_pt": 0.0,
                "size_pt": 9.0,
            }
        }
        st = styles["educations.item_title"]
        first = Line(
            raw_line("L1", "Licence en Gestion de Projet", 10.0, 10, w=57.5),
            {"role": "item_title", "section": "educations", "item": 1},
        )
        second = Line(
            raw_line("L2", "Universite Evenementielle", 10.0, 15, w=52.0),
            {"role": "item_title", "section": "educations", "item": 1},
        )
        assembler._col_right = 67.5
        section = {
            "kind": "educations",
            "elements": [{"role": "item_title", "lines": [first, second]}],
        }
        width = assembler.wrapped_box_width(section, {}, styles, 10.0)
        widest = max(assembler.fonts.width_mm(ln.text, st) for ln in (first, second))
        assert width is not None
        assert width >= widest + 0.3 - 0.01


def test_des_etiquettes_seules_dans_leur_rubrique_sont_du_texte() -> None:
    from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

    lines = [
        raw_line("L1", "JEAN", 20, 10, h=8, size=20),
        raw_line("L2", "COMPETENCES", 20, 50, size=11, caps=True),
        raw_line("L3", "Python", 20, 57),
        raw_line("L4", "Mon portfolio", 120, 250, w=30),
    ]
    plan = _mini_plan(
        [
            {
                "ref": "S1",
                "kind": "skills",
                "title_lines": ["L2"],
                "items": [{"lines": [{"line": "L3", "role": "skill"}]}],
                "side_roles": [],
            },
            {
                "ref": "S2",
                "kind": "other",
                "title_lines": [],
                "items": [{"lines": [{"line": "L4", "role": "label"}]}],
                "side_roles": [],
            },
        ],
        [{"line": "L1", "field": "full_name"}],
    )
    spec, sample, _ = _assemble(lines, plan)
    other = sections(spec)["other:section-2"]
    assert [sl["roles"] for sl in other["slots"]] == [["item_text"]]
    assert sample["others"]["other:section-2"]["items"][0]["description"] == "Mon portfolio"


class TestPiedDePage:
    def _lines(self) -> list[Json]:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        return [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20),
            raw_line("L2", "COMPETENCES", 20, 50, size=11, caps=True),
            raw_line("L3", "Python", 20, 57),
            raw_line("L4", "0612", 20, 270, w=12),
            raw_line("L5", "a@b.fr", 120, 270, w=14),
        ]

    def _plan(self, *, titled: bool = False, low: bool = True, alone: bool = True) -> Json:
        plan = _mini_plan(
            [
                {
                    "ref": "S1",
                    "kind": "skills",
                    "title_lines": ["L2"],
                    "items": [{"lines": [{"line": "L3", "role": "skill"}]}],
                    "side_roles": [],
                }
            ],
            [{"line": "L1", "field": "full_name"}],
        )
        contact = {
            "ref": "S2",
            "kind": "contact",
            "title_lines": ["L2"] if titled else [],
            "items": [
                {"lines": [{"line": "L4", "role": "phone"}, {"line": "L5", "role": "email"}]}
            ],
            "side_roles": [],
        }
        if alone:
            plan["bands"].append({"columns": [{"sections": [contact]}]})
        else:
            plan["bands"][0]["columns"][0]["sections"].append(contact)
        return plan

    def test_des_coordonnees_en_pied_de_page_sont_des_champs_dentete(self) -> None:
        spec, sample, assembler = _assemble(self._lines(), self._plan())
        fields = {e["field"]: e for e in spec["header"]["elements"]}
        assert fields["phone"]["y_mm"] == 270
        assert fields["email"]["x_mm"] == 120
        assert "contact" not in sections(spec)
        assert len(spec["bands"]) == 1
        assert sample["contact"] == {"phone": "0612", "email": "a@b.fr"}
        plan = assembler.labels["plan"]
        assert len(plan["bands"]) == 1
        assert [h["field"] for h in plan["header"]["lines"]] == ["full_name", "phone", "email"]

    def test_des_coordonnees_a_la_suite_dune_rubrique_sont_aussi_un_pied_de_page(self) -> None:
        spec, _, assembler = _assemble(self._lines(), self._plan(alone=False))
        assert list(sections(spec)) == ["skills"]
        assert {e["field"] for e in spec["header"]["elements"]} >= {"phone", "email"}
        assert len(assembler.labels["plan"]["bands"]) == 1

    def test_une_rubrique_contact_haute_titree_ou_suivie_reste_une_bande(self) -> None:
        lines = self._lines()
        lines[3]["y"], lines[4]["y"] = 80.0, 80.0
        spec, _, _ = _assemble(lines, self._plan())
        assert "contact" in sections(spec)
        spec, _, _ = _assemble(self._lines(), self._plan(titled=True))
        assert "contact" in sections(spec)
        plan = self._plan(alone=False)
        plan["bands"][0]["columns"][0]["sections"].reverse()
        spec, _, _ = _assemble(self._lines(), plan)
        assert "contact" in sections(spec)
        plan = self._plan()
        plan["bands"][-1]["columns"][0]["sections"][0]["items"][0]["lines"][0]["role"] = "item_text"
        spec, _, _ = _assemble(self._lines(), plan)
        assert "contact" in sections(spec)
        plan = self._plan()
        plan["bands"][-1]["columns"][0]["sections"][0]["items"] = []
        plan["bands"][-1]["columns"][0]["sections"][0]["title_lines"] = []
        spec, _, _ = _assemble(self._lines(), plan)
        assert len(spec["bands"]) == 1


def test_un_plan_sans_bande_na_pas_de_pied_de_page() -> None:
    from karria.modules.documents.infrastructure.pdf_reader import PdfPage  # noqa: PLC0415
    from karria.modules.documents.infrastructure.spec_assembler import (  # noqa: PLC0415
        relocate_footer_contact,
    )

    plan = LayoutPlan.model_validate(_mini_plan([], []))
    plan.bands = []
    page = PdfPage(210.0, 297.0, 1, [], [], [])
    assert relocate_footer_contact(plan, page) is plan


def test_des_coordonnees_a_cote_du_titre_contact_restent_dans_lentete() -> None:
    """« CONTACT » suivi du téléphone et du courriel sur la même ligne (cv10) : une bande
    d'en-tête, pas une rubrique ; le titre vide disparaît avec sa rubrique."""
    from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

    lines = [
        raw_line("L1", "JEAN", 80, 10, h=8, size=20),
        raw_line("L2", "CONTACT", 80, 55, size=11, caps=True),
        raw_line("L3", "0612", 110, 55.4, w=12),
        raw_line("L4", "a@b.fr", 140, 55.4, w=14),
        raw_line("L5", "PROFIL", 20, 75, size=11, caps=True),
        raw_line("L6", "Un profil.", 20, 82, w=40),
    ]
    plan = _mini_plan(
        [
            {"ref": "S0", "kind": "contact", "title_lines": ["L2"], "items": [], "side_roles": []},
            {
                "ref": "S1",
                "kind": "summary",
                "title_lines": ["L5"],
                "items": [{"lines": [{"line": "L6", "role": "summary"}]}],
                "side_roles": [],
            },
        ],
        [
            {"line": "L1", "field": "full_name"},
            {"line": "L3", "field": "phone"},
            {"line": "L4", "field": "email"},
        ],
    )
    spec, _, _ = _assemble(lines, plan)
    assert {"phone", "email"} <= {e["field"] for e in spec["header"]["elements"]}
    assert sections(spec)["contact"]["slots"] == []


def test_le_bas_du_contenu_ignore_un_pied_de_page(built: tuple) -> None:
    from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

    lines = [
        raw_line("L1", "JEAN", 20, 10, h=8, size=20),
        raw_line("L2", "COMPETENCES", 20, 50, size=11, caps=True),
        raw_line("L3", "Python", 20, 57),
        raw_line("L4", "0612", 20, 280, w=12),
    ]
    plan = _mini_plan(
        [
            {
                "ref": "S1",
                "kind": "skills",
                "title_lines": ["L2"],
                "items": [{"lines": [{"line": "L3", "role": "skill"}]}],
                "side_roles": [],
            }
        ],
        [{"line": "L1", "field": "full_name"}, {"line": "L4", "field": "phone"}],
    )
    spec, _, _ = _assemble(lines, plan)
    assert spec["content_bottom_mm"] < 100.0
    assert built[0]["content_bottom_mm"] > 0


# ---------------------------------------------------------------------------
# Un modèle à intitulés en deux polices, puces réparties, site en pied de page
# ---------------------------------------------------------------------------


def _glyphs(text: str, x: float, step: float = 2.0) -> list[tuple[str, float, float]]:
    """Les glyphes visibles d'un texte, posés à un pas régulier."""
    return [(c, x + i * step, x + (i + 1) * step) for i, c in enumerate(text.replace(" ", ""))]


def _two_font_title(lid: str, y: float) -> Json:
    from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

    text = "Vendeur - Zara"
    spans = [
        {
            "text": c,
            "font": BOLD if i < 7 else REGULAR,
            "size": 9.0,
            "color": "#000000" if i < 7 else "#6E6E6E",
        }
        for i, c in enumerate(text.replace(" ", ""))
    ]
    return raw_line(lid, text, 22, y, w=24, font=BOLD, spans=spans, chars=_glyphs(text, 22.0))


class TestIntituleEnDeuxPolices:
    def test_la_seconde_moitie_devient_le_sous_titre_sans_son_tiret(self) -> None:
        from karria.modules.documents.infrastructure.spec_assembler import split_mixed_title  # noqa: PLC0415
        from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

        line = Line(
            _two_font_title("L3", 57), {"role": "item_title", "section": "experiences", "item": 1}
        )
        halves = split_mixed_title(line)
        assert halves is not None
        title, subtitle = halves
        assert (title.text, title.role, title.raw["font"], title.x, title.w) == (
            "Vendeur",
            "item_title",
            BOLD,
            22.0,
            14.0,
        )
        assert (subtitle.text, subtitle.role, subtitle.raw["font"], subtitle.raw["color"]) == (
            "Zara",
            "item_subtitle",
            REGULAR,
            "#6E6E6E",
        )
        # « Zara » commence après le tiret : au huitième glyphe.
        assert (subtitle.x, subtitle.w, subtitle.raw["lead"]) == (38.0, 8.0, "-")

    def test_une_ligne_dune_police_ou_coupee_dans_un_mot_ne_change_pas(self) -> None:
        from karria.modules.documents.infrastructure.spec_assembler import split_mixed_title  # noqa: PLC0415
        from karria.modules.documents.infrastructure.spec_builder import Line  # noqa: PLC0415

        label = {"role": "item_title", "section": "experiences", "item": 1}
        raw = _two_font_title("L3", 57)
        assert split_mixed_title(Line({**raw, "spans": None}, label)) is None
        inside = [{**sp, "font": BOLD if i < 3 else REGULAR} for i, sp in enumerate(raw["spans"])]
        assert split_mixed_title(Line({**raw, "spans": inside}, label)) is None
        three = [
            {**sp, "font": "Autre" if i > 9 else sp["font"]} for i, sp in enumerate(raw["spans"])
        ]
        assert split_mixed_title(Line({**raw, "spans": three}, label)) is None
        # Rien après le signe : pas de sous-titre.
        alone = {**raw, "text": "Vendeur -", "spans": raw["spans"][:8], "chars": raw["chars"][:8]}
        assert split_mixed_title(Line(alone, label)) is None

    def _spec(self) -> tuple[Json, Json]:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        lines = [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20),
            raw_line("L2", "EXPERIENCE", 20, 50, size=11, caps=True),
            _two_font_title("L3", 57),
            raw_line("L4", "2020 - 2022", 170, 57, w=20),
            _two_font_title("L5", 70),
            raw_line("L6", "2019", 182, 70, w=8),
        ]
        items = [
            {"lines": [{"line": a, "role": "item_title"}, {"line": b, "role": "item_dates"}]}
            for a, b in (("L3", "L4"), ("L5", "L6"))
        ]
        plan = _mini_plan(
            [
                {
                    "ref": "S1",
                    "kind": "experiences",
                    "title_lines": ["L2"],
                    "items": items,
                    "side_roles": [],
                }
            ],
            [{"line": "L1", "field": "full_name"}],
        )
        spec, sample, _ = _assemble(lines, plan)
        return spec, sample

    def test_la_ligne_porte_trois_champs_les_dates_au_fer_a_droite(self) -> None:
        spec, sample = self._spec()
        exp = sections(spec)["experiences"]
        assert exp["slot_order"] == ["item_title+item_subtitle+item_dates"]
        row = exp["slots"][0]
        assert row["joiners"] == {"item_subtitle": "-"}
        assert row["flush_right"] is True
        assert spec["styles"]["experiences.item_subtitle"]["color"] == "#6E6E6E"
        assert spec["styles"]["experiences.item_subtitle"]["weight"] == 400
        assert sample["experiences"][0]["title_raw"] == "Vendeur"
        assert sample["experiences"][0]["subtitle_raw"] == "Zara"

    def test_le_rendu_reecrit_le_signe_entre_le_poste_et_lentreprise(self) -> None:
        spec, sample = self._spec()
        html = HtmlRenderer(spec, from_sample(spec, sample), cv_font_set(), photo=None).render()
        joiner = '<span class="s-experiences-item-subtitle" style="white-space:pre"> - </span>'
        assert html.count(joiner) == 2
        assert html.count('<span style="flex:1 1 auto;min-width:3mm"></span>') == 2
        cv = {
            "identity": {"first_name": "Awa", "last_name": "Diallo"},
            "experiences": [
                {"position": "Caissière", "company": "Fnac", "start_date": "2021"},
                {"position": "Hôtesse", "company": "Ikea"},
            ],
        }
        view = from_cv(spec, cv)
        person = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert "Caissière</span>" in person
        assert person.count(joiner) == 2
        # Sans dates, l'entreprise suit le poste : elle ne part pas à la marge.
        assert person.count('<span style="flex:1 1 auto;min-width:3mm"></span>') == 1
        assert DocxRenderer(spec, view, cv_font_set(), photo=None).render({}).startswith(b"PK")


class TestRubriqueIntercalee:
    def _lines(self) -> list[Json]:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        return [
            raw_line("L1", "JEAN", 20, 10, h=8, size=20),
            raw_line("L2", "EXPERIENCE", 20, 50, size=11, caps=True),
            raw_line("L3", "Vendeur", 20, 57),
            raw_line("L4", "Conseiller", 20, 62, w=22),
            raw_line("L5", "Encaisser", 20, 67, w=20),
            raw_line("L6", "Caissier", 20, 77),
            raw_line("L7", "Rendre la monnaie", 20, 82, w=30),
            raw_line("L8", "Ranger", 20, 87, w=12),
        ]

    def _plan(self, stray: list[str]) -> Json:
        return _mini_plan(
            [
                {
                    "ref": "S1",
                    "kind": "experiences",
                    "title_lines": ["L2"],
                    "items": [
                        {
                            "lines": [
                                {"line": "L3", "role": "item_title"},
                                {"line": "L4", "role": "bullet"},
                            ]
                        },
                        {
                            "lines": [
                                {"line": "L6", "role": "item_title"},
                                {"line": "L7", "role": "bullet"},
                            ]
                        },
                    ],
                    "side_roles": [],
                },
                {
                    "ref": "S2",
                    "kind": "other",
                    "title_lines": [],
                    "items": [{"lines": [{"line": ln, "role": "bullet"}]} for ln in stray],
                    "side_roles": [],
                },
            ],
            [{"line": "L1", "field": "full_name"}],
        )

    def test_les_lignes_rejoignent_lelement_quelles_suivent(self) -> None:
        spec, sample, assembler = _assemble(self._lines(), self._plan(["L5", "L8"]))
        assert list(sections(spec)) == ["experiences"]
        assert [e["bullets"] for e in sample["experiences"]] == [
            ["Conseiller Encaisser"],
            ["Rendre la monnaie Ranger"],
        ]
        assert assembler.check.unplaced == []
        plan = assembler.labels["plan"]
        assert len(plan["bands"][0]["columns"][0]["sections"]) == 1

    def test_une_rubrique_sans_titre_qui_suit_les_autres_reste_une_rubrique(self) -> None:
        lines = self._lines()
        lines[4]["y"], lines[7]["y"] = 120.0, 125.0
        spec, _, _ = _assemble(lines, self._plan(["L5", "L8"]))
        assert len(sections(spec)) == 2

    def test_une_ligne_trop_loin_de_tout_element_interdit_le_rapprochement(self) -> None:
        lines = self._lines()
        lines[7]["y"] = 110.0
        spec, _, _ = _assemble(lines, self._plan(["L5", "L8"]))
        assert len(sections(spec)) == 2


class TestSiteEnPiedDePageEtFiletDansLeNom:
    def _assemble(self, drawings: list[Json] | None = None) -> tuple[Json, Json]:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        lines = [
            raw_line(
                "L1",
                "JEAN DUPONT CV",
                60,
                10,
                w=83,
                h=8,
                size=20,
                # Dix glyphes serrés, un blanc de 10 mm, puis « CV ».
                chars=[*_glyphs("JEANDUPONT", 60.0, 6.0), *_glyphs("CV", 130.0, 6.5)],
            ),
            raw_line("L2", "jean.fr", 170, 30, w=25, size=8),
            raw_line("L3", "COMPETENCES", 20, 50, size=11, caps=True),
            raw_line("L4", "Python", 20, 57),
            raw_line("L5", "jean.fr · github.com/jean", 70, 292, w=70, color="#0096FF"),
        ]
        plan = _mini_plan(
            [
                {
                    "ref": "S1",
                    "kind": "skills",
                    "title_lines": ["L3"],
                    "items": [{"lines": [{"line": "L4", "role": "skill"}]}],
                    "side_roles": [],
                },
                {
                    "ref": "S2",
                    "kind": "other",
                    "title_lines": [],
                    "items": [{"lines": [{"line": "L5", "role": "website"}]}],
                    "side_roles": [],
                },
            ],
            [{"line": "L1", "field": "full_name"}, {"line": "L2", "field": "website"}],
        )
        spec, sample, _ = _assemble(lines, plan, drawings=drawings or [])
        return spec, sample

    def test_le_site_repete_en_pied_de_page_est_un_second_champ_dentete(self) -> None:
        spec, sample = self._assemble()
        assert list(sections(spec)) == ["skills"]
        sites = [e for e in spec["header"]["elements"] if e["field"] == "website"]
        assert [e["style"] for e in sites] == ["header.website", "header.website.2"]
        # L'exemple garde le texte de chaque écriture ; la première fait la valeur.
        assert sites[1]["sample_lines"][0]["text"] == "jean.fr · github.com/jean"
        assert sample["contact"]["website"] == "jean.fr"
        # Le pied de page Karria occupe les 6 derniers millimètres : la ligne remonte.
        box = spec["styles"]["header.website.2"]["box_h_mm"]
        assert sites[1]["y_mm"] == pytest.approx(297.0 - 6.0 - box, abs=0.01)
        assert sites[1]["sample_lines"][0]["y_mm"] == sites[1]["y_mm"]
        assert spec["content_bottom_mm"] < 100

    def test_un_filet_entre_deux_mots_du_nom_ne_vaut_que_pour_lexemple(self) -> None:
        from documents.infrastructure.test_spec_builder import drawing  # noqa: PLC0415

        # « JEAN DUPONT | CV » : le filet est dans le blanc entre « DUPONT » (120 mm) et « CV » (130 mm).
        spec, sample = self._assemble([drawing("rect", 124.5, 9, 1.0, 9, fill="#0096FF")])
        name = next(e for e in spec["header"]["elements"] if e["field"] == "full_name")
        assert [ln["text"] for ln in name["sample_lines"]] == ["JEAN DUPONT", "CV"]
        assert name["sample_lines"][1]["x_mm"] == 130.0
        assert spec["header"]["decorations"][0]["sample_only"] is True
        view = from_cv(spec, {"identity": {"first_name": "Awa", "last_name": "Diallo"}})
        person = HtmlRenderer(spec, view, cv_font_set(), photo=None).render()
        assert "#0096FF;border-radius" not in person
        example = HtmlRenderer(spec, from_sample(spec, sample), cv_font_set(), photo=None).render()
        assert "background:#0096FF" in example
        docx = DocxRenderer(spec, view, cv_font_set(), photo=None).render({})
        assert docx.startswith(b"PK")

    def test_un_filet_hors_du_nom_reste_un_ornement(self) -> None:
        from documents.infrastructure.test_spec_builder import drawing  # noqa: PLC0415

        spec, _ = self._assemble([drawing("rect", 40.0, 9, 1.0, 9, fill="#0096FF")])
        name = next(e for e in spec["header"]["elements"] if e["field"] == "full_name")
        assert "sample_lines" not in name
        assert "sample_only" not in spec["header"]["decorations"][0]


class TestBandePropreALExemple:
    """Une rubrique libre à côté des coordonnées de l'en-tête : rien pour une personne."""

    def _spec(self) -> Json:
        from documents.infrastructure.test_spec_builder import raw_line  # noqa: PLC0415

        lines = [
            raw_line("L1", "JEAN DUPONT", 60, 10, w=90, h=8, size=20),
            raw_line("L2", "Statut : salarié", 20, 30, w=40),
            raw_line("L3", "0612", 170, 30, w=20, size=8),
            raw_line("L4", "COMPETENCES", 20, 50, size=11, caps=True),
            raw_line("L5", "Python", 20, 57),
        ]
        plan = _mini_plan(
            [
                {
                    "ref": "S1",
                    "kind": "other",
                    "title_lines": [],
                    "items": [{"lines": [{"line": "L2", "role": "item_text"}]}],
                    "side_roles": [],
                }
            ],
            [{"line": "L1", "field": "full_name"}, {"line": "L3", "field": "phone"}],
        )
        plan["bands"].append(
            {
                "columns": [
                    {
                        "sections": [
                            {
                                "ref": "S2",
                                "kind": "skills",
                                "title_lines": ["L4"],
                                "items": [{"lines": [{"line": "L5", "role": "skill"}]}],
                                "side_roles": [],
                            }
                        ]
                    }
                ]
            }
        )
        spec, _, _ = _assemble(lines, plan)
        return spec

    CV: Json = {  # noqa: RUF012 - une donnée de test, jamais modifiée
        "identity": {"first_name": "Awa", "last_name": "Diallo"},
        "contact": {"phone": "06 12 34 56 78"},
        "skills": [{"name": "Vente"}],
    }

    def test_la_bande_suivante_garde_sa_place_sous_les_coordonnees(self) -> None:
        spec = self._spec()
        renderer = HtmlRenderer(spec, from_cv(spec, self.CV), cv_font_set(), photo=None)
        # La bande des compétences commençait à 50 mm, la première à 30 mm.
        assert renderer.held_band_top(1) == "20.00mm"
        assert renderer.held_band_top(0) == ""
        assert "margin-top:20.00mm" in renderer.render()

    def test_sans_coordonnee_a_cette_hauteur_elle_remonte(self) -> None:
        spec = self._spec()
        cv = {**self.CV, "contact": {}}
        renderer = HtmlRenderer(spec, from_cv(spec, cv), cv_font_set(), photo=None)
        assert renderer.held_band_top(1) == ""
        assert "margin-top:20.00mm" not in renderer.render()
