"""Modèles de CV : formats d'export, verrou Pro, noms, et traits mesurés pour la galerie."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from karria.modules.documents.domain.errors import InvalidFormatError, TemplateLockedError
from karria.modules.documents.domain.templates import (
    ExplanationStatus,
    Fit,
    GeneratedCv,
    Template,
    TemplateVersion,
    VersionStatus,
    ats_safe_helps,
    check_formats,
    ensure_usable,
    export_file_name,
    gallery_traits,
    slugify,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 24, tzinfo=UTC)


def _template(*, premium: bool = False, version_id: UUID | None = None) -> Template:
    return Template(
        id=uuid4(),
        name="meridian",
        label="Meridian",
        category="classique",
        description="",
        tags=(),
        is_premium=premium,
        price=None,
        primary_color=None,
        font_family=None,
        has_photo=False,
        columns=1,
        published_version_id=version_id,
        published_version=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _style(family: str = "Lato", spacing: float = 0.0, size: float = 10, **extra: Any) -> dict:
    return {"family": family, "letter_spacing_pt": spacing, "size_pt": size, **extra}


def _spec(
    styles: dict[str, Any] | None = None,
    columns: int = 1,
    photo: dict[str, Any] | None = None,
    elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "styles": styles if styles is not None else {"body": _style()},
        "bands": [{"columns": [{"sections": []} for _ in range(columns)]}],
        "header": {"elements": elements or [], "photo": photo},
    }


class TestFormats:
    @pytest.mark.parametrize("formats", [None, []])
    def test_sans_demande_les_deux_formats_sont_produits(self, formats: list[str] | None) -> None:
        assert check_formats(formats) == ("pdf", "docx")

    def test_les_formats_sont_remis_dans_l_ordre_canonique(self) -> None:
        assert check_formats(["docx", "pdf", "docx"]) == ("pdf", "docx")

    def test_un_seul_format_demande_est_garde(self) -> None:
        assert check_formats(["docx"]) == ("docx",)

    def test_un_format_inconnu_est_refuse_avec_la_liste_des_inconnus(self) -> None:
        with pytest.raises(InvalidFormatError) as exc:
            check_formats(["pdf", "odt", "rtf"])
        assert exc.value.details["formats"] == ["odt", "rtf"]


class TestVerrouPro:
    def test_un_modele_gratuit_se_rend_pour_tout_le_monde(self) -> None:
        ensure_usable(_template(), owned=False)

    def test_un_modele_pro_possede_se_rend(self) -> None:
        ensure_usable(_template(premium=True), owned=True)

    def test_un_modele_pro_non_possede_est_verrouille(self) -> None:
        with pytest.raises(TemplateLockedError):
            ensure_usable(_template(premium=True), owned=False)


class TestNoms:
    def test_le_nom_technique_est_en_minuscules_sans_accents_ni_espaces(self) -> None:
        assert slugify("  Méridian Pro — Édition #2 ") == "meridian-pro-edition-2"

    def test_un_nom_sans_lettre_latine_donne_modele(self) -> None:
        assert slugify("★★★") == "modele"

    def test_le_fichier_exporte_porte_le_prenom_et_le_nom(self) -> None:
        assert export_file_name(" Awa ", " Diallo ", "pdf") == "CV - Awa Diallo.pdf"

    def test_le_fichier_exporte_s_accommode_d_un_seul_nom(self) -> None:
        assert export_file_name("", "Diallo", "docx") == "CV - Diallo.docx"

    def test_sans_nom_le_fichier_s_appelle_cv(self) -> None:
        assert export_file_name("  ", "", "pdf") == "CV.pdf"


class TestTraitsDeGalerie:
    def test_un_dessin_sobre_est_pret_pour_les_ats(self) -> None:
        traits = gallery_traits(
            _spec({"section_title": _style(color="#1A73E8"), "body": _style("Lato")})
        )
        assert traits == {
            "primary_color": "#1A73E8",
            "font_family": "Lato",
            "has_photo": False,
            "columns": 1,
            "ats": {"readiness": "good", "warnings": []},
            "fields_added": [],
        }

    def test_la_famille_principale_est_la_plus_employee(self) -> None:
        styles = {"a": _style("Lato"), "b": _style("Roboto"), "c": _style("Roboto")}
        assert gallery_traits(_spec(styles))["font_family"] == "Roboto"

    def test_sans_styles_ni_titre_de_rubrique_rien_n_est_mesure(self) -> None:
        traits = gallery_traits(_spec({}))
        assert traits["font_family"] is None
        assert traits["primary_color"] is None

    def test_sans_bande_le_modele_a_une_colonne(self) -> None:
        spec = {**_spec(), "bands": []}
        assert gallery_traits(spec)["columns"] == 1

    def test_un_titre_espace_au_dela_de_0_1_em_est_signale(self) -> None:
        traits = gallery_traits(_spec({"section_title": _style(spacing=1.2, size=10)}))
        assert traits["ats"] == {"readiness": "warning", "warnings": ["spaced_titles"]}

    def test_un_interlettrage_de_0_1_em_tout_juste_n_est_pas_signale(self) -> None:
        traits = gallery_traits(_spec({"section_title": _style(spacing=1.0, size=10)}))
        assert traits["ats"]["warnings"] == []

    def test_un_style_synthetise_par_karria_n_est_pas_reproche_au_dessin(self) -> None:
        traits = gallery_traits(_spec({"headline": _style(spacing=3.0, synthesized=True)}))
        assert traits["ats"]["warnings"] == []

    def test_photo_et_colonnes_sont_signalees(self) -> None:
        traits = gallery_traits(_spec(columns=2, photo={"x_mm": 10}))
        assert traits["has_photo"] is True
        assert traits["columns"] == 2
        assert traits["ats"]["warnings"] == ["photo", "multi_column"]

    def test_un_poste_vise_synthetise_est_declare_comme_champ_ajoute(self) -> None:
        elements = [{"role": "full_name"}, {"role": "headline", "synthesized": True}]
        assert gallery_traits(_spec(elements=elements))["fields_added"] == ["headline"]

    def test_la_variante_ats_aide_seulement_si_des_titres_sont_espaces(self) -> None:
        assert ats_safe_helps(_spec({"t": _style(spacing=2.0)})) is True
        assert ats_safe_helps(_spec(columns=2, photo={"x": 1})) is False


class TestEntites:
    def test_un_modele_est_publie_quand_il_pointe_une_version(self) -> None:
        assert _template(version_id=uuid4()).is_published is True
        assert _template().is_published is False

    def test_changer_d_etat_rend_une_nouvelle_version_sans_toucher_l_ancienne(self) -> None:
        version = TemplateVersion(
            id=uuid4(),
            template_id=uuid4(),
            version=1,
            status=VersionStatus.PROCESSING,
            source_key="k",
            created_at=NOW,
        )
        published = version.with_status(VersionStatus.PUBLISHED, published_at=NOW)
        assert published.status is VersionStatus.PUBLISHED
        assert published.published_at == NOW
        assert version.status is VersionStatus.PROCESSING
        assert version.report == {}
        assert version.fonts == ()

    def test_la_mise_en_page_par_defaut_tient_sur_une_page_sans_reduction(self) -> None:
        assert Fit().to_json() == {"spacing": 1.0, "font_scale": 1.0, "pages": 1}

    def test_un_export_neuf_attend_son_explication(self) -> None:
        cv = GeneratedCv(
            id=GeneratedCv.new_id(),
            user_id=uuid4(),
            template_id=uuid4(),
            template_version_id=uuid4(),
            template_label="Meridian",
            template_version=1,
            ats_safe=False,
            created_at=NOW,
        )
        assert isinstance(cv.id, UUID)
        assert cv.explanation_status is ExplanationStatus.PENDING
        assert cv.fit == Fit()
        assert GeneratedCv.new_id() != GeneratedCv.new_id()
