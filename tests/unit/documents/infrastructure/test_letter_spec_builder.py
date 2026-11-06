"""Le PDF d'une lettre appariée → sa spécification de lettre et son contenu d'exemple (ADR-0036)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from documents.infrastructure.fakes import (
    SERVED,
    cv_font_set,
    letter_labels_for,
    letter_template_pdf,
)
from karria.modules.documents.infrastructure.letter_spec_builder import (
    LetterSpecBuilder,
    complete_spec,
    contact_field,
    split_subject,
    title_case_name,
)
from karria.modules.documents.infrastructure.pdf_reader import PdfDocument

pytestmark = pytest.mark.unit

Json = dict[str, Any]
#: La couleur du décor du CV apparié : celle de l'intitulé dans la lettre de test.
ACCENT = "#444444"


@pytest.fixture(scope="module")
def built() -> tuple[Json, Json, list[Json]]:
    with PdfDocument(letter_template_pdf()) as doc:
        page = doc.read()
        labels = letter_labels_for(page.lines)
        spec, sample = LetterSpecBuilder(page, labels, cv_font_set(), SERVED, accent=ACCENT).build()
        return spec, sample, page.lines


@pytest.fixture
def spec(built: tuple[Json, Json, list[Json]]) -> Json:
    return copy.deepcopy(built[0])


@pytest.fixture
def sample(built: tuple[Json, Json, list[Json]]) -> Json:
    return copy.deepcopy(built[1])


class TestPetitesRegles:
    def test_une_coordonnee_se_reconnait_a_son_texte(self) -> None:
        assert contact_field("hello@reallygreatsite.com") == "email"
        assert contact_field("+123-456-7890") == "phone"
        assert contact_field("06 12 34 56 78") == "phone"
        assert contact_field("123 Anywhere St., Any City") == "address"

    def test_l_objet_perd_son_prefixe_tel_que_le_modele_l_ecrit(self) -> None:
        assert split_subject("Objet : Candidature") == ("Objet : ", "Candidature")
        assert split_subject("OBJET - Stage") == ("OBJET - ", "Stage")
        assert split_subject("Candidature spontanée") == ("", "Candidature spontanée")

    def test_un_nom_en_capitales_prend_la_casse_d_un_nom(self) -> None:
        assert title_case_name("DUBOIS") == "Dubois"
        assert title_case_name("JEAN-MARC") == "Jean-Marc"
        assert title_case_name("McDonald") == "McDonald"


class TestEnTete:
    def test_l_expediteur_est_une_pile_a_gauche_et_le_destinataire_une_pile_a_droite(
        self, spec: Json
    ) -> None:
        blocks = spec["header"]["blocks"]
        assert [b["anchor"] for b in blocks] == ["left", "right"]
        left, right = blocks
        assert [e["field"] for e in left["elements"]] == [
            "full_name",
            "headline",
            "phone",
            "email",
            "address",
        ]
        assert left["x_mm"] == pytest.approx(20.0, abs=0.6)
        assert left["y_mm"] < 25.0
        # Les lignes s'empilent au pas mesuré : 5 mm entre les coordonnées.
        assert left["elements"][2]["pitch_mm"] == pytest.approx(5.0, abs=0.05)
        assert [e["field"] for e in right["elements"]] == ["company", "city", "date_line"]
        assert right["right_mm"] == pytest.approx(20.0, abs=0.6)
        # Le destinataire ne peut pas s'étaler sur l'expéditeur : largeur bornée.
        assert 20.0 < right["max_w_mm"] < 170.0
        assert left["max_w_mm"] < 170.0

    def test_les_styles_viennent_du_pdf(self, spec: Json) -> None:
        styles = spec["styles"]
        assert styles["header.full_name"]["weight"] == 700
        assert styles["header.full_name"]["size_pt"] == 24.0
        assert styles["header.full_name"]["uppercase"] is True
        assert styles["header.headline"]["color"] == "#444444"
        assert styles["recipient.company"]["weight"] == 700
        assert styles["date_line"]["size_pt"] == 11.0
        assert spec["accent"] == ACCENT
        assert styles["header.headline"]["follows_decor"] is True, "même couleur que le décor"
        assert "follows_decor" not in styles["paragraph"]
        assert {(f["family"], f["weight"]) for f in spec["fonts"]} == {
            ("Testa", 400),
            ("Testa", 700),
        }


class TestCorps:
    def test_la_colonne_et_les_blocs_dans_l_ordre_avec_leurs_blancs(self, spec: Json) -> None:
        body = spec["body"]
        assert body["x_mm"] == pytest.approx(20.0, abs=0.6)
        assert body["w_mm"] == pytest.approx(170.0, abs=1.5)
        assert [b["role"] for b in body["blocks"]] == [
            "subject",
            "greeting",
            "paragraph",
            "closing",
            "signature",
        ]
        subject, greeting, paragraph, closing, signature = body["blocks"]
        assert subject["prefix"] == "Objet : "
        assert subject["gap_before_mm"] == 0.0
        assert greeting["gap_before_mm"] > 4.0
        assert paragraph["align"] == "justify"
        assert paragraph["gap_between_mm"] == pytest.approx(
            10.0 - spec["styles"]["paragraph"]["box_h_mm"], abs=0.3
        )
        assert spec["styles"]["paragraph"]["pitch_mm"] == pytest.approx(5.0, abs=0.05)
        assert closing["align"] == "left"
        assert signature["gap_before_mm"] > closing["gap_before_mm"]
        assert spec["content_bottom_mm"] > 160.0

    def test_les_decorations_sont_gardees_en_absolu_et_la_mention_hors_lettre_ignoree(
        self, spec: Json
    ) -> None:
        [rule] = spec["page_decorations"]
        assert rule["kind"] == "hline"
        assert rule["y_mm"] == pytest.approx(55.0, abs=0.3)
        assert rule["stroke"] == "#999999"
        assert spec["source"] == {"producer": "Canva"}
        assert "Mentions" not in str(spec)


class TestContenuDExemple:
    def test_la_lettre_du_modele_au_format_de_la_lettre(self, sample: Json) -> None:
        assert sample["sender"] == {
            "first_name": "Awa",
            "last_name": "Diallo",
            "headline": "Vendeuse",
            "phone": "06 12 34 56 78",
            "email": "awa@mail.fr",
            "city": "Montreuil",
        }
        assert sample["recipient"] == {"company": "Acme", "city": "Lyon"}
        assert sample["date_line"] == "Montreuil, le 5 novembre 2026"
        assert sample["subject"] == "Candidature au poste de vendeuse"
        assert sample["greeting"] == "Madame, Monsieur,"
        assert len(sample["paragraphs"]) == 3
        assert sample["paragraphs"][0]["text"].startswith("venteb conseilb")
        assert sample["closing"].startswith("Je vous prie de croire")
        assert sample["closing"].endswith("distinguees.")
        assert "signature" not in sample


class TestSansEtiquettesUtiles:
    def test_sans_paragraphe_la_lettre_est_refusee(self) -> None:
        with PdfDocument(letter_template_pdf()) as doc:
            page = doc.read()
            labels = letter_labels_for(page.lines)
            labels["lines"] = [lb for lb in labels["lines"] if lb["role"] != "paragraph"]
            with pytest.raises(ValueError, match="paragraphe"):
                LetterSpecBuilder(page, labels, cv_font_set(), SERVED).build()

    def test_sans_numero_les_paragraphes_se_devinent_au_blanc(self) -> None:
        with PdfDocument(letter_template_pdf()) as doc:
            page = doc.read()
            labels = letter_labels_for(page.lines)
            for lb in labels["lines"]:
                lb["paragraph"] = 0
            spec, sample = LetterSpecBuilder(page, labels, cv_font_set(), SERVED).build()
        assert len(sample["paragraphs"]) == 3
        assert "accent" not in spec

    def test_un_nom_sur_deux_lignes_devient_prenom_et_nom(self) -> None:
        with PdfDocument(letter_template_pdf()) as doc:
            page = doc.read()
            labels = letter_labels_for(page.lines)
            # L'intitulé devient la seconde ligne du nom.
            for lb in labels["lines"]:
                if lb["role"] == "sender_headline":
                    lb["role"] = "sender_name"
            spec, sample = LetterSpecBuilder(page, labels, cv_font_set(), SERVED).build()
        fields = [e["field"] for e in spec["header"]["blocks"][0]["elements"]]
        assert fields[:2] == ["first_name", "last_name"]
        assert sample["sender"]["first_name"] == "Awa Diallo"
        assert sample["sender"]["last_name"] == "Vendeuse"
        assert "headline" in fields, "l'intitulé manquant reçoit un emplacement"


class TestCompletion:
    """Ce que le modèle n'a pas reçoit un emplacement dans sa grammaire."""

    def test_une_specification_complete_ne_bouge_pas(self, spec: Json) -> None:
        assert complete_spec(spec) == spec

    def test_les_champs_absents_sont_ajoutes_sans_toucher_l_original(self, spec: Json) -> None:
        bare = copy.deepcopy(spec)
        bare["header"]["blocks"] = [
            {
                "anchor": "left",
                "x_mm": 20.0,
                "y_mm": 20.0,
                "max_w_mm": 100.0,
                "elements": [spec["header"]["blocks"][0]["elements"][0]],
            }
        ]
        bare["body"]["blocks"] = [b for b in spec["body"]["blocks"] if b["role"] == "paragraph"]
        bare["styles"] = {
            k: v for k, v in spec["styles"].items() if k in ("header.full_name", "paragraph")
        }
        done = complete_spec(bare)
        assert bare["body"]["blocks"][0]["role"] == "paragraph", "l'original est intact"
        fields = [e["field"] for b in done["header"]["blocks"] for e in b["elements"]]
        assert fields == [
            "full_name",
            "headline",
            "address",
            "phone",
            "email",
            "company",
            "city",
            "date_line",
        ]
        assert done["header"]["blocks"][1]["anchor"] == "right"
        assert [b["role"] for b in done["body"]["blocks"]] == [
            "subject",
            "greeting",
            "paragraph",
            "closing",
            "signature",
        ]
        assert done["body"]["blocks"][0]["prefix"] == "Objet\u00a0: "
        assert done["body"]["blocks"][0]["gap_before_mm"] == 0.0
        assert done["styles"]["subject"]["weight"] == 700
        assert done["styles"]["header.headline"]["synthesized"] is True
        assert done["styles"]["recipient.city"]["weight"] == 400
        assert {f["weight"] for f in done["fonts"]} == {400, 700}

    def test_sans_aucune_ligne_d_expediteur_une_pile_est_creee_en_haut_a_gauche(
        self, spec: Json
    ) -> None:
        bare = copy.deepcopy(spec)
        bare["header"]["blocks"] = [spec["header"]["blocks"][1]]
        bare["styles"] = {k: v for k, v in spec["styles"].items() if not k.startswith("header.")}
        done = complete_spec(bare)
        first = done["header"]["blocks"][0]
        assert first["anchor"] == "left"
        assert [e["field"] for e in first["elements"]] == [
            "full_name",
            "headline",
            "address",
            "phone",
            "email",
        ]
        assert done["styles"]["header.full_name"]["weight"] == 700

    def test_sans_destinataire_ni_date_un_bloc_droit_est_cree(self, spec: Json) -> None:
        bare = copy.deepcopy(spec)
        bare["header"]["blocks"] = [spec["header"]["blocks"][0]]
        bare["styles"] = {
            k: v
            for k, v in spec["styles"].items()
            if not k.startswith("recipient.") and k != "date_line"
        }
        done = complete_spec(bare)
        right = done["header"]["blocks"][1]
        assert right["anchor"] == "right"
        assert [e["field"] for e in right["elements"]] == ["company", "city", "date_line"]
        # La date après une ligne de blanc sous le destinataire.
        assert right["elements"][1]["pitch_mm"] == pytest.approx(
            2 * done["styles"]["paragraph"]["pitch_mm"], abs=0.01
        )


class TestAncragesEtMesuresDirectes:
    """Les règles de mesure, appelées une à une sur le constructeur du modèle de test."""

    @pytest.fixture
    def builder(self) -> LetterSpecBuilder:
        with PdfDocument(letter_template_pdf()) as doc:
            page = doc.read()
            b = LetterSpecBuilder(page, letter_labels_for(page.lines), cv_font_set(), SERVED)
            b.build()
            return b

    def test_l_interlettrage_ne_se_mesure_ni_sur_une_lettre_seule_ni_sans_police(
        self, builder: LetterSpecBuilder
    ) -> None:
        one = next(ln for ln in builder.lines if ln.role == "sender_contact")
        one.text = "x"
        assert builder.letter_spacing(one) is None
        other = next(ln for ln in builder.lines if ln.role == "greeting")
        other.raw = {**other.raw, "font": "Inconnue"}
        assert builder.letter_spacing(other) is None

    def test_sans_style_de_paragraphe_l_interligne_vaut_1_15(self) -> None:
        with PdfDocument(letter_template_pdf()) as doc:
            page = doc.read()
            fresh = LetterSpecBuilder(page, letter_labels_for(page.lines), cv_font_set(), SERVED)
        assert fresh.leading() == 1.15
        assert fresh.natural_pitch({"box_h_mm": 4.0, "pitch_mm": None}) == 4.6

    def test_une_ligne_seule_s_ancre_selon_ce_qu_elle_touche(
        self, builder: LetterSpecBuilder
    ) -> None:
        body_x, body_right = 20.0, 190.0
        assert builder.anchor_of([{"x": 150.0, "right": 190.3}], body_x, body_right)[0] == "right"
        assert builder.anchor_of([{"x": 20.4, "right": 60.0}], body_x, body_right)[0] == "left"
        centered = builder.anchor_of([{"x": 85.0, "right": 125.0}], body_x, body_right)
        assert centered == ("center", 105.0)
        far_right = builder.anchor_of([{"x": 160.0, "right": 180.0}], body_x, body_right)
        assert far_right == ("right", 30.0)
        assert builder.anchor_of([{"x": 40.0, "right": 80.0}], body_x, body_right)[0] == "left"

    def test_un_bloc_centre_prend_son_axe_et_la_largeur_de_la_colonne(
        self, builder: LetterSpecBuilder
    ) -> None:
        elems = [
            {
                "field": "company",
                "style": "recipient.company",
                "x": 85.0,
                "y": 60.0,
                "w": 40.0,
                "h": 4.5,
                "right": 125.0,
            },
            {
                "field": "city",
                "style": "recipient.city",
                "x": 95.0,
                "y": 65.0,
                "w": 20.0,
                "h": 4.5,
                "right": 115.0,
            },
        ]
        [block] = builder.header_blocks(elems, 20.0, 190.0)
        assert block["anchor"] == "center"
        assert block["cx_mm"] == 105.0
        assert block["max_w_mm"] == 170.0

    def test_le_fond_de_page_et_les_formes_sans_encre_ne_sont_pas_des_decorations(
        self, builder: LetterSpecBuilder
    ) -> None:
        builder.page.drawings = [
            {"kind": "rect", "x": 0, "y": 0, "w": 210, "h": 297, "fill": "#FFFFFF", "stroke": None},
            {"kind": "rect", "x": 10, "y": 10, "w": 5, "h": 5, "fill": None, "stroke": None},
            {
                "kind": "hline",
                "x": 10,
                "y": 50,
                "w": 100,
                "h": 0,
                "fill": None,
                "stroke": "#000000",
            },
        ]
        [rule] = builder.decorations()
        assert rule["kind"] == "hline"

    def test_un_bloc_seul_sur_une_ligne_n_est_pas_justifie(
        self, builder: LetterSpecBuilder
    ) -> None:
        line = next(ln for ln in builder.lines if ln.role == "greeting")
        assert builder.alignment([line], 190.0) == "left"


class TestVariantesDEtiquetage:
    """Ce que le modèle de vision peut omettre ou déplacer, et ce qu'en fait la spécification."""

    def _build(self, transform: Any) -> tuple[Json, Json]:
        with PdfDocument(letter_template_pdf()) as doc:
            page = doc.read()
            labels = letter_labels_for(page.lines)
            labels["lines"] = [lb for lb in (transform(lb, page) for lb in labels["lines"]) if lb]
            return LetterSpecBuilder(page, labels, cv_font_set(), SERVED).build()

    @staticmethod
    def _text(page: Any, line_id: str) -> str:
        return next(ln["text"] for ln in page.lines if ln["id"] == line_id)

    def test_une_seconde_adresse_est_ignoree_et_les_coordonnees_restent_uniques(self) -> None:
        def relabel(lb: Json, page: Any) -> Json:
            if self._text(page, lb["line"]) == "Vendeuse":
                return {**lb, "role": "sender_contact"}
            return lb

        spec, sample = self._build(relabel)
        fields = [e["field"] for e in spec["header"]["blocks"][0]["elements"]]
        assert fields.count("address") == 1
        assert sample["sender"]["city"] == "Vendeuse"
        assert "headline" in fields, "l'intitulé reçoit malgré tout un emplacement"

    def test_sans_destinataire_la_date_seule_s_ancre_a_droite_et_le_destinataire_est_ajoute(
        self,
    ) -> None:
        spec, sample = self._build(
            lambda lb, page: None if lb["role"] in ("recipient_name", "recipient_address") else lb
        )
        right = next(b for b in spec["header"]["blocks"] if b["anchor"] == "right")
        assert [e["field"] for e in right["elements"]] == ["company", "city", "date_line"]
        assert sample["recipient"] == {"company": "", "city": ""}

    def test_une_signature_a_droite_garde_son_alignement(self) -> None:
        def relabel(lb: Json, page: Any) -> Json | None:
            if lb["role"] == "date_line":
                return {**lb, "role": "signature"}
            if lb["role"] == "signature":
                return None
            return lb

        spec, _ = self._build(relabel)
        signature = next(b for b in spec["body"]["blocks"] if b["role"] == "signature")
        assert signature["align"] == "right"
        assert "date_line" in {e["field"] for b in spec["header"]["blocks"] for e in b["elements"]}

    def test_sans_nom_les_coordonnees_portent_la_pile_de_l_expediteur(self) -> None:
        spec, sample = self._build(
            lambda lb, page: None if lb["role"] in ("sender_name", "sender_headline") else lb
        )
        fields = [e["field"] for e in spec["header"]["blocks"][0]["elements"]]
        assert fields[0] == "full_name"
        assert "phone" in fields
        assert sample["sender"].get("first_name") is None
